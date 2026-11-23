"""Перевод SQL запросов и представлений Oracle, T-SQL и Teradata на диалект цели (ADB 7.5 / 6.x, ClickHouse).

Разбор и вывод — sqlglot; поверх него правила, которых в sqlglot нет или которые зависят от модели:

* имена таблиц источника -> имена в целевой базе (SALES.ORDERS -> dbt_sales.orders), имена в нижнем регистре;
* Oracle: внешние соединения (+), CONNECT BY -> WITH RECURSIVE, ROWNUM -> LIMIT, FROM DUAL, ADD_MONTHS,
  MONTHS_BETWEEN, TO_NUMBER, деление целых, «||» с NULL как пустой строкой, DATE + число;
* T-SQL: сложение строк «+» -> «||», datetime + число; TOP, ISNULL, DATEADD, CROSS APPLY — средствами sqlglot;
* Teradata: SEL, QUALIFY, ZEROIFNULL, NULLIFZERO, INDEX, OREPLACE, CAST … FORMAT, DATE, ADD_MONTHS.

Всё, что перевести не удалось, не теряется: остаётся в тексте как было и попадает в список todo
(неизвестная функция, таблица не из модели, оставшийся CONNECT BY, SAMPLE и т. п.). Объект с todo
считается переведённым частично и в пакет для развёртывания не попадает, только в черновики.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp, transforms
from sqlglot.errors import ErrorLevel, ParseError, TokenError

from dbtransfer.generator.sql import RESERVED
from dbtransfer.model import SourceKind
from dbtransfer.translate.functions import is_known_function
from dbtransfer.translate.names import DATE, INTEGER, NUMBER, STRING, TIME, TIMESTAMP, NameMap, Relation

# sqlglot пишет в журнал «Falling back to parsing as a Command»; это и так попадает в todo
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# Источник модели -> диалект sqlglot
SOURCE_DIALECTS = {SourceKind.ORACLE: "oracle", SourceKind.MSSQL: "tsql", SourceKind.TERADATA: "teradata",
                   SourceKind.GREENPLUM: "postgres"}
SOURCE_NAMES = {"oracle": "Oracle", "tsql": "T-SQL", "teradata": "Teradata", "postgres": "PostgreSQL"}

AUTO, PARTIAL, FAILED = "auto", "partial", "failed"
STATUS_TEXT = {AUTO: "переведено автоматически", PARTIAL: "черновик, нужна доработка", FAILED: "не разобрано"}

_SAFE_NAME = re.compile(r"^[a-z_][a-z0-9_$]*$")
_PSEUDO = {"oracle": {"ROWNUM", "ROWID", "LEVEL", "SYSDATE", "CONNECT_BY_ISLEAF", "CONNECT_BY_ISCYCLE"},
           "teradata": set(), "tsql": set()}


def target_sqlglot(dialect: str) -> str:
    return "clickhouse" if dialect == "clickhouse" else "postgres"


@dataclass
class QueryResult:
    sql: str = ""
    todos: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # что переведено правилами (для отчёта)
    references: list[Relation] = field(default_factory=list)
    error: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return FAILED
        return PARTIAL if self.todos else AUTO


def _uniq(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _tpl(sql: str, dialect: str = "postgres", **args: exp.Expression) -> exp.Expression:
    """Выражение по шаблону в синтаксисе цели: имена _a, _b заменяются на переданные выражения."""
    tree = sqlglot.parse_one(sql, read=dialect)

    def repl(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table and node.name in args:
            return args[node.name].copy()
        return node

    return tree.transform(repl)


def _rewrite(tree: exp.Expression, fn) -> exp.Expression:
    """Замена узлов снизу вверх: потомки переводятся раньше родителя, и шаблон родителя берёт уже
    переведённых потомков (sqlglot transform идёт сверху и не заходит в замену)."""
    nodes = list(tree.walk(bfs=False))
    for node in reversed(nodes):
        new = fn(node)
        if new is not node:
            if node is tree:
                tree = new
            else:
                node.replace(new)
    return tree


def _num(e: exp.Expression | None) -> int | None:
    if isinstance(e, exp.Literal) and not e.is_string and e.this.isdigit():
        return int(e.this)
    return None


class _Translator:
    def __init__(self, source: str, dialect: str, names: NameMap | None, default_schema: str, pretty: bool = True):
        self.pretty = pretty
        self.source = source
        self.dialect = dialect
        self.target = target_sqlglot(dialect)
        self.names = names or NameMap()
        self.default_schema = default_schema
        self.result = QueryResult()
        self.aliases: dict[str, Relation] = {}  # псевдоним или имя таблицы в запросе -> отношение
        self.known_functions: set[str] = set()  # функции модели в целевой базе (схема.имя)

    def todo(self, text: str) -> None:
        self.result.todos.append(text)

    def note(self, text: str) -> None:
        self.result.notes.append(text)

    # --- общий ход ---
    def run(self, text: str) -> QueryResult:
        try:
            trees = [t for t in sqlglot.parse(text, read=self.source) if t is not None]
        except (ParseError, TokenError) as e:
            self.result.error = f"SQL {SOURCE_NAMES[self.source]} не разобран: {_short(e)}"
            return self.result
        if len(trees) != 1:
            self.result.error = f"ожидался один запрос, а их {len(trees)}"
            return self.result
        tree = trees[0]
        if isinstance(tree, exp.Command):
            self.result.error = f"конструкция не разобрана: {tree.sql()[:80]}"
            return self.result
        tree = self.translate_tree(tree)
        if isinstance(tree, exp.Merge) and self.target != "clickhouse":
            parts = [self.generate(t) for t in self.merge_statements(tree)]
            self.result.sql = ";\n".join(parts)
            for part in parts:
                self.check_output(part)
        else:
            self.result.sql = self.generate(tree)
            self.check_output(self.result.sql)
        self.result.todos = _uniq(self.result.todos)
        self.result.notes = _uniq(self.result.notes)
        return self.result

    def translate_tree(self, tree: exp.Expression) -> exp.Expression:
        if tree.find(exp.Qualify) and self.target != "clickhouse":
            self.note("QUALIFY переведён в подзапрос с оконной функцией")
        if self.source == "oracle":
            tree = self.oracle(tree)
        elif self.source == "teradata":
            tree = self.teradata(tree)
        elif self.source == "tsql":
            tree = self.tsql(tree)
        for i, sub in enumerate(tree.find_all(exp.Subquery), start=1):
            if not sub.alias and isinstance(sub.parent, (exp.From, exp.Join)):
                sub.set("alias", exp.TableAlias(this=exp.to_identifier(f"_s{i}")))
        tree = self.rename_tables(tree)
        tree = self.normalize_identifiers(tree)
        if self.source in ("oracle", "tsql", "teradata"):
            tree = _rewrite(tree, self.typed)
        return tree

    def tpl(self, sql: str, **args: exp.Expression) -> exp.Expression:
        return _tpl(sql, self.target, **args)

    def generate(self, tree: exp.Expression) -> str:
        dialect = sqlglot.Dialect.get_or_raise(self.target)
        gen = dialect.generator(unsupported_level=ErrorLevel.IGNORE, pretty=self.pretty)
        sql = gen.generate(tree)
        for msg in gen.unsupported_messages:
            self.todo(f"не поддерживается в {self.dialect}: {msg}")
        return sql

    # --- имена ---
    def rename_tables(self, tree: exp.Expression) -> exp.Expression:
        ctes = {c.alias_or_name.upper() for c in tree.find_all(exp.CTE)}
        for table in list(tree.find_all(exp.Table)):
            name = table.name
            if not name or name.upper() in ctes and not table.db:
                continue
            if isinstance(table.this, exp.Func):  # табличная функция
                continue
            rel = self.names.resolve(table.db or None, name, self.default_schema)
            source_name = ".".join(p for p in (table.db, name) if p)
            if rel is None:
                if name.upper() != "DUAL":
                    self.todo(f"таблица или представление {source_name} нет в модели: имя оставлено как есть")
                continue
            if rel not in self.result.references:
                self.result.references.append(rel)
            new = exp.to_table(rel.target.replace('"', ""), dialect="postgres")
            alias = table.args.get("alias")
            for part in ("this", "db"):
                ident = new.args.get(part)
                if ident is not None:
                    ident.set("quoted", True)  # имя уже в нижнем регистре целевой базы
            new.set("alias", alias)
            for k in ("joins", "laterals", "pivots", "hints", "sample"):
                if table.args.get(k):
                    new.set(k, table.args[k])
            table.replace(new)
            self.aliases[(alias.name if alias else name).lower()] = rel
            self.aliases.setdefault(name.lower(), rel)
        # Пользовательские функции, переведённые вместе с моделью: F(x), SCHEMA.F(x) -> имя в целевой базе
        if self.names.functions:
            for f in list(tree.find_all(exp.Anonymous)):
                parent = f.parent
                qualified_call = isinstance(parent, exp.Dot) and parent.expression is f
                schema = parent.this.name if qualified_call else None
                target = self.names.resolve_function(schema, f.name, self.default_schema)
                if target is None:
                    continue
                self.known_functions.add(target.replace('"', "").lower())
                # Схема.функция: квалифицированные имена генератор не переводит в верхний регистр
                schema_name, _, func = target.replace('"', "").rpartition(".")
                new = exp.Anonymous(this=func, expressions=f.expressions)
                if schema_name:
                    new = exp.Dot(this=exp.to_identifier(schema_name), expression=new)
                (parent if qualified_call else f).replace(new)
        # Колонки вида СХЕМА.ТАБЛИЦА.КОЛОНКА: схема в целевой базе другая, оставляем ТАБЛИЦА.КОЛОНКА
        for col in tree.find_all(exp.Column):
            if col.args.get("db"):
                col.set("db", None)
                col.set("catalog", None)
        return tree

    def normalize_identifiers(self, tree: exp.Expression) -> exp.Expression:
        for ident in tree.find_all(exp.Identifier):
            name = ident.this
            if not isinstance(name, str):
                continue
            if ident.quoted and name == name.lower() and isinstance(ident.parent, exp.Table):
                ident.set("quoted", not _SAFE_NAME.match(name) or name in RESERVED)
                continue
            low = name.lower()
            ident.set("this", low)
            ident.set("quoted", not _SAFE_NAME.match(low) or low in RESERVED)
        return tree

    # --- типы ---
    def category(self, e: exp.Expression | None) -> str:
        if e is None:
            return ""
        if isinstance(e, exp.Paren):
            return self.category(e.this)
        if isinstance(e, exp.Column):
            name = e.name.lower()
            if e.table:
                rel = self.aliases.get(e.table.lower())
                return rel.columns.get(name, "") if rel else ""
            cats = {r.columns[name] for r in self.result.references if name in r.columns}
            return cats.pop() if len(cats) == 1 else ""
        if isinstance(e, exp.Literal):
            if e.is_string:
                return STRING
            return INTEGER if e.this.isdigit() else NUMBER
        if isinstance(e, exp.Neg):
            return self.category(e.this)
        if isinstance(e, (exp.CurrentTimestamp, exp.Systimestamp, exp.DateTrunc, exp.TimestampTrunc,
                          exp.StrToTime, exp.CurrentDatetime)):
            return TIMESTAMP
        if isinstance(e, (exp.CurrentDate, exp.StrToDate, exp.LastDay, exp.DateStrToDate, exp.TsOrDsToDate)):
            return DATE
        if isinstance(e, (exp.Cast, exp.TryCast)):
            to = e.to
            if to.is_type(*exp.DataType.TEMPORAL_TYPES):
                return DATE if to.is_type("date") else TIME if to.is_type("time") else TIMESTAMP
            if to.is_type(*exp.DataType.TEXT_TYPES):
                return STRING
            if to.is_type(*exp.DataType.INTEGER_TYPES):
                return INTEGER
            if to.is_type(*exp.DataType.REAL_TYPES):
                return NUMBER
            return ""
        if isinstance(e, (exp.DateAdd, exp.DateSub)):
            return self.category(e.this)
        if isinstance(e, (exp.DPipe, exp.Concat, exp.Upper, exp.Lower, exp.Substring, exp.Trim, exp.ToChar,
                          exp.Initcap, exp.Pad)):
            return STRING
        if isinstance(e, (exp.Coalesce, exp.Nullif)):
            return self.category(e.this)
        if isinstance(e, exp.Case):
            ifs = e.args.get("ifs") or []
            return self.category(ifs[0].args.get("true")) if ifs else self.category(e.args.get("default"))
        if isinstance(e, (exp.Count, exp.Length)):
            return INTEGER
        if isinstance(e, (exp.Sum, exp.Avg, exp.Round, exp.Div)):
            return NUMBER
        if isinstance(e, (exp.Add, exp.Sub, exp.Mul)):
            a, b = self.category(e.this), self.category(e.expression)
            if TIMESTAMP in (a, b) or DATE in (a, b):
                return a if a in (DATE, TIMESTAMP) else b
            if STRING in (a, b):
                return STRING
            return INTEGER if a == b == INTEGER else NUMBER if a and b else ""
        return ""

    def typed(self, node: exp.Expression) -> exp.Expression:
        """Правила, которым нужны типы колонок: арифметика дат, деление целых, «||», ADD_MONTHS."""
        if isinstance(node, (exp.Add, exp.Sub)):
            return self.arithmetic(node)
        if self.source == "oracle" and isinstance(node, exp.DPipe) and self.target != "clickhouse":
            # В Oracle NULL в «||» — пустая строка, в PostgreSQL результат NULL; concat() пропускает NULL
            parts = []
            for p in (node.this, node.expression):
                parts.extend(p.expressions if isinstance(p, exp.Anonymous) and p.name.upper() == "CONCAT"
                             else [p])
            self.note("«||» переведено в concat(): NULL как пустая строка, как в Oracle")
            return exp.Anonymous(this="CONCAT", expressions=parts)
        if self.source == "oracle" and isinstance(node, exp.Div) and not node.args.get("typed"):
            # В Oracle 7/2 = 3.5, в PostgreSQL целые делятся нацело. sqlglot приводил бы делимое к double
            # precision и терял точность numeric, поэтому целые приводятся к numeric, остальное — как есть
            node.set("typed", True)
            if self.target != "clickhouse" and self.category(node.this) in (INTEGER, "") \
                    and self.category(node.expression) in (INTEGER, "") and not isinstance(node.this, exp.Cast):
                node.set("this", exp.Cast(this=node.this, to=exp.DataType.build("numeric")))
            return node
        if (self.source in ("oracle", "teradata") and isinstance(node, exp.Anonymous) and node.name.upper() == "TRUNC"
                and len(node.expressions) == 1 and self.category(node.expressions[0]) in (DATE, TIMESTAMP)):
            if self.target == "clickhouse":
                return self.tpl("toStartOfDay(_d)", _d=node.expressions[0])
            return self.tpl("DATE_TRUNC('day', _d)", _d=node.expressions[0])
        if isinstance(node, exp.AddMonths):
            self.note("ADD_MONTHS переведён через INTERVAL: последний день месяца не переносится на последний")
            when, n = node.this, node.expression
            if self.target == "clickhouse":
                return self.tpl("addMonths(_d, _n)", _d=when, _n=n)
            result = self.tpl("_d + (_n) * INTERVAL '1 month'", _d=when, _n=n)
            if self.category(when) == DATE:
                return exp.Cast(this=result, to=exp.DataType.build("date"))
            return result
        return node

    def arithmetic(self, node: exp.Expression) -> exp.Expression:
        """Даты и строки: DATE + 1 (Oracle, T-SQL), разность дат в днях (Oracle), «+» строк (T-SQL)."""
        a, b = node.this, node.expression
        ca, cb = self.category(a), self.category(b)
        numeric = (INTEGER, NUMBER)
        if self.source == "tsql" and isinstance(node, exp.Add) and STRING in (ca, cb):
            self.note("сложение строк «+» T-SQL переведено в «||»")
            return exp.DPipe(this=a, expression=b)
        if ca == TIMESTAMP and cb in numeric or (isinstance(node, exp.Add) and cb == TIMESTAMP and ca in numeric):
            when, days = (a, b) if ca == TIMESTAMP else (b, a)
            self.note("дата + число дней переведено через INTERVAL")
            if self.target == "clickhouse":
                return self.tpl("_d + toIntervalSecond(round((_n) * 86400))" if isinstance(node, exp.Add)
                                else "_d - toIntervalSecond(round((_n) * 86400))", _d=when, _n=days)
            sign = "+" if isinstance(node, exp.Add) else "-"
            return _tpl(f"_d {sign} (_n) * INTERVAL '1 day'", _d=when, _n=days)
        if (isinstance(node, exp.Sub) and self.source == "oracle" and ca in (DATE, TIMESTAMP)
                and cb in (DATE, TIMESTAMP) and TIMESTAMP in (ca, cb)):
            self.note("разность дат Oracle (дни с дробной частью) переведена через EXTRACT(EPOCH …) / 86400")
            if self.target == "clickhouse":
                return self.tpl("dateDiff('second', _b, _a) / 86400", _a=a, _b=b)
            return _tpl("EXTRACT(EPOCH FROM (_a - _b)) / 86400", _a=a, _b=b)
        return node

    # --- MERGE ---
    def merge_statements(self, m: exp.Merge) -> list[exp.Expression]:
        """MERGE -> UPDATE … FROM, DELETE … USING и INSERT … WHERE NOT EXISTS: в Greenplum 6 и 7 (PostgreSQL 9.4
        и 12) MERGE нет. Порядок как у MERGE: сначала изменяются найденные строки, потом добавляются новые."""
        target = m.this
        alias = target.alias_or_name
        bare = target.copy()
        bare.set("alias", None)
        using, on = m.args["using"], m.args["on"]
        on = on.this if isinstance(on, exp.Paren) else on
        updates, deletes, inserts = [], [], []
        for when in m.args["whens"].expressions:
            then, cond = when.args.get("then"), when.args.get("condition")
            matched, by_source = when.args.get("matched"), when.args.get("source")
            if matched and isinstance(then, exp.Update):
                extra = [c for c in (cond, then.args["where"].this if then.args.get("where") else None) if c]
                sets = [exp.EQ(this=exp.column(e.this.name), expression=e.expression) for e in then.expressions]
                updates.append(exp.Update(this=target.copy(), expressions=sets, from_=exp.From(this=using.copy()),
                                          where=exp.Where(this=exp.and_(on.copy(), *[c.copy() for c in extra]))))
            elif matched and _is_delete(then):
                deletes.append(exp.Delete(this=target.copy(), using=[using.copy()],
                                          where=exp.Where(this=exp.and_(on.copy(), *([cond.copy()] if cond else [])))))
            elif not matched and by_source and _is_delete(then):
                src = using.copy()
                exists = exp.Exists(this=exp.select("1").from_(src).where(on.copy()))
                deletes.append(exp.Delete(this=target.copy(), where=exp.Where(
                    this=exp.and_(exp.Not(this=exists), *([cond.copy()] if cond else [])))))
            elif not matched and isinstance(then, exp.Insert):
                values = then.expression.expressions if isinstance(then.expression, exp.Tuple) else [then.expression]
                exists = exp.Exists(this=exp.select("1").from_(target.copy()).where(on.copy()))
                extra = [c for c in (cond, then.args["where"].this if then.args.get("where") else None) if c]
                query = exp.select(*[v.copy() for v in values]).from_(using.copy()).where(
                    exp.and_(exp.Not(this=exists), *[c.copy() for c in extra]))
                inserts.append(exp.Insert(this=exp.Schema(this=bare.copy(), expressions=[
                    exp.to_identifier(c.name) for c in then.this.expressions]) if then.this else bare.copy(),
                    expression=query))
            else:
                self.todo(f"ветка MERGE {when.sql(self.target)[:60]} не переведена")
        self.note(f"MERGE в {alias} переведён в UPDATE / DELETE / INSERT: в Greenplum нет MERGE; при повторах "
                  "ключа в источнике MERGE падает, а UPDATE … FROM берёт любую строку")
        return [*updates, *deletes, *inserts]

    # --- Oracle ---
    def oracle(self, tree: exp.Expression) -> exp.Expression:
        if any(c.args.get("join_mark") for c in tree.find_all(exp.Column)):
            try:
                tree = transforms.eliminate_join_marks(tree)
                self.note("внешние соединения (+) переведены в LEFT JOIN")
            except Exception as e:  # noqa: BLE001 — sqlglot не переводит сложные случаи (+)
                self.todo(f"внешнее соединение (+) не переведено: {_short(e)}")
        for select in list(tree.find_all(exp.Select)):
            if select.args.get("connect"):
                self.connect_by(select)
        for select in list(tree.find_all(exp.Select)):
            self.rownum_limit(select)
            frm = select.args.get("from_")
            if (frm is not None and isinstance(frm.this, exp.Table) and frm.this.name.upper() == "DUAL"
                    and not frm.this.db and not select.args.get("joins")):
                select.set("from_", None)
        return _rewrite(tree, self.oracle_node)

    def oracle_node(self, node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table:
            name = node.name.upper()
            if name == "ROWNUM":
                self.note("ROWNUM в списке колонок переведён в row_number() OVER () — порядок строк не задан")
                return _tpl("ROW_NUMBER() OVER ()")
            if name == "USER" and not node.this.quoted:
                return exp.CurrentUser()
        if isinstance(node, exp.CurrentTimestamp) and node.args.get("sysdate"):
            # SYSDATE — дата и время без долей секунды, как Oracle DATE -> timestamp(0)
            if self.target == "clickhouse":
                return self.tpl("now()")
            return self.tpl("CAST(LOCALTIMESTAMP AS TIMESTAMP(0))")
        if isinstance(node, exp.Systimestamp):
            return exp.CurrentTimestamp()
        if isinstance(node, exp.ToNumber) and node.args.get("format") is None:
            return exp.Cast(this=node.this, to=exp.DataType.build("numeric"))
        if isinstance(node, exp.StrToDate) and isinstance(node.args.get("format"), exp.Literal):
            fmt = node.args["format"].this
            if re.search(r"%[HIMSf]", fmt):  # в формате есть время: Oracle DATE его хранит
                return exp.StrToTime(this=node.this, format=node.args["format"])
        return self.common_functions(node)

    def rownum_limit(self, select: exp.Select) -> None:
        where = select.args.get("where")
        if where is None:
            return
        conjuncts = list(where.this.flatten()) if isinstance(where.this, exp.And) else [where.this]
        rest, limit = [], None
        for c in conjuncts:
            n = None
            if isinstance(c, (exp.LTE, exp.LT, exp.EQ)) and _is_rownum(c.this):
                n = _num(c.expression)
                if n is not None and isinstance(c, exp.LT):
                    n -= 1
                if isinstance(c, exp.EQ) and n != 1:
                    n = None
            elif isinstance(c, (exp.GTE, exp.GT)) and _is_rownum(c.expression):
                n = _num(c.this)
                if n is not None and isinstance(c, exp.GT):
                    n -= 1
            if n is None:
                rest.append(c)
            else:
                limit = n if limit is None else min(limit, n)
        if limit is None:
            if any(_is_rownum(c) for c in where.find_all(exp.Column)):
                self.todo("ROWNUM в условии не переведён: перепишите через row_number() OVER (…)")
            return
        if select.args.get("order") or select.args.get("group"):
            self.todo("ROWNUM вместе с ORDER BY или GROUP BY на том же уровне: в Oracle он считается до "
                      "сортировки, LIMIT — после; перепишите запрос")
            return
        select.set("where", exp.Where(this=exp.and_(*rest)) if rest else None)
        frm = select.args.get("from_")
        inner = frm.this if frm is not None else None
        if (isinstance(inner, exp.Subquery) and isinstance(inner.this, exp.Select) and inner.this.args.get("order")
                and not rest and not select.args.get("joins")):
            # Классика Oracle «SELECT * FROM (… ORDER BY …) WHERE ROWNUM <= n»: в Greenplum порядок подзапроса
            # теряется при передаче между сегментами, поэтому LIMIT ставится в подзапрос рядом с ORDER BY
            inner.this.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
        else:
            if isinstance(inner, exp.Subquery) and isinstance(inner.this, exp.Select) and inner.this.args.get("order"):
                self.todo("ROWNUM над отсортированным подзапросом с дополнительным условием: проверьте, что "
                          "сортировка и LIMIT стоят на одном уровне")
            select.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
        self.note("ROWNUM <= n переведён в LIMIT n")

    def connect_by(self, select: exp.Select) -> None:
        """CONNECT BY -> WITH RECURSIVE для запроса к одной таблице."""
        connect = select.args["connect"]
        frm = select.args.get("from_")
        if frm is None or not isinstance(frm.this, exp.Table) or select.args.get("joins"):
            self.todo("CONNECT BY с соединением таблиц не переведён: перепишите через WITH RECURSIVE")
            return
        table = frm.this
        alias = table.alias_or_name
        own = {alias.upper(), table.name.upper()}

        def qualify(e: exp.Expression, tbl: str, level: exp.Expression) -> exp.Expression:
            def repl(node):
                if isinstance(node, exp.Prior):
                    return qualify(node.this, "_p", level)
                if isinstance(node, exp.Column) and (not node.table or node.table.upper() in own):
                    if node.name.upper() == "LEVEL" and not node.table:
                        return level.copy()
                    return exp.column(node.name, table=tbl)
                return node
            return e.copy().transform(repl)

        one, next_level = exp.Literal.number(1), _tpl("_p._level + 1")
        start = connect.args.get("start")
        cond = connect.args.get("connect")
        extra_anchor, extra_rec, outer_repl = [], [], {}
        for i, f in enumerate(list(select.find_all(exp.Anonymous, exp.ConnectByRoot)), start=1):
            if isinstance(f, exp.ConnectByRoot):
                col = f"_root{i}"
                extra_anchor.append(exp.alias_(qualify(f.this, "_c", one), col))
                extra_rec.append(exp.column(col, table="_p"))
                outer_repl[id(f)] = exp.column(col)
            elif f.name.upper() == "SYS_CONNECT_BY_PATH" and len(f.expressions) == 2:
                col = f"_path{i}"
                what, sep = f.expressions
                extra_anchor.append(exp.alias_(_tpl("CAST(CONCAT(_s, _w) AS TEXT)", _s=sep,
                                                    _w=qualify(what, "_c", one)), col))
                extra_rec.append(_tpl("CAST(CONCAT(_x, _s, _w) AS TEXT)", _x=exp.column(col, table="_p"), _s=sep,
                                      _w=qualify(what, "_c", one)))
                outer_repl[id(f)] = exp.column(col)
        bare = table.copy()
        bare.set("alias", None)
        anchor = exp.select(exp.column(exp.Star(), table="_c"),
                            exp.alias_(exp.Literal.number(1), "_level"), *extra_anchor) \
            .from_(exp.alias_(bare.copy(), "_c", table=True))
        if start is not None:
            anchor = anchor.where(qualify(start, "_c", one))
        rec_cond = qualify(cond, "_c", next_level)
        recursive = exp.select(exp.column(exp.Star(), table="_c"), _tpl("_p._level + 1"), *extra_rec) \
            .from_(exp.alias_(bare.copy(), "_c", table=True)) \
            .join(exp.alias_(exp.to_table("_h"), "_p", table=True), on=rec_cond)
        union = exp.union(anchor, recursive, distinct=False)

        def outer(node):
            if id(node) in outer_repl:
                return outer_repl[id(node)]
            if isinstance(node, exp.Column) and not node.table and node.name.upper() == "LEVEL":
                return exp.column("_level")
            if isinstance(node, exp.Column) and node.name.upper() in ("CONNECT_BY_ISLEAF", "CONNECT_BY_ISCYCLE"):
                self.todo(f"{node.name.upper()} не переведён")
            return node

        for e in list(select.expressions):
            e.replace(_rewrite(e, outer))
        if select.args.get("where"):
            select.set("where", _rewrite(select.args["where"], outer))
        select.set("connect", None)
        order = select.args.get("order")
        if order is not None and order.args.get("siblings"):
            order.set("siblings", None)
            self.todo("ORDER SIBLINGS BY переведён как ORDER BY: порядок обхода дерева не сохраняется")
        if connect.args.get("nocycle"):
            self.todo("NOCYCLE не переведён: при циклах в данных WITH RECURSIVE не остановится")
        frm.set("this", exp.alias_(exp.to_table("_h"), alias, table=True))
        select.with_("_h", as_=union, recursive=True, copy=False)
        self.note("CONNECT BY переведён в WITH RECURSIVE")

    # --- Teradata ---
    def teradata(self, tree: exp.Expression) -> exp.Expression:
        for s in tree.find_all(exp.TableSample):
            self.todo("SAMPLE Teradata (случайная выборка строк) не переведён: в отчётном SQL уберите или "
                      "замените на ORDER BY random() LIMIT n")
            s.pop()
        return _rewrite(tree, self.teradata_node)

    def teradata_node(self, node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table and not node.this.quoted and node.name.upper() == "DATE":
            return exp.CurrentDate()
        if isinstance(node, exp.Cast) and node.args.get("format") is not None:
            fmt = node.args["format"]
            to = node.to
            if isinstance(fmt, exp.Literal) and (to.this == exp.DataType.Type.UNKNOWN or to.is_type(
                    *exp.DataType.TEXT_TYPES)):
                self.note("CAST … FORMAT переведён в to_char")
                return exp.Anonymous(this="TO_CHAR", expressions=[node.this,
                                                                  exp.Literal.string(_td_format(fmt.this))])
            self.todo(f"CAST … FORMAT '{fmt.sql()}' к типу {to.sql()} не переведён")
        if isinstance(node, exp.Anonymous):
            name = node.name.upper()
            args = node.expressions
            if name == "ZEROIFNULL" and len(args) == 1:
                return exp.Coalesce(this=args[0], expressions=[exp.Literal.number(0)])
            if name == "NULLIFZERO" and len(args) == 1:
                return exp.Nullif(this=args[0], expression=exp.Literal.number(0))
            if name == "INDEX" and len(args) == 2:
                return exp.StrPosition(this=args[0], substr=args[1])
            if name == "OREPLACE" and len(args) in (2, 3):
                return exp.Anonymous(this="REPLACE", expressions=[*args, *([] if len(args) == 3
                                                                           else [exp.Literal.string("")])])
            if name == "OTRANSLATE" and len(args) == 3:
                return exp.Anonymous(this="TRANSLATE", expressions=args)
            if name == "CHARACTERS" and len(args) == 1:
                return exp.Length(this=args[0])
        return self.common_functions(node)

    # --- T-SQL ---
    def tsql(self, tree: exp.Expression) -> exp.Expression:
        for limit in tree.find_all(exp.Limit):
            if limit.args.get("percent"):
                self.todo("TOP … PERCENT не переведён")
        return _rewrite(tree, self.tsql_node)

    def tsql_node(self, node: exp.Expression) -> exp.Expression:
        # Базы MS SQL почти всегда в collation *_CI_* (без учёта регистра), PostgreSQL сравнивает с учётом
        if isinstance(node, exp.StrPosition) and node.args.get("substr") is not None:
            self.note("CHARINDEX без учёта регистра, как в collation *_CI_*: lower() с обеих сторон")
            node.set("this", exp.Lower(this=node.this))
            node.set("substr", exp.Lower(this=node.args["substr"]))
            return node
        if isinstance(node, exp.Like):
            self.note("LIKE переведён в ILIKE: в MS SQL (collation *_CI_*) сравнение без учёта регистра")
            return exp.ILike(this=node.this, expression=node.expression)
        if isinstance(node, (exp.EQ, exp.NEQ)) and STRING in (self.category(node.this),
                                                             self.category(node.expression)):
            self.note("сравнение строк: в MS SQL (collation *_CI_*) без учёта регистра, здесь — с учётом")
        return node

    # --- общие функции Oracle и Teradata ---
    def common_functions(self, node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.MonthsBetween) or (isinstance(node, exp.Anonymous)
                                                   and node.name.upper() == "MONTHS_BETWEEN"):
            args = [node.this, node.expression] if isinstance(node, exp.MonthsBetween) else node.expressions
            if len(args) != 2:
                return node
            self.note("MONTHS_BETWEEN переведён формулой (месяцы + дни / 31)")
            if self.target == "clickhouse":
                return self.tpl("dateDiff('month', _b, _a)", _a=args[0], _b=args[1])
            return _tpl("((EXTRACT(YEAR FROM _a) - EXTRACT(YEAR FROM _b)) * 12 + EXTRACT(MONTH FROM _a) "
                        "- EXTRACT(MONTH FROM _b) + (EXTRACT(DAY FROM _a) - EXTRACT(DAY FROM _b)) / 31.0)",
                        _a=args[0], _b=args[1])
        return node

    # --- проверка результата ---
    def check_output(self, sql: str) -> None:
        try:
            tokens = sqlglot.Dialect.get_or_raise(self.target).tokenize(sql)
        except TokenError:
            return
        T = sqlglot.TokenType
        for i, tok in enumerate(tokens):
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt is None or nxt.token_type != T.L_PAREN or tok.token_type != T.VAR:
                continue
            start = i
            while start >= 2 and tokens[start - 1].token_type == T.DOT:
                start -= 2
            before = tokens[start - 1] if start >= 1 else None
            if before is not None and before.token_type in (T.INTO, T.TABLE, T.FROM, T.JOIN, T.UPDATE, T.VIEW):
                continue  # INSERT INTO t (колонки), CREATE TABLE t (…)
            prev = tokens[i - 1] if i >= 1 else None
            if sql_name(tokens, tok).lower() in self.known_functions:
                continue
            if prev is not None and prev.token_type == T.DOT:
                self.todo(f"функция {sql_name(tokens, tok)} — пользовательская (UDF): перенесите её отдельно")
            elif not is_known_function(tok.text, self.target):
                self.todo(f"функция {tok.text.upper()} не переведена: в {self.dialect} её нет")
        try:
            tree = sqlglot.parse_one(sql, read=self.target)
        except (ParseError, TokenError) as e:
            self.todo(f"результат не разбирается как SQL {self.dialect}: {_short(e)}")
            return
        if tree.find(exp.Connect):
            self.todo("CONNECT BY не переведён")
        for col in tree.find_all(exp.Column):
            if not col.table and not col.this.quoted and col.name.upper() in _PSEUDO.get(self.source, set()):
                self.todo(f"псевдоколонка {col.name.upper()} не переведена")


def sql_name(tokens, tok) -> str:
    i = tokens.index(tok)
    parts = [tok.text]
    while i >= 2 and tokens[i - 1].token_type == sqlglot.TokenType.DOT:
        i -= 2
        parts.insert(0, tokens[i].text)
    return ".".join(parts)


def _is_delete(e: exp.Expression | None) -> bool:
    return isinstance(e, exp.Delete) or (isinstance(e, exp.Var) and e.name.upper() == "DELETE")


def _is_rownum(e: exp.Expression) -> bool:
    return isinstance(e, exp.Column) and not e.table and e.name.upper() == "ROWNUM"


def _td_format(fmt: str) -> str:
    """Формат даты Teradata -> to_char PostgreSQL: HH — 24 часа, MMM — сокращённый месяц, B — пробел."""
    out = re.sub(r"HH(?!24)", "HH24", fmt.upper())
    out = out.replace("MMMM", "Month").replace("MMM", "Mon").replace("B", " ")
    return out


def _short(e: Exception) -> str:
    text = str(e).split("\n")[0]
    return re.sub(r"\x1b\[[0-9;]*m", "", text)[:200]


def translate_query(text: str, source: str, dialect: str = "greenplum7", names: NameMap | None = None,
                    default_schema: str = "", pretty: bool = True) -> QueryResult:
    """Запрос на диалекте sqlglot source (oracle, tsql, teradata, postgres) -> SQL цели и список todo."""
    if source == "teradata":
        text = _TD_LOCKING.sub("", text)
    return _Translator(source, dialect, names, default_schema, pretty).run(text)


_TD_LOCKING = re.compile(r"\bLOCKING\s+(?:ROW|TABLE\s+[\w.\"]+|DATABASE\s+[\w.\"]+|VIEW\s+[\w.\"]+)?\s*"
                         r"(?:FOR|IN)\s+(?:ACCESS|READ|WRITE|EXCLUSIVE|SHARE)(?:\s+MODE)?\s*", re.I)
