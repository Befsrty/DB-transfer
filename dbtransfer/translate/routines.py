"""Черновой перевод процедур PL/SQL, T-SQL и макросов/процедур Teradata в PL/pgSQL.

Перевод полуавтоматический: заголовок, параметры, переменные, управляющие конструкции (IF, циклы, блоки
исключений), присваивания и SQL внутри кода переводятся; всё, что перевести не удалось, остаётся
в тексте комментарием «-- TODO: …» с исходным оператором. Черновик не разворачивается сам: его
проверяют и доводят вручную. Отчёт о сложности берёт отсюда долю операторов, переведённых без TODO.

Что известно о различиях и учтено:
* имена колонок важнее имён переменных, как в Oracle: «#variable_conflict use_column»;
* SELECT … INTO в Oracle падает без строк и при нескольких строках: INTO STRICT;
* SQL%ROWCOUNT / @@ROWCOUNT -> GET DIAGNOSTICS;
* MERGE -> UPDATE … FROM + INSERT … WHERE NOT EXISTS (в Greenplum MERGE нет);
* процедура: в ADB 7 (PostgreSQL 12) — PROCEDURE и CALL, в ADB 6 — FUNCTION … RETURNS void и PERFORM;
  OUT-параметры процедуры в PostgreSQL 12 — только INOUT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dbtransfer import naming
from dbtransfer.generator.dialects import get_dialect
from dbtransfer.generator.sql import qualified, sql_comment
from dbtransfer.model import Model, ObjectType, OtherObject, SourceKind
from dbtransfer.translate.names import NameMap
from dbtransfer.translate.sql import AUTO, FAILED, PARTIAL, SOURCE_DIALECTS, SOURCE_NAMES, translate_query

# --- лексер ---
_TOKEN = re.compile(r"""
 (?P<ws>\s+)
|(?P<comment>--[^\n]*|/\*.*?\*/)
|(?P<string>[Nn]?'(?:[^']|'')*')
|(?P<qident>"(?:[^"]|"")*"|\[[^\]]*\])
|(?P<label><<\s*\w+\s*>>)
|(?P<word>(?:@@|@|\#\#|\#|:)?[A-Za-z_][\w$\#@]*)
|(?P<number>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
|(?P<op>:=|\.\.|<>|!=|<=|>=|\|\||=>|[-+*/%(),;.=<>!&|^~:])
|(?P<other>.)
""", re.S | re.X)


@dataclass
class Tok:
    kind: str
    text: str

    @property
    def up(self) -> str:
        return self.text.upper() if self.kind == "word" else self.text


def tokenize(text: str) -> list[Tok]:
    return [Tok(m.lastgroup, m.group()) for m in _TOKEN.finditer(text) if m.lastgroup not in ("ws", "comment")]


def join(tokens: list[Tok]) -> str:
    out = ""
    for i, t in enumerate(tokens):
        if i and not (t.text in (")", ",", ";", ".", "%") or tokens[i - 1].text in ("(", ".", "%")):
            out += " "
        out += t.text
    return out


def _words(tokens: list[Tok], n: int = 3) -> list[str]:
    return [t.up for t in tokens[:n]]


def _match_paren(tokens: list[Tok], i: int) -> int:
    """Индекс закрывающей скобки для tokens[i] == '('."""
    depth = 0
    for j in range(i, len(tokens)):
        if tokens[j].text == "(":
            depth += 1
        elif tokens[j].text == ")":
            depth -= 1
            if depth == 0:
                return j
    return len(tokens) - 1


def _split_commas(tokens: list[Tok]) -> list[list[Tok]]:
    parts, cur, depth = [], [], 0
    for t in tokens:
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
        if t.text == "," and depth == 0:
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        parts.append(cur)
    return parts


# --- типы ---
_TYPES = {
    "oracle": {"PLS_INTEGER": "integer", "BINARY_INTEGER": "integer", "SIMPLE_INTEGER": "integer",
               "NATURAL": "integer", "POSITIVE": "integer", "INTEGER": "integer", "INT": "integer",
               "SMALLINT": "smallint", "NUMBER": "numeric", "FLOAT": "double precision",
               "BINARY_DOUBLE": "double precision", "BINARY_FLOAT": "real", "VARCHAR2": "varchar",
               "NVARCHAR2": "varchar", "VARCHAR": "varchar", "CHAR": "char", "NCHAR": "char", "CLOB": "text",
               "NCLOB": "text", "LONG": "text", "DATE": "timestamp(0)", "TIMESTAMP": "timestamp",
               "BOOLEAN": "boolean", "BLOB": "bytea", "RAW": "bytea", "ROWID": "text", "SYS_REFCURSOR": "refcursor"},
    "tsql": {"INT": "integer", "INTEGER": "integer", "BIGINT": "bigint", "SMALLINT": "smallint",
             "TINYINT": "smallint", "BIT": "smallint", "DECIMAL": "numeric", "NUMERIC": "numeric",
             "MONEY": "numeric(19,4)", "SMALLMONEY": "numeric(10,4)", "FLOAT": "double precision", "REAL": "real",
             "VARCHAR": "varchar", "NVARCHAR": "varchar", "CHAR": "char", "NCHAR": "char", "TEXT": "text",
             "NTEXT": "text", "DATE": "date", "TIME": "time", "DATETIME": "timestamp(3)",
             "DATETIME2": "timestamp", "SMALLDATETIME": "timestamp(0)", "DATETIMEOFFSET": "timestamptz",
             "UNIQUEIDENTIFIER": "varchar(36)", "VARBINARY": "bytea", "BINARY": "bytea", "XML": "text",
             "SYSNAME": "varchar(128)"},
    "teradata": {"BYTEINT": "smallint", "SMALLINT": "smallint", "INTEGER": "integer", "INT": "integer",
                 "BIGINT": "bigint", "DECIMAL": "numeric", "DEC": "numeric", "NUMERIC": "numeric",
                 "NUMBER": "numeric", "FLOAT": "double precision", "REAL": "double precision",
                 "DOUBLE": "double precision", "VARCHAR": "varchar", "CHAR": "char", "CHARACTER": "char",
                 "CLOB": "text", "DATE": "date", "TIME": "time", "TIMESTAMP": "timestamp", "BYTE": "bytea",
                 "VARBYTE": "bytea", "BLOB": "bytea"},
}


class _Ctx:
    """Общее для одного объекта: сообщения, счётчики операторов, имена."""

    def __init__(self, source: str, dialect: str, names: NameMap, routines: dict, schema: str):
        self.source = source
        self.dialect = dialect
        self.names = names
        self.routines = routines  # (СХЕМА, ИМЯ) -> (имя в цели, процедура ли)
        self.schema = schema
        self.todos: list[str] = []
        self.notes: list[str] = []
        self.statements = 0
        self.auto_statements = 0
        self.declares: list[str] = []  # дополнительные переменные (record для циклов, _rowcount)
        self.procedures = get_dialect(dialect).procedures
        self.package = ""  # член пакета: вызовы без имени пакета ищутся в нём

    def todo(self, text: str) -> str:
        self.todos.append(text)
        return f"-- TODO: {text}"

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def declare(self, line: str) -> None:
        if line not in self.declares:
            self.declares.append(line)

    # --- выражения и SQL ---
    def _prepare(self, tokens: list[Tok]) -> list[Tok]:
        out: list[Tok] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            after = tokens[i + 2] if i + 2 < len(tokens) else None
            if nxt is not None and nxt.text == "%" and after is not None and after.kind == "word":
                attr = after.up
                if t.up == "SQL" and attr == "ROWCOUNT":
                    self.declare("_rowcount integer;")
                    out.append(Tok("word", "_rowcount"))
                    i += 3
                    continue
                if attr in ("NOTFOUND", "FOUND"):
                    out.extend([Tok("word", "NOT"), Tok("word", "FOUND")] if attr == "NOTFOUND"
                               else [Tok("word", "FOUND")])
                    i += 3
                    continue
            if self.source == "teradata" and t.kind == "word" and t.text.startswith(":"):
                out.append(Tok("word", t.text[1:]))
                i += 1
                continue
            out.append(t)
            i += 1
        if (self.source == "teradata" and len(out) > 2 and out[0].up in ("DEL", "DELETE")
                and out[-1].up == "ALL"):
            out = out[:-1]  # DELETE FROM t ALL — то же, что DELETE FROM t
        return out

    def expr(self, tokens: list[Tok]) -> tuple[str, bool]:
        """Выражение источника -> выражение PL/pgSQL. Второе значение — переведено ли без TODO."""
        text = join(self._prepare(tokens))
        if not text:
            return "", True
        r = translate_query(f"SELECT {text}", self.source, self.dialect, self.names, self.schema, pretty=False)
        if r.error:
            self.todos.append(f"выражение не разобрано: {text[:80]}")
            return text, False
        self.notes.extend(n for n in r.notes if n not in self.notes)
        sql = re.sub(r"^SELECT\s+", "", r.sql, count=1)
        if r.todos:
            self.todos.extend(r.todos)
            return sql, False
        return sql, True

    def sql(self, tokens: list[Tok], strict_into: bool) -> tuple[list[str], bool]:
        """SQL-оператор внутри кода: SELECT … INTO, INSERT, UPDATE, DELETE, MERGE."""
        tokens = self._prepare(tokens)
        into = ""
        if tokens and tokens[0].up in ("SELECT", "SEL", "WITH"):
            tokens, into = _cut_into(tokens)
        text = join(tokens)
        r = translate_query(text, self.source, self.dialect, self.names, self.schema)
        if r.error:
            return [self.todo(f"SQL не разобран ({r.error})"), *_commented(text)], False
        self.notes.extend(n for n in r.notes if n not in self.notes)
        statements = r.sql.split(";\n")
        lines: list[str] = []
        for k, st in enumerate(statements):
            if into and k == len(statements) - 1:
                st += f"\nINTO {'STRICT ' if strict_into else ''}{into}"
            lines.extend((st + ";").split("\n"))
            if "_rowcount integer;" in self.declares and re.match(r"^\s*(INSERT|UPDATE|DELETE)", st, re.I):
                lines.append("GET DIAGNOSTICS _rowcount = ROW_COUNT;")
        if r.todos:
            self.todos.extend(r.todos)
            return [*(f"-- TODO: {t}" for t in r.todos), *lines], False
        return lines, True

    def type(self, tokens: list[Tok]) -> tuple[str, bool]:
        """Тип переменной или параметра."""
        if not tokens:
            return "text", False
        if len(tokens) >= 3 and tokens[-2].text == "%" and tokens[-1].up in ("TYPE", "ROWTYPE"):
            ref = [t for t in tokens[:-2] if t.text != "."]
            attr = tokens[-1].up.lower()
            if attr == "type" and len(ref) >= 2:
                rel = self.names.resolve(ref[-3].text if len(ref) >= 3 else None, ref[-2].text, self.schema)
                if rel is not None:
                    return f"{rel.target}.{ref[-1].text.lower()}%type", True
                return f"{join(tokens[:-2]).lower()}%type", True  # переменная%TYPE
            if attr == "rowtype":
                rel = self.names.resolve(ref[-2].text if len(ref) >= 2 else None, ref[-1].text, self.schema)
                if rel is not None:
                    return f"{rel.target}%rowtype", True
                return "record", True  # курсор%ROWTYPE
        base = tokens[0].up.lstrip("@")
        mapped = _TYPES[self.source].get(base)
        if mapped is None:
            self.todos.append(f"тип {join(tokens)} не переведён")
            return join(tokens).lower(), False
        rest = tokens[1:]
        args = ""
        if rest and rest[0].text == "(":
            end = _match_paren(rest, 0)
            inner = [t.text for t in rest[1:end] if t.up not in ("BYTE", "CHAR")]
            if inner and inner[0].upper() == "MAX":
                return "text", True
            if "(" not in mapped:
                args = "(" + "".join(inner).replace(",", ", ") + ")"
        if mapped == "timestamp" and args:
            return f"timestamp{args}", True
        if mapped in ("varchar", "char", "numeric"):
            return mapped + args, True
        return mapped, True

    def call(self, name_tokens: list[Tok], args: list[Tok]) -> tuple[str, bool]:
        """Вызов процедуры: CALL (ADB 7) или PERFORM (ADB 6, функции)."""
        parts = [t.text for t in name_tokens if t.text != "."]
        key = None
        if len(parts) == 1:
            key = (self.schema.upper(), parts[0].upper())
            if key not in self.routines and self.package:
                key = (self.schema.upper(), f"{self.package}__{parts[0]}".upper())
        elif len(parts) == 2:
            key = (parts[0].upper(), parts[1].upper())
            if key not in self.routines:  # пакет.процедура той же схемы
                key = (self.schema.upper(), f"{parts[0]}__{parts[1]}".upper())
        elif len(parts) == 3:
            key = (parts[0].upper(), f"{parts[1]}__{parts[2]}".upper())
        arg_text = ""
        ok = True
        if args:
            items = []
            for a in _split_commas(args):
                if len(a) >= 2 and a[1].text == "=>":
                    value, good = self.expr(a[2:])
                    items.append(f"{a[0].text.lower()} => {value}")
                else:
                    value, good = self.expr(a)
                    items.append(value)
                ok = ok and good
            arg_text = ", ".join(items)
        if key in self.routines:
            target, is_proc = self.routines[key]
            verb = "CALL" if is_proc and self.procedures else "PERFORM"
            return f"{verb} {target}({arg_text});", ok
        name = ".".join(parts)
        self.todos.append(f"вызов {name}: процедуры нет в модели, перенесите её отдельно")
        return f"PERFORM {name.lower()}({arg_text});", False


def _commented(text: str) -> list[str]:
    return [f"-- {line}" for line in text.splitlines()]


def _cut_into(tokens: list[Tok]) -> tuple[list[Tok], str]:
    """SELECT a, b INTO x, y FROM … -> (SELECT a, b FROM …, 'x, y')."""
    depth = 0
    for i, t in enumerate(tokens):
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
        elif depth == 0 and t.up == "INTO":
            j = i + 1
            while j < len(tokens) and tokens[j].up not in ("FROM", "WHERE", "GROUP", "UNION", "ORDER", ";"):
                j += 1
            target = join(tokens[i + 1:j]).lower()
            return tokens[:i] + tokens[j:], target
    return tokens, ""


@dataclass
class RoutineTranslation:
    """Черновик PL/pgSQL одного объекта (процедура, функция, член пакета, макрос)."""

    type: ObjectType
    name: str
    target: str
    source_dialect: str
    status: str
    sql: str = ""
    todos: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    lines: int = 0
    statements: int = 0
    auto_statements: int = 0
    label: str = "процедура"
    depends: list[str] = field(default_factory=list)

    @property
    def auto(self) -> bool:
        return self.status == AUTO

    @property
    def auto_share(self) -> float:
        return self.auto_statements / self.statements if self.statements else 0.0


# --- PL/SQL и SPL Teradata: операторы через «;» ---
_SPLIT_AFTER = {"THEN", "ELSE", "LOOP", "BEGIN", "EXCEPTION", "DO", "DECLARE", "REPEAT"}
_EXCEPTIONS = {"NO_DATA_FOUND": "no_data_found", "TOO_MANY_ROWS": "too_many_rows",
               "DUP_VAL_ON_INDEX": "unique_violation", "ZERO_DIVIDE": "division_by_zero",
               "INVALID_NUMBER": "invalid_text_representation", "VALUE_ERROR": "data_exception",
               "OTHERS": "OTHERS"}
_SQL_START = {"SELECT", "SEL", "INSERT", "INS", "UPDATE", "UPD", "DELETE", "DEL", "MERGE", "WITH"}


def pieces(tokens: list[Tok], source: str) -> list[list[Tok]]:
    """Тело -> операторы и заголовки блоков (IF … THEN, LOOP, BEGIN, WHEN … THEN, ELSE, END IF)."""
    result: list[list[Tok]] = []
    cur: list[Tok] = []
    depth = 0
    case_expr = 0
    stmt_case = 0
    for i, t in enumerate(tokens):
        up = t.up
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
        if depth == 0 and t.kind == "word":
            if up == "CASE":
                if not cur:
                    stmt_case += 1
                else:
                    case_expr += 1
            elif up == "END" and case_expr and cur:
                case_expr -= 1
                cur.append(t)
                continue
            elif up == "END" and not cur and i + 1 < len(tokens) and tokens[i + 1].up == "CASE":
                stmt_case = max(0, stmt_case - 1)
        if t.text == ";" and depth == 0 and not case_expr:
            if cur:
                result.append(cur)
            cur = []
            continue
        if (depth == 0 and not case_expr and t.kind == "word" and up == "WHEN" and stmt_case and cur
                and cur[0].up == "CASE"):
            result.append(cur)
            cur = []
        if t.kind == "label" or (t.kind == "word" and i + 1 < len(tokens) and tokens[i + 1].text == ":"
                                 and not cur and source == "teradata"):
            result.append([t] if t.kind == "label" else [t, tokens[i + 1]])
            continue
        if t.text == ":" and cur == [] and result and result[-1][-1].text == ":":
            continue
        cur.append(t)
        if cur[0].up in _SQL_START or (cur[0].up == "OPEN" and len(cur) > 2) or cur[0].up == "CURSOR":
            continue  # внутри SQL (MERGE … THEN, CASE … ELSE) операторы не режутся
        if depth == 0 and not case_expr and t.kind == "word" and up in _SPLIT_AFTER:
            prev = cur[-2].up if len(cur) >= 2 else ""
            if up == "LOOP" and prev == "END":
                continue
            if up == "DECLARE" and source == "teradata":
                continue  # DECLARE v INTEGER; — оператор, а не начало блока
            if up == "BEGIN" and prev in ("TRY", "CATCH"):
                continue
            if up == "ELSE" and cur[0].up not in ("ELSE",) and len(cur) > 1:
                # «… ELSE» внутри оператора (например, после THEN без «;»): разрезаем перед ELSE
                result.append(cur[:-1])
                cur = [t]
            result.append(cur)
            cur = []
    if cur:
        result.append(cur)
    return result


class _BlockConverter:
    """PL/SQL (Oracle) и SPL (Teradata) -> PL/pgSQL по операторам."""

    def __init__(self, ctx: _Ctx):
        self.ctx = ctx
        self.lines: list[str] = []
        self.indent = 1
        self.in_declare = False
        self.blocks: list[bool] = []  # открытые BEGIN: есть ли у блока секция EXCEPTION
        self.strict_into = ctx.source == "oracle"

    def emit(self, line: str, ok: bool = True, count: bool = True) -> None:
        if count:
            self.ctx.statements += 1
            if ok:
                self.ctx.auto_statements += 1
        for part in line.split("\n"):
            self.lines.append("    " * self.indent + part)

    def emit_lines(self, lines: list[str], ok: bool) -> None:
        self.ctx.statements += 1
        if ok:
            self.ctx.auto_statements += 1
        for part in lines:
            self.lines.append("    " * self.indent + part)

    def todo_stmt(self, tokens: list[Tok], why: str) -> None:
        self.emit_lines([self.ctx.todo(why), *_commented(join(tokens))], False)

    def convert(self, body: list[Tok]) -> list[str]:
        for p in pieces(body, self.ctx.source):
            self.piece(p)
        return self.lines

    def cond(self, tokens: list[Tok]) -> tuple[str, bool]:
        return self.ctx.expr(tokens)

    def piece(self, p: list[Tok]) -> None:  # noqa: C901 — разбор по первому слову оператора
        ctx = self.ctx
        w = _words(p, 4)
        head = w[0]
        if p[0].kind == "label":
            self.emit(p[0].text, count=False)
            return
        if len(p) == 2 and p[1].text == ":":
            self.emit(f"<<{p[0].text.lower()}>>", count=False)
            return
        if self.in_declare and head != "BEGIN":
            self.declaration(p)
            return
        if head == "DECLARE" and ctx.source == "teradata" and len(p) > 1:
            line, ok = declaration(ctx, p)
            if line.startswith("-- TODO"):
                self.emit_lines([line, *_commented(join(p))], False)
            else:
                ctx.declare(line)
                self.ctx.statements += 1
                self.ctx.auto_statements += ok
            return
        if head == "DECLARE" and len(p) == 1:
            self.emit("DECLARE", count=False)
            self.in_declare = True
            return
        if head == "BEGIN" and len(p) == 1:
            self.in_declare = False
            self.emit("BEGIN", count=False)
            self.blocks.append(False)
            self.indent += 1
            return
        if head == "EXCEPTION" and len(p) == 1:
            self.indent -= 1
            self.emit("EXCEPTION", count=False)
            if self.blocks:
                self.blocks[-1] = True
            self.indent += 2
            return
        if head == "END":
            kind = w[1] if len(w) > 1 else ""
            step = 1
            if kind not in ("IF", "CASE", "LOOP", "WHILE", "FOR", "REPEAT") and self.blocks:
                step = 2 if self.blocks.pop() else 1
            self.indent = max(1, self.indent - step)
            if kind in ("IF", "CASE"):
                self.emit(f"END {kind};", count=False)
            elif kind in ("LOOP", "WHILE", "FOR"):
                self.emit("END LOOP;", count=False)
            elif kind == "REPEAT":
                self.emit("END LOOP;", count=False)
            else:
                self.emit("END;", count=False)
            return
        if head in ("IF", "ELSIF", "ELSEIF") and p[-1].up == "THEN":
            c, ok = self.cond(p[1:-1])
            if head != "IF":
                self.indent -= 1
            self.emit(f"{'IF' if head == 'IF' else 'ELSIF'} {c} THEN", ok)
            self.indent += 1
            return
        if head == "ELSE" and len(p) == 1:
            self.indent -= 1
            self.emit("ELSE", count=False)
            self.indent += 1
            return
        if head == "WHEN" and p[-1].up == "THEN":
            return self.when(p)
        if head == "CASE":
            c, ok = self.cond(p[1:]) if len(p) > 1 else ("", True)
            self.emit(f"CASE {c}".rstrip(), ok)
            self.indent += 1
            return
        if head == "LOOP" and len(p) == 1:
            self.emit("LOOP", count=False)
            self.indent += 1
            return
        if head == "WHILE" and p[-1].up in ("LOOP", "DO"):
            c, ok = self.cond(p[1:-1])
            self.emit(f"WHILE {c} LOOP", ok)
            self.indent += 1
            return
        if head == "FOR" and p[-1].up in ("LOOP", "DO"):
            return self.for_loop(p)
        if head == "REPEAT" and len(p) == 1:
            self.emit("LOOP", count=False)
            self.indent += 1
            return
        if head == "UNTIL":
            c, ok = self.cond(p[1:])
            self.emit(f"EXIT WHEN {c};", ok)
            return
        if head == "NULL" and len(p) == 1:
            self.emit("NULL;")
            return
        if head == "RETURN":
            if len(p) == 1:
                self.emit("RETURN;")
            else:
                e, ok = ctx.expr(p[1:])
                self.emit(f"RETURN {e};", ok)
            return
        if head in ("EXIT", "LEAVE", "CONTINUE", "ITERATE"):
            verb = "EXIT" if head in ("EXIT", "LEAVE") else "CONTINUE"
            rest = p[1:]
            label = ""
            if rest and rest[0].kind == "word" and rest[0].up != "WHEN":
                label = " " + rest[0].text.lower()
                rest = rest[1:]
            if rest and rest[0].up == "WHEN":
                c, ok = self.cond(rest[1:])
                self.emit(f"{verb}{label} WHEN {c};", ok)
            else:
                self.emit(f"{verb}{label};")
            return
        if head == "SET" and ctx.source == "teradata":
            eq = next((i for i, t in enumerate(p) if t.text == "="), None)
            if eq is not None:
                e, ok = ctx.expr(p[eq + 1:])
                self.emit(f"{join(p[1:eq]).lower()} := {e};", ok)
                return
        if len(p) > 2 and (p[1].text == ":=" or (p[1].text == "." and len(p) > 3 and p[3].text == ":=")):
            k = 1 if p[1].text == ":=" else 3
            e, ok = ctx.expr(p[k + 1:])
            self.emit(f"{join(p[:k]).lower()} := {e};", ok)
            return
        if head in _SQL_START:
            lines, ok = ctx.sql(p, self.strict_into)
            self.emit_lines(lines, ok)
            return
        if head == "TRUNCATE":
            rel = self.relation(p[2:] if len(p) > 2 and p[1].up == "TABLE" else p[1:])
            if rel:
                self.emit(f"TRUNCATE TABLE {rel};")
            else:
                self.todo_stmt(p, "TRUNCATE: таблицы нет в модели")
            return
        if head in ("COMMIT", "ROLLBACK"):
            if ctx.procedures:
                ctx.note("COMMIT/ROLLBACK в процедуре: в ADB 7 работает при вызове CALL вне транзакции и не "
                         "внутри блока с EXCEPTION")
                self.emit(f"{head};")
            else:
                self.todo_stmt(p, f"{head} внутри функции ADB 6 невозможен: транзакцией управляет вызывающий")
            return
        if head == "SAVEPOINT" or (head == "ROLLBACK" and len(w) > 1 and w[1] == "TO"):
            self.todo_stmt(p, "точки сохранения внутри PL/pgSQL не поддерживаются: используйте вложенный BEGIN … "
                              "EXCEPTION")
            return
        if head == "OPEN":
            if len(p) > 2 and p[2].up == "FOR":
                lines, ok = ctx.sql(p[3:], False)
                self.emit_lines([f"OPEN {p[1].text.lower()} FOR", *lines], ok)
            else:
                self.emit(f"OPEN {join(p[1:]).lower()};")
            return
        if head == "FETCH":
            if any(t.up == "BULK" for t in p):
                self.todo_stmt(p, "BULK COLLECT не переведён: используйте массив и array_agg или цикл FOR")
                return
            self.emit(f"FETCH {join(p[1:]).lower()};")
            return
        if head == "CLOSE":
            self.emit(f"CLOSE {join(p[1:]).lower()};")
            return
        if head == "FORALL":
            self.todo_stmt(p, "FORALL не переведён: замените одним INSERT/UPDATE … SELECT")
            return
        if head == "EXECUTE" and len(w) > 1 and w[1] == "IMMEDIATE":
            e, _ = ctx.expr(p[2:]) if not any(t.up in ("INTO", "USING") for t in p) else (join(p[2:]), False)
            self.emit_lines([ctx.todo("динамический SQL: текст запроса — на диалекте источника, проверьте его"),
                             f"EXECUTE {e};"], False)
            return
        if head == "RAISE":
            if len(p) == 1:
                self.emit("RAISE;")
            else:
                self.todo_stmt(p, f"исключение {join(p[1:])} источника: объявите его через RAISE EXCEPTION … "
                                  "USING ERRCODE")
            return
        if head == "RAISE_APPLICATION_ERROR" and len(p) > 2 and p[1].text == "(":
            args = _split_commas(p[2:_match_paren(p, 1)])
            msg, ok = ctx.expr(args[1]) if len(args) > 1 else ("'ошибка'", False)
            code = "".join(t.text for t in args[0]) if args else ""
            self.emit(f"RAISE EXCEPTION '%', {msg} USING DETAIL = 'код Oracle {code}';", ok)
            return
        if head in ("DBMS_OUTPUT", "DBMS_OUTPUT.PUT_LINE") or (head == "DBMS_OUTPUT" and len(w) > 2):
            args = p[p.index(next(t for t in p if t.text == "(")) + 1:-1] if any(t.text == "(" for t in p) else []
            e, ok = ctx.expr(args)
            self.emit(f"RAISE NOTICE '%', {e};", ok)
            return
        if head == "CALL" or head == "EXEC" or head == "EXECUTE":
            p = p[1:]
            head = p[0].up if p else ""
        if head == "PRAGMA":
            self.todo_stmt(p, "PRAGMA не переводится (AUTONOMOUS_TRANSACTION — через dblink)")
            return
        if head == "GOTO":
            self.todo_stmt(p, "GOTO в PL/pgSQL нет: перепишите через циклы и EXIT")
            return
        if p and p[0].kind == "word":
            k = 1
            while k + 1 < len(p) and p[k].text == "." and p[k + 1].kind == "word":
                k += 2
            if k == len(p) or (p[k].text == "(" and _match_paren(p, k) == len(p) - 1):
                args = p[k + 1:-1] if k < len(p) else []
                line, ok = ctx.call(p[:k], args)
                self.emit(line, ok)
                return
        self.todo_stmt(p, "оператор не переведён")

    def relation(self, tokens: list[Tok]) -> str:
        parts = [t.text for t in tokens if t.text != "."]
        if not parts:
            return ""
        rel = self.ctx.names.resolve(parts[-2] if len(parts) > 1 else None, parts[-1], self.ctx.schema)
        return rel.target if rel else ""

    def when(self, p: list[Tok]) -> None:
        """WHEN … THEN: обработчик исключения или ветка оператора CASE."""
        names = [t for t in p[1:-1] if t.up != "OR"]
        if names and all(t.kind == "word" for t in names) and (any(t.up in _EXCEPTIONS for t in names)
                                                               or self.ctx.source == "oracle"):
            mapped, ok = [], True
            for t in names:
                if t.up in _EXCEPTIONS:
                    mapped.append(_EXCEPTIONS[t.up])
                else:
                    ok = False
                    self.ctx.todos.append(f"исключение {t.text} источника: в PL/pgSQL обрабатывается по SQLSTATE")
                    mapped.append("OTHERS")
            if any(t.up == "VALUE_ERROR" for t in names):
                self.ctx.note("VALUE_ERROR переведено как data_exception (весь класс ошибок данных)")
            self.indent -= 1
            self.emit(f"WHEN {' OR '.join(mapped)} THEN", ok)
            self.indent += 1
            return
        c, ok = self.cond(p[1:-1])
        self.indent -= 1
        self.emit(f"WHEN {c} THEN", ok)
        self.indent += 1

    def for_loop(self, p: list[Tok]) -> None:
        ctx = self.ctx
        body = p[1:-1]
        var = body[0].text.lower() if body else "r"
        if len(body) > 2 and body[1].up == "IN":
            rest = body[2:]
            if any(t.text == ".." for t in rest):
                k = next(i for i, t in enumerate(rest) if t.text == "..")
                rev = rest and rest[0].up == "REVERSE"
                lo, ok1 = ctx.expr(rest[1 if rev else 0:k])
                hi, ok2 = ctx.expr(rest[k + 1:])
                self.emit(f"FOR {var} IN {'REVERSE ' if rev else ''}{lo}..{hi} LOOP", ok1 and ok2)
                self.indent += 1
                return
            if rest[0].text == "(":
                lines, ok = ctx.sql(rest[1:_match_paren(rest, 0)], False)
                ctx.declare(f"{var} record;")
                self.emit_lines([f"FOR {var} IN", *["    " + x.rstrip(";") for x in lines], "LOOP"], ok)
                self.indent += 1
                return
            self.emit(f"FOR {var} IN {join(rest).lower()} LOOP")  # курсор
            self.indent += 1
            return
        if len(body) > 2 and body[1].up == "AS":  # Teradata: FOR r AS [c CURSOR FOR] SELECT … DO
            rest = body[2:]
            if len(rest) > 2 and rest[1].up == "CURSOR" and rest[2].up == "FOR":
                rest = rest[3:]
            lines, ok = ctx.sql(rest, False)
            ctx.declare(f"{var} record;")
            self.emit_lines([f"FOR {var} IN", *["    " + x.rstrip(";") for x in lines], "LOOP"], ok)
            self.indent += 1
            return
        self.todo_stmt(p, "цикл FOR не разобран")
        self.indent += 1

    def declaration(self, p: list[Tok]) -> None:
        line, ok = declaration(self.ctx, p)
        self.emit(line, ok)


def declaration(ctx: _Ctx, p: list[Tok]) -> tuple[str, bool]:
    """Объявление в секции DECLARE: переменная, константа, курсор, тип, исключение."""
    w = _words(p, 3)
    name = p[0].text.lower()
    if w[0] == "CURSOR":
        cur_name = p[1].text.lower()
        k = next((i for i, t in enumerate(p) if t.up in ("IS", "FOR")), None)
        if k is None:
            return ctx.todo(f"курсор {cur_name} не разобран"), False
        params = ""
        if p[2].text == "(":
            end = _match_paren(p, 2)
            items = []
            for a in _split_commas(p[3:end]):
                tp, _ = ctx.type([t for t in a[1:] if t.up != "IN"])
                items.append(f"{a[0].text.lower()} {tp}")
            params = "(" + ", ".join(items) + ")"
        lines, ok = ctx.sql(p[k + 1:], False)
        body = "\n    ".join(x.rstrip(";") for x in lines)
        return f"{cur_name} CURSOR{params} FOR\n    {body};", ok
    if len(w) > 1 and w[1] == "EXCEPTION":
        return ctx.todo(f"исключение {name}: в PL/pgSQL нет объявляемых исключений, используйте ERRCODE"), False
    if w[0] in ("TYPE", "SUBTYPE"):
        return ctx.todo(f"тип {p[1].text} (коллекция или запись) не переведён"), False
    if w[0] == "PRAGMA":
        return ctx.todo(f"{join(p)} не переводится"), False
    if w[0] in ("PROCEDURE", "FUNCTION"):
        return ctx.todo("вложенная процедура или функция: вынесите её отдельно"), False
    if w[0] == "DECLARE":  # Teradata: DECLARE v INTEGER DEFAULT 0
        p = p[1:]
        name = p[0].text.lower()
        if any(t.up == "HANDLER" for t in p):
            return ctx.todo(f"обработчик {join(p)}: переведите в EXCEPTION WHEN … THEN"), False
    rest = p[1:]
    constant = bool(rest) and rest[0].up == "CONSTANT"
    if constant:
        rest = rest[1:]
    default_at = next((i for i, t in enumerate(rest) if t.text == ":=" or t.up == "DEFAULT"), None)
    type_tokens = rest[:default_at] if default_at is not None else rest
    not_null = False
    if len(type_tokens) >= 2 and type_tokens[-2].up == "NOT" and type_tokens[-1].up == "NULL":
        not_null, type_tokens = True, type_tokens[:-2]
    tp, ok = ctx.type(type_tokens)
    line = f"{name}{' CONSTANT' if constant else ''} {tp}{' NOT NULL' if not_null else ''}"
    if default_at is not None:
        e, ok2 = ctx.expr(rest[default_at + 1:])
        line += f" := {e}"
        ok = ok and ok2
    return line + ";", ok


# --- заголовки ---
_HEAD_KINDS = ("PROCEDURE", "FUNCTION", "MACRO", "PROC", "TRIGGER", "PACKAGE")


def _skip_create(tokens: list[Tok]) -> list[Tok]:
    i = 0
    while i < len(tokens) and tokens[i].up in ("CREATE", "OR", "REPLACE", "ALTER", "EDITIONABLE",
                                               "NONEDITIONABLE"):
        i += 1
    return tokens[i:]


def _name_end(tokens: list[Tok], i: int) -> int:
    j = i + 1
    while j + 1 < len(tokens) and tokens[j].text == "." and tokens[j + 1].kind in ("word", "qident"):
        j += 2
    return j


def _clean(name: str) -> str:
    return name.strip('"[]')


class RoutineSet:
    """Перевод всех процедур модели: общие имена (для вызовов друг друга) и результаты."""

    def __init__(self, model: Model, dialect: str, names: NameMap | None = None):
        self.model = model
        self.dialect = dialect
        self.source = SOURCE_DIALECTS.get(model.source_kind, "")
        self.names = names or NameMap(model)
        self.prefix = model.schema_prefix
        self.routines: dict[tuple[str, str], tuple[str, bool]] = {}
        self._registered: list | None = None

    def target_name(self, schema: str, name: str) -> str:
        target_schema = naming.load_naming()["schemas"]["source"].format(prefix=self.prefix, schema=schema.lower())
        return qualified(target_schema, name.lower())

    def objects(self) -> list[tuple[OtherObject, str, list[Tok]]]:
        """(объект, схема, лексемы) для каждой процедуры; пакеты раскладываются на члены."""
        result = []
        for o in sorted(self.model.others, key=lambda o: o.name):
            if o.type != ObjectType.DB_ROUTINE or not o.text.strip() or "." not in o.name:
                continue
            schema = o.schema_name or o.name.split(".", 1)[0]
            result.append((o, schema, tokenize(o.text)))
        return result

    def register(self, items) -> list[tuple[OtherObject, str, str, str, list[Tok]]]:
        """Имена всех процедур до перевода: чтобы вызовы находили друг друга."""
        if self._registered is not None:
            return self._registered
        out = []
        for o, schema, tokens in items:
            body = _skip_create(tokens)
            if not body:
                continue
            kind = body[0].up
            if kind == "PACKAGE" and len(body) > 1 and body[1].up == "BODY":
                pkg_end = _name_end(body, 2)
                pkg = _clean(body[pkg_end - 1].text)
                for member_kind, member, toks in _package_members(body[pkg_end:]):
                    full = f"{pkg}__{member}"
                    self.routines[(schema.upper(), full.upper())] = (self.target_name(schema, full),
                                                                     member_kind == "PROCEDURE")
                    if member_kind == "FUNCTION":
                        self.names.functions[(schema.upper(), full.upper())] = self.target_name(schema, full)
                        self.names.functions[(pkg.upper(), member.upper())] = self.target_name(schema, full)
                    out.append((o, schema, member_kind, full, toks))
                continue
            if kind not in _HEAD_KINDS or len(body) < 2:
                continue
            end = _name_end(body, 1)
            name = _clean(body[end - 1].text)
            self.routines[(schema.upper(), name.upper())] = (self.target_name(schema, name),
                                                             kind in ("PROCEDURE", "PROC"))
            if kind == "FUNCTION":
                self.names.functions[(schema.upper(), name.upper())] = self.target_name(schema, name)
            out.append((o, schema, kind, name, body))
        self._registered = out
        return out

    def translate_all(self) -> list[RoutineTranslation]:
        if not self.source or self.model.source_kind == SourceKind.GREENPLUM or self.dialect == "clickhouse":
            return []
        result = []
        if self._registered is None:
            self._registered = self.register(self.objects())
        for o, schema, kind, name, tokens in self._registered:
            ctx = _Ctx(self.source, self.dialect, self.names, self.routines, schema)
            if "__" in name:
                ctx.package = name.split("__", 1)[0]
            if re.search(r"SQL\s*%\s*ROWCOUNT|@@ROWCOUNT", o.text, re.I):
                ctx.declare("_rowcount integer;")
            target = self.target_name(schema, name)
            t = RoutineTranslation(ObjectType.DB_ROUTINE, f"{schema}.{name}" if "__" in name else o.name, target,
                                   self.source, PARTIAL, lines=o.lines or o.text.count("\n") + 1,
                                   label=_label(o.info))
            try:
                if self.source == "tsql":
                    from dbtransfer.translate.tsql import convert_tsql

                    sql = convert_tsql(ctx, kind, target, tokens)
                elif kind == "MACRO":
                    sql = self.convert_macro(ctx, target, tokens)
                elif kind == "TRIGGER":
                    sql = None
                    ctx.todos.append("триггер переводится вручную: в Greenplum триггеры на строки не работают "
                                     "на сегментах, логику переносят в процедуры загрузки")
                else:
                    sql = self.convert_block(ctx, kind, target, tokens)
            except (IndexError, StopIteration, ValueError) as e:
                sql = None
                ctx.todos.append(f"заголовок или тело не разобраны: {e}")
            t.todos, t.notes = _uniq(ctx.todos), ctx.notes
            t.statements, t.auto_statements = ctx.statements, ctx.auto_statements
            if sql is None:
                t.status = FAILED
                t.sql = (f"-- {SOURCE_NAMES[self.source]}: {t.label} {t.name}\n"
                         + "\n".join(f"-- TODO: {x}" for x in t.todos) + "\n/*\n" + o.text.strip() + "\n*/\n")
            else:
                t.status = AUTO if not t.todos else PARTIAL
                head = f"-- {SOURCE_NAMES[self.source]}: {t.label} {t.name}"
                if t.statements:
                    head += f"; операторов {t.statements}, переведено без TODO {t.auto_statements}"
                if t.notes:
                    head += "\n" + sql_comment("переведено: " + "; ".join(t.notes))
                t.sql = head + "\n" + sql
            result.append(t)
        return result

    # --- PL/SQL, SPL ---
    def params(self, ctx: _Ctx, tokens: list[Tok], is_proc: bool) -> tuple[str, bool]:
        """(p IN NUMBER, q OUT VARCHAR2) -> (p numeric, INOUT q varchar)."""
        items, ok, seen_default = [], True, False
        for a in _split_commas(tokens):
            a = [t for t in a if t.up not in ("NOCOPY",)]
            if not a:
                continue
            mode = ""
            if a[0].up in ("IN", "OUT", "INOUT"):  # Teradata: IN p INTEGER
                mode_tokens, a = [a[0]], a[1:]
                if a and a[0].up == "OUT" and mode_tokens[0].up == "IN":
                    mode_tokens.append(a[0])
                    a = a[1:]
                mode = " ".join(t.up for t in mode_tokens)
                name, rest = a[0], a[1:]
            else:
                name, rest = a[0], a[1:]
                while rest and rest[0].up in ("IN", "OUT"):
                    mode = (mode + " " + rest[0].up).strip()
                    rest = rest[1:]
            default_at = next((i for i, t in enumerate(rest) if t.text == ":=" or t.up == "DEFAULT"), None)
            tp, good = ctx.type(rest[:default_at] if default_at is not None else rest)
            pg_mode = {"": "", "IN": "", "OUT": "OUT", "IN OUT": "INOUT", "INOUT": "INOUT"}.get(mode, "")
            if pg_mode == "OUT" and is_proc and ctx.procedures:
                pg_mode = "INOUT"
                ctx.note("OUT-параметры процедуры стали INOUT: в PostgreSQL 12 у процедур нет OUT")
            item = f"{pg_mode + ' ' if pg_mode else ''}{self.param_name(name.text)} {tp}"
            if default_at is not None:
                e, good2 = ctx.expr(rest[default_at + 1:])
                item += f" DEFAULT {e}"
                good = good and good2
            if default_at is None and seen_default and pg_mode != "OUT":
                item += " DEFAULT NULL"  # в PostgreSQL после параметра со значением по умолчанию — тоже
                ctx.note("параметрам после параметра со значением по умолчанию добавлено DEFAULT NULL")
            seen_default = seen_default or default_at is not None
            items.append(item)
            ok = ok and good
        return ", ".join(items), ok

    def param_name(self, name: str) -> str:
        return _clean(name).lower()

    def wrap(self, ctx: _Ctx, kind: str, target: str, params: str, returns: str, decls: list[str],
             body: list[str]) -> str:
        return wrap_routine(ctx, kind, target, params, returns, decls, body)

    def convert_block(self, ctx: _Ctx, kind: str, target: str, tokens: list[Tok]) -> str | None:
        """PROCEDURE/FUNCTION name (params) [RETURN type] IS|AS decls BEGIN body END (Oracle) или
        PROCEDURE name (params) BEGIN … END (Teradata SPL)."""
        i = _name_end(tokens, 1)
        params, ok = "", True
        is_proc = kind in ("PROCEDURE", "PROC")
        if i < len(tokens) and tokens[i].text == "(":
            end = _match_paren(tokens, i)
            params, ok = self.params(ctx, tokens[i + 1:end], is_proc)
            i = end + 1
        returns = ""
        if i < len(tokens) and tokens[i].up in ("RETURN", "RETURNS"):
            j = i + 1
            while j < len(tokens) and tokens[j].up not in ("IS", "AS", "BEGIN", "DETERMINISTIC", "PIPELINED",
                                                            "PARALLEL_ENABLE", "RESULT_CACHE", "AUTHID",
                                                            "LANGUAGE", "SQL", "CONTAINS", "READS", "MODIFIES"):
                j += 1
            returns, good = ctx.type(tokens[i + 1:j])
            ok = ok and good
            i = j
        while i < len(tokens) and tokens[i].up not in ("IS", "AS", "BEGIN"):
            i += 1  # AUTHID, DETERMINISTIC, SQL SECURITY …
        if i >= len(tokens):
            return None
        if tokens[i].up in ("IS", "AS"):
            i += 1
        # Объявления до BEGIN верхнего уровня (Oracle)
        begin = next(k for k in range(i, len(tokens)) if tokens[k].up == "BEGIN")
        decl_tokens = tokens[i:begin]
        decls = []
        conv = _BlockConverter(ctx)
        for p in pieces(decl_tokens, ctx.source):
            line, good = declaration(ctx, p)
            ctx.statements += 1
            ctx.auto_statements += good
            decls.extend("    " + x for x in line.split("\n"))
        body = tokens[begin + 1:]
        # Последний END [name] [;]
        k = len(body) - 1
        while k >= 0 and body[k].text == ";":
            k -= 1
        while k >= 0 and body[k].up != "END":
            k -= 1
        conv.convert(body[:k])
        ctx.statements += 1  # заголовок
        ctx.auto_statements += ok
        return self.wrap(ctx, kind, target, params, returns, decls, conv.lines)

    def convert_macro(self, ctx: _Ctx, target: str, tokens: list[Tok]) -> str | None:
        """Макрос Teradata: MACRO name (p INTEGER, …) AS ( оператор; … ; ) -> функция PL/pgSQL."""
        i = _name_end(tokens, 1)
        params, ok = "", True
        names: list[str] = []
        if i < len(tokens) and tokens[i].text == "(":
            end = _match_paren(tokens, i)
            items = []
            for a in _split_commas(tokens[i + 1:end]):
                names.append(a[0].text)
                stop = next((k for k, t in enumerate(a) if k and t.up in ("NOT", "DEFAULT", "FORMAT", "TITLE")),
                            len(a))
                tp, good = ctx.type(a[1:stop])
                items.append(f"p_{a[0].text.lower()} {tp}")
                ok = ok and good
            params = ", ".join(items)
            i = end + 1
        while i < len(tokens) and tokens[i].up != "AS":
            i += 1
        start = i + 2  # AS (
        end = _match_paren(tokens, i + 1)
        body = tokens[start:end]
        # :p и p -> p_p: параметры макроса в теле пишутся с двоеточием
        renamed: list[Tok] = []
        lowered = {n.upper() for n in names}
        j = 0
        while j < len(body):
            t = body[j]
            if t.text == ":" and j + 1 < len(body) and body[j + 1].up in lowered:
                renamed.append(Tok("word", f"p_{body[j + 1].text.lower()}"))
                j += 2
                continue
            if t.kind == "word" and t.text.startswith(":") and t.text[1:].upper() in lowered:
                renamed.append(Tok("word", f"p_{t.text[1:].lower()}"))
                j += 1
                continue
            renamed.append(t)
            j += 1
        conv = _BlockConverter(ctx)
        selects = 0
        for p in pieces(renamed, ctx.source):
            if p[0].up in ("SELECT", "SEL") and not any(t.up == "INTO" for t in p):
                selects += 1
                conv.todo_stmt(p, "макрос возвращает строки: оформите как функцию RETURNS TABLE (…) с RETURN QUERY "
                                  "или как представление")
                continue
            conv.piece(p)
        ctx.statements += 1
        ctx.auto_statements += ok
        ctx.note("макрос стал функцией: вызов EXEC макрос(…) -> SELECT функция(…)")
        return self.wrap(ctx, "FUNCTION", target, params, "", [], conv.lines)


def wrap_routine(ctx: _Ctx, kind: str, target: str, params: str, returns: str, decls: list[str],
                 body: list[str]) -> str:
    """CREATE PROCEDURE (ADB 7) или CREATE FUNCTION (функции и ADB 6) с телом PL/pgSQL."""
    is_proc = kind in ("PROCEDURE", "PROC")
    all_decls = [*decls, *("    " + d for d in ctx.declares)]
    declare = ("DECLARE\n" + "\n".join(all_decls) + "\n") if all_decls else ""
    text = "#variable_conflict use_column\n" + declare + "BEGIN\n" + "\n".join(body) + "\nEND;"
    if is_proc and ctx.procedures:
        head = f"CREATE OR REPLACE PROCEDURE {target}({params})\nLANGUAGE plpgsql\nAS $body$\n"
    else:
        if not returns:
            returns = "void" if "OUT " not in f" {params}" else ""
        ret = f"\nRETURNS {returns}" if returns else ""
        head = f"CREATE OR REPLACE FUNCTION {target}({params}){ret}\nLANGUAGE plpgsql\nAS $body$\n"
    return head + text + "\n$body$;\n"


def _package_members(tokens: list[Tok]):
    """Члены тела пакета: (PROCEDURE|FUNCTION, имя, лексемы от PROCEDURE до END имя;)."""
    i = 0
    while i < len(tokens) and tokens[i].up not in ("IS", "AS"):
        i += 1
    i += 1
    while i < len(tokens):
        t = tokens[i]
        if t.up in ("PROCEDURE", "FUNCTION"):
            start = i
            name = _clean(tokens[i + 1].text)
            # предварительное объявление «PROCEDURE p(…);» — без тела
            j = i + 2
            if j < len(tokens) and tokens[j].text == "(":
                j = _match_paren(tokens, j) + 1
            while j < len(tokens) and tokens[j].up not in ("IS", "AS", ";"):
                j += 1
            if j < len(tokens) and tokens[j].text == ";":
                i = j + 1
                continue
            depth = 0
            k = j
            while k < len(tokens):
                up = tokens[k].up
                if up in ("BEGIN",):
                    depth += 1
                elif up == "CASE" and not (k and tokens[k - 1].up == "END"):
                    depth += 1
                elif up == "END" and k + 1 < len(tokens) and tokens[k + 1].up in ("IF", "LOOP"):
                    pass
                elif up == "END":
                    depth -= 1
                    if depth == 0:
                        while k < len(tokens) and tokens[k].text != ";":
                            k += 1
                        break
                k += 1
            yield t.up, name, tokens[start:k + 1]
            i = k + 1
            continue
        i += 1


def _label(info: str) -> str:
    """«макрос (конструкции Teradata: …), строк: 6» -> «макрос»."""
    m = re.match(r"^[^(,]+", info or "")
    return m.group().strip() if m else "процедура"


def _uniq(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def translate_routines(model: Model, dialect: str, names: NameMap | None = None) -> list[RoutineTranslation]:
    return RoutineSet(model, dialect, names).translate_all()


__all__ = ["RoutineSet", "RoutineTranslation", "pieces", "tokenize", "translate_routines"]
