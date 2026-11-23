"""T-SQL (процедуры и функции MS SQL Server) -> PL/pgSQL. В T-SQL операторы не обязаны кончаться «;»,
поэтому границы операторов ищутся по ключевым словам (IF, SET, SELECT, INSERT …) на верхнем уровне скобок.

Имена: параметр @CustomerId -> p_customerid, переменная @Total -> v_total, временная таблица #Tmp -> tmp_tmp.
"""

from __future__ import annotations

import copy

from dbtransfer.translate.names import Relation
from dbtransfer.translate.routines import Tok, _commented, _Ctx, _match_paren, _split_commas, join, wrap_routine

_STARTS = {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "SET", "DECLARE", "IF", "ELSE", "WHILE", "BEGIN",
           "END", "RETURN", "PRINT", "EXEC", "EXECUTE", "RAISERROR", "THROW", "TRUNCATE", "BREAK", "CONTINUE",
           "COMMIT", "ROLLBACK", "CREATE", "DROP", "OPEN", "FETCH", "CLOSE", "DEALLOCATE", "WITH", "GOTO",
           "WAITFOR"}
_SKIP_SET = {"NOCOUNT", "XACT_ABORT", "ANSI_NULLS", "QUOTED_IDENTIFIER", "ANSI_WARNINGS", "ARITHABORT",
             "TRANSACTION", "DATEFIRST", "LANGUAGE", "DEADLOCK_PRIORITY", "LOCK_TIMEOUT", "ROWCOUNT",
             "CONCAT_NULL_YIELDS_NULL", "IMPLICIT_TRANSACTIONS", "FMTONLY"}


class TsqlConverter:
    def __init__(self, ctx: _Ctx):
        ctx.names = copy.copy(ctx.names)  # временные таблицы видны только в своей процедуре
        ctx.names.relations = dict(ctx.names.relations)
        self.ctx = ctx
        self.params: dict[str, str] = {}
        self.vars: dict[str, str] = {}
        self.decls: list[str] = []
        self.toks: list[Tok] = []
        self.pos = 0
        self.temp: set[str] = set()
        self.is_function = False

    # --- имена ---
    def name(self, word: str) -> str:
        up = word.upper()
        if up in self.params:
            return self.params[up]
        if up not in self.vars:
            self.vars[up] = "v_" + word.lstrip("@").lower()
        return self.vars[up]

    def rename(self, tokens: list[Tok]) -> list[Tok]:
        """@переменные, #временные таблицы и системные функции T-SQL -> имена PL/pgSQL."""
        out: list[Tok] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            up = t.up
            if t.kind == "word" and up.startswith("@@"):
                if up == "@@ROWCOUNT":
                    self.ctx.declare("_rowcount integer;")
                    out.append(Tok("word", "_rowcount"))
                elif up == "@@FETCH_STATUS":
                    # @@FETCH_STATUS = 0 -> FOUND, <> 0 -> NOT FOUND
                    if i + 2 < len(tokens) and tokens[i + 2].text == "0" and tokens[i + 1].text in ("=", "<>", "!="):
                        out.extend([Tok("word", "NOT"), Tok("word", "FOUND")] if tokens[i + 1].text != "="
                                   else [Tok("word", "FOUND")])
                        i += 3
                        continue
                    self.ctx.todos.append("@@FETCH_STATUS: используйте FOUND после FETCH")
                    out.append(Tok("word", "FOUND"))
                else:
                    self.ctx.todos.append(f"{t.text} не переведён")
                    out.append(Tok("word", t.text.lstrip("@").lower()))
            elif t.kind == "word" and up.startswith("@"):
                out.append(Tok("word", self.name(t.text)))
            elif t.kind == "word" and up.startswith("#"):
                name = "tmp_" + t.text.lstrip("#").lower()
                if name not in self.temp:
                    self.temp.add(name)
                    self.ctx.names.add("#", name, Relation(t.text, name, kind="temp"))
                out.append(Tok("word", name))
            elif t.kind == "word" and up in ("ERROR_MESSAGE", "ERROR_NUMBER", "ERROR_STATE", "ERROR_SEVERITY",
                                             "ERROR_LINE", "ERROR_PROCEDURE", "SCOPE_IDENTITY") \
                    and i + 2 < len(tokens) and tokens[i + 1].text == "(" and tokens[i + 2].text == ")":
                mapped = {"ERROR_MESSAGE": "SQLERRM", "ERROR_NUMBER": "SQLSTATE"}.get(up)
                if mapped is None:
                    self.ctx.todos.append(f"{t.text}() не переведена")
                    mapped = "NULL"
                out.append(Tok("word", mapped))
                i += 3
                continue
            else:
                out.append(t)
            i += 1
        return out

    # --- границы операторов ---
    def at_start(self, i: int) -> bool:
        t = self.toks[i]
        if t.kind != "word" or t.up not in _STARTS:
            return False
        if t.up == "WITH":
            nxt = self.toks[i + 1] if i + 1 < len(self.toks) else None
            return nxt is not None and nxt.kind == "word" and nxt.up not in ("NOLOCK", "ROLLUP", "CUBE", "TIES")
        return True

    def statement_end(self, start: int) -> int:
        """Индекс за последней лексемой оператора, начатого в start."""
        head = self.toks[start].up
        depth = case = 0
        seen_set = head != "UPDATE"
        cursor = head == "DECLARE" and start + 2 < len(self.toks) and self.toks[start + 2].up == "CURSOR"
        seen_query = head not in ("INSERT", "WITH") and not cursor
        i = start + 1
        while i < len(self.toks):
            t = self.toks[i]
            up = t.up
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
            elif t.text == ";" and depth == 0:
                return i
            elif depth == 0 and t.kind == "word":
                if up == "CASE":
                    case += 1
                elif up == "END" and case:
                    case -= 1
                elif head == "MERGE":
                    pass
                elif up == "SET" and not seen_set:
                    seen_set = True
                elif up in ("SELECT", "INSERT", "UPDATE", "DELETE", "VALUES") and not seen_query:
                    seen_query = True
                elif up == "SELECT" and self.toks[i - 1].up in ("UNION", "ALL", "EXCEPT", "INTERSECT"):
                    pass
                elif not case and self.at_start(i):
                    return i
            i += 1
        return i

    def cond_end(self, start: int) -> int:
        """Конец условия IF / WHILE: первое начало оператора на верхнем уровне."""
        depth = case = 0
        i = start
        while i < len(self.toks):
            t = self.toks[i]
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
            elif depth == 0 and t.up == "CASE":
                case += 1
            elif depth == 0 and t.up == "END" and case:
                case -= 1
            elif depth == 0 and not case and self.at_start(i):
                return i
            i += 1
        return i

    # --- разбор ---
    def block_until(self, *ends: tuple[str, ...]) -> list[str]:
        lines: list[str] = []
        while self.pos < len(self.toks):
            if self.toks[self.pos].text == ";":
                self.pos += 1
                continue
            if any(self._at(*e) for e in ends):
                return lines
            lines.extend(self.statement())
        return lines

    def _at(self, *words: str) -> bool:
        return all(self.pos + k < len(self.toks) and self.toks[self.pos + k].up == w for k, w in enumerate(words))

    def count(self, ok: bool) -> None:
        self.ctx.statements += 1
        self.ctx.auto_statements += ok

    def todo(self, tokens: list[Tok], why: str) -> list[str]:
        self.count(False)
        return [self.ctx.todo(why), *_commented(join(tokens))]

    def statement(self) -> list[str]:  # noqa: C901 — разбор по первому слову оператора
        ctx = self.ctx
        start = self.pos
        t = self.toks[start]
        head = t.up
        if head == "BEGIN":
            if self._at("BEGIN", "TRY"):
                self.pos += 2
                body = self.block_until(("END", "TRY"))
                self.pos += 2
                catch: list[str] = []
                if self._at("BEGIN", "CATCH"):
                    self.pos += 2
                    catch = self.block_until(("END", "CATCH"))
                    self.pos += 2
                self.count(True)
                return ["BEGIN", *_ind(body), "EXCEPTION", "    WHEN OTHERS THEN", *_ind(catch or ["NULL;"], 2),
                        "END;"]
            if self._at("BEGIN", "TRAN") or self._at("BEGIN", "TRANSACTION"):
                end = self.statement_end(start + 1)
                self.pos = end
                ctx.note("BEGIN TRAN убран: процедура и так выполняется в транзакции вызова")
                return []
            self.pos += 1
            body = self.block_until(("END",))
            self.pos += 1
            return body
        if head == "IF":
            cond_end = self.cond_end(start + 1)
            cond_tokens = self.toks[start + 1:cond_end]
            self.pos = cond_end
            # IF OBJECT_ID('tempdb..#t') IS NOT NULL DROP TABLE #t -> DROP TABLE IF EXISTS
            if cond_tokens and cond_tokens[0].up == "OBJECT_ID" and self._at("DROP"):
                return self.statement()
            c, ok = ctx.expr(self.rename(cond_tokens))
            self.count(ok)
            then = self.statement()
            lines = [f"IF {c} THEN", *_ind(then or ["NULL;"])]
            while self.pos < len(self.toks) and self.toks[self.pos].text == ";":
                self.pos += 1
            if self._at("ELSE"):
                self.pos += 1
                other = self.statement()
                if len(other) >= 1 and other[0].startswith("IF ") and other[-1] == "END IF;":
                    lines.append("ELS" + other[0])  # ELSE IF -> ELSIF
                    lines.extend(other[1:-1])
                else:
                    lines.extend(["ELSE", *_ind(other or ["NULL;"])])
            lines.append("END IF;")
            return lines
        if head == "WHILE":
            cond_end = self.cond_end(start + 1)
            c, ok = ctx.expr(self.rename(self.toks[start + 1:cond_end]))
            self.count(ok)
            self.pos = cond_end
            body = self.statement()
            return [f"WHILE {c} LOOP", *_ind(body or ["NULL;"]), "END LOOP;"]
        end = self.statement_end(start)
        tokens = self.toks[start:end]
        self.pos = end
        if head == "ELSE" or head == "END":
            return self.todo(tokens, "непарные ELSE/END")
        if head == "DECLARE":
            return self.declare(tokens[1:])
        if head == "SET":
            if len(tokens) > 1 and tokens[1].up in _SKIP_SET:
                ctx.note(f"SET {tokens[1].text} убран: в PostgreSQL не нужен")
                return []
            rest = self.rename(tokens[1:])
            op = next((i for i, x in enumerate(rest) if x.text in ("=", "+", "-", "*", "/")), None)
            if op is None or not rest:
                return self.todo(tokens, "SET не разобран")
            target = rest[0].text
            if rest[op].text != "=" and op + 1 < len(rest) and rest[op + 1].text == "=":  # SET @x += 1
                e, ok = ctx.expr([Tok("word", target), rest[op], Tok("op", "("), *rest[op + 2:], Tok("op", ")")])
            else:
                e, ok = ctx.expr(rest[op + 1:])
            self.count(ok)
            return [f"{target} := {e};"]
        if head == "SELECT":
            return self.select(tokens)
        if head in ("INSERT", "UPDATE", "DELETE", "MERGE", "WITH", "TRUNCATE"):
            return self.sql(tokens)
        if head == "CREATE" and len(tokens) > 2 and tokens[1].up == "TABLE" and tokens[2].text.startswith("#"):
            renamed = self.rename(tokens)
            lines, ok = ctx.sql(renamed, False)
            self.count(ok)
            ctx.note("временные таблицы #t стали CREATE TEMP TABLE tmp_t")
            return [lines[0].replace("CREATE TABLE", "CREATE TEMP TABLE", 1), *lines[1:]]
        if head == "DROP" and len(tokens) > 2 and tokens[1].up == "TABLE":
            names = [x.text for x in self.rename(tokens[2:]) if x.kind == "word" and x.up not in ("IF", "EXISTS")]
            self.count(True)
            return [f"DROP TABLE IF EXISTS {', '.join(n.lower() for n in names)};"]
        if head == "PRINT":
            e, ok = ctx.expr(self.rename(tokens[1:]))
            self.count(ok)
            return [f"RAISE NOTICE '%', {e};"]
        if head == "RAISERROR":
            args = tokens[2:_match_paren(tokens, 1)] if len(tokens) > 1 and tokens[1].text == "(" else tokens[1:]
            parts = _split_commas(args)
            e, ok = ctx.expr(self.rename(parts[0])) if parts else ("'ошибка'", False)
            self.count(ok)
            if len(parts) > 3:
                ctx.todos.append("RAISERROR с подстановками %s/%d: перепишите через format()")
            return [f"RAISE EXCEPTION '%', {e};"]
        if head == "THROW":
            if len(tokens) == 1:
                self.count(True)
                return ["RAISE;"]
            parts = _split_commas(tokens[1:])
            e, ok = ctx.expr(self.rename(parts[1])) if len(parts) > 1 else ("'ошибка'", False)
            self.count(ok)
            return [f"RAISE EXCEPTION '%', {e} USING DETAIL = 'код T-SQL {join(parts[0])}';"]
        if head == "RETURN":
            self.count(True)
            if len(tokens) == 1:
                return ["RETURN;"]
            e, ok = ctx.expr(self.rename(tokens[1:]))
            if not self.is_function:
                ctx.todos.append(f"RETURN {e}: код возврата процедуры в PostgreSQL не передаётся, используйте "
                                 "INOUT-параметр")
                return ["-- TODO: код возврата процедуры", "RETURN;"]
            return [f"RETURN {e};"]
        if head == "BREAK":
            self.count(True)
            return ["EXIT;"]
        if head == "CONTINUE":
            self.count(True)
            return ["CONTINUE;"]
        if head in ("COMMIT", "ROLLBACK"):
            if ctx.procedures:
                ctx.note("COMMIT/ROLLBACK в процедуре: в ADB 7 работает при вызове CALL вне транзакции и не "
                         "внутри блока с EXCEPTION")
                self.count(True)
                return [f"{head};"]
            return self.todo(tokens, f"{head} внутри функции ADB 6 невозможен: транзакцией управляет вызывающий")
        if head in ("EXEC", "EXECUTE"):
            return self.exec(tokens)
        if head == "OPEN":
            self.count(True)
            return [f"OPEN {join(self.rename(tokens[1:])).lower()};"]
        if head == "FETCH":
            rest = self.rename(tokens[1:])
            if rest and rest[0].up in ("NEXT", "PRIOR", "FIRST", "LAST"):
                rest = rest[1:]
            if rest and rest[0].up == "FROM":
                rest = rest[1:]
            self.count(True)
            return [f"FETCH {join(rest).lower()};"]
        if head == "CLOSE":
            self.count(True)
            return [f"CLOSE {join(tokens[1:]).lower()};"]
        if head == "DEALLOCATE":
            return []
        if head == "GOTO":
            return self.todo(tokens, "GOTO в PL/pgSQL нет: перепишите через циклы и EXIT")
        return self.todo(tokens, "оператор не переведён")

    def declare(self, tokens: list[Tok]) -> list[str]:
        ctx = self.ctx
        lines: list[str] = []
        if len(tokens) > 2 and tokens[1].up == "CURSOR":
            k = next((i for i, x in enumerate(tokens) if x.up == "FOR"), None)
            if k is None:
                return self.todo(tokens, "курсор не разобран")
            sql, ok = ctx.sql(self.rename(tokens[k + 1:]), False)
            self.count(ok)
            body = "\n        ".join(x.rstrip(";") for x in sql)
            self.decls.append(f"    {tokens[0].text.lower()} CURSOR FOR\n        {body};")
            return []
        for item in _split_commas(tokens):
            if not item or not item[0].text.startswith("@"):
                lines.extend(self.todo(item, "DECLARE не разобран"))
                continue
            var = self.name(item[0].text)
            rest = item[1:]
            if rest and rest[0].up == "AS":
                rest = rest[1:]
            if rest and rest[0].up == "TABLE":
                lines.extend(self.todo(item, "табличная переменная: замените временной таблицей"))
                continue
            eq = next((i for i, x in enumerate(rest) if x.text == "="), None)
            tp, ok = ctx.type(rest[:eq] if eq is not None else rest)
            self.decls.append(f"    {var} {tp};")
            if eq is not None:
                e, ok2 = ctx.expr(self.rename(rest[eq + 1:]))
                lines.append(f"{var} := {e};")
                ok = ok and ok2
            self.count(ok)
        return lines

    def select(self, tokens: list[Tok]) -> list[str]:
        """SELECT @a = x, @b = y FROM … -> SELECT x, y FROM … INTO v_a, v_b; SELECT … INTO #t -> CREATE TEMP."""
        ctx = self.ctx
        depth = 0
        from_at = len(tokens)
        into_at = None
        for i, x in enumerate(tokens):
            if x.text == "(":
                depth += 1
            elif x.text == ")":
                depth -= 1
            elif depth == 0 and x.up == "FROM":
                from_at = i
                break
            elif depth == 0 and x.up == "INTO":
                into_at = i
        if into_at is not None and tokens[into_at + 1].text.startswith("#"):
            table = self.rename([tokens[into_at + 1]])[0].text
            query = tokens[:into_at] + tokens[into_at + 2:]
            lines, ok = ctx.sql(self.rename(query), False)
            self.count(ok)
            ctx.note("SELECT … INTO #t стал CREATE TEMP TABLE … AS")
            return [f"CREATE TEMP TABLE {table} AS", *lines]
        items = _split_commas(tokens[1:from_at])
        if items and all(len(it) > 2 and it[0].text.startswith("@") and it[1].text == "=" for it in items):
            targets = [self.name(it[0].text) for it in items]
            values = [Tok("op", ",") if k else None for k in range(len(items))]
            query = [tokens[0]]
            for k, it in enumerate(items):
                if values[k]:
                    query.append(values[k])
                query.extend(it[2:])
            query.extend(tokens[from_at:])
            renamed = self.rename(query)
            if from_at == len(tokens):  # SELECT @a = 1, @b = 2 без FROM: присваивания
                parts = _split_commas(renamed[1:])
                out, ok = [], True
                for target, part in zip(targets, parts, strict=False):
                    e, good = ctx.expr(part)
                    out.append(f"{target} := {e};")
                    ok = ok and good
                self.count(ok)
                return out
            lines, ok = ctx.sql(renamed, False)
            self.count(ok)
            ctx.note("SELECT @v = … переведён в SELECT … INTO: без строк переменные станут NULL, в T-SQL "
                     "остаются прежними")
            return [*lines[:-1], lines[-1].rstrip(";"), f"INTO {', '.join(targets)};"]
        return self.todo(tokens, "SELECT без присваивания возвращает строки вызывающему: сделайте функцию "
                                 "RETURNS TABLE (…) с RETURN QUERY или представление")

    def sql(self, tokens: list[Tok]) -> list[str]:
        renamed = self.rename(tokens)
        if renamed[0].up == "TRUNCATE":
            names = [x.text for x in renamed[2:] if x.text != "."]
            rel = self.ctx.names.resolve(names[-2] if len(names) > 1 else None, names[-1], self.ctx.schema) \
                if names else None
            if rel is None:
                return self.todo(tokens, "TRUNCATE: таблицы нет в модели")
            self.count(True)
            return [f"TRUNCATE TABLE {rel.target};"]
        lines, ok = self.ctx.sql(renamed, False)
        self.count(ok)
        return lines

    def exec(self, tokens: list[Tok]) -> list[str]:
        ctx = self.ctx
        rest = tokens[1:]
        if rest and rest[0].text == "(" or (rest and rest[0].up in ("SP_EXECUTESQL", "DBO.SP_EXECUTESQL")):
            arg = rest[1:-1] if rest[0].text == "(" else _split_commas(rest[1:])[0] if len(rest) > 1 else []
            e, _ = ctx.expr(self.rename(arg))
            self.count(False)
            return [ctx.todo("динамический SQL: текст запроса — на диалекте T-SQL, проверьте его"), f"EXECUTE {e};"]
        if len(rest) > 2 and rest[0].text.startswith("@") and rest[1].text == "=":
            ctx.todos.append("код возврата EXEC @rc = …: в PostgreSQL процедура его не возвращает")
            rest = rest[2:]
        k = 1
        while k + 1 < len(rest) and rest[k].text == "." and rest[k + 1].kind in ("word", "qident"):
            k += 2
        name = [Tok(x.kind, x.text.strip("[]")) for x in rest[:k]]
        args = []
        for a in _split_commas(rest[k:]):
            a = [x for x in a if x.up not in ("OUTPUT", "OUT")]
            if len(a) > 2 and a[0].text.startswith("@") and a[1].text == "=":
                callee_param = "p_" + a[0].text.lstrip("@").lower()
                args.extend([Tok("word", callee_param), Tok("op", "=>"), *self.rename(a[2:]), Tok("op", ",")])
            else:
                args.extend([*self.rename(a), Tok("op", ",")])
        if args:
            args = args[:-1]
        line, ok = ctx.call(name, args)
        self.count(ok)
        return [line]

    # --- объект целиком ---
    def convert(self, kind: str, target: str, tokens: list[Tok]) -> str | None:
        ctx = self.ctx
        self.is_function = kind == "FUNCTION"
        i = 1
        while i + 1 < len(tokens) and tokens[i + 1].text == ".":
            i += 2
        i += 1
        params_end = i
        while params_end < len(tokens) and tokens[params_end].up not in ("AS", "RETURNS", "WITH"):
            params_end += 1
        param_tokens = tokens[i:params_end]
        if param_tokens and param_tokens[0].text == "(" and _match_paren(param_tokens, 0) == len(param_tokens) - 1:
            param_tokens = param_tokens[1:-1]
        params, ok = [], True
        for a in _split_commas(param_tokens):
            if not a:
                continue
            pname = "p_" + a[0].text.lstrip("@").lower()
            self.params[a[0].up] = pname
            rest = [x for x in a[1:] if x.up not in ("READONLY",)]
            mode = ""
            if rest and rest[-1].up in ("OUTPUT", "OUT"):
                mode = "INOUT "  # в T-SQL OUTPUT-параметр читается и пишется
                rest = rest[:-1]
            if rest and rest[0].up == "AS":
                rest = rest[1:]
            eq = next((k for k, x in enumerate(rest) if x.text == "="), None)
            tp, good = ctx.type(rest[:eq] if eq is not None else rest)
            item = f"{mode}{pname} {tp}"
            if eq is not None:
                e, good2 = ctx.expr(rest[eq + 1:])
                item += f" DEFAULT {e}"
                good = good and good2
            params.append(item)
            ok = ok and good
        seen = False
        for k, p in enumerate(params):  # в PostgreSQL после параметра со значением по умолчанию — тоже
            if " DEFAULT " in p:
                seen = True
            elif seen:
                params[k] = p + " DEFAULT NULL"
        returns = ""
        j = params_end
        if j < len(tokens) and tokens[j].up == "RETURNS":
            k = j + 1
            while k < len(tokens) and tokens[k].up not in ("AS", "WITH", "BEGIN"):
                k += 1
            if tokens[j + 1].up == "TABLE" or tokens[j + 1].text.startswith("@"):
                ctx.todos.append("табличная функция T-SQL: оформите как RETURNS TABLE (…) с RETURN QUERY")
                return None
            returns, good = ctx.type(tokens[j + 1:k])
            ok = ok and good
            j = k
        while j < len(tokens) and tokens[j].up != "AS":
            j += 1  # WITH EXECUTE AS, WITH SCHEMABINDING
        self.toks = tokens[j + 1:]
        self.pos = 0
        if self.toks and self.toks[0].up == "BEGIN" and not (len(self.toks) > 1 and self.toks[1].up in ("TRY",
                                                                                                         "TRAN")):
            self.pos = 1
            body = self.block_until(("END",))
        else:
            body = self.block_until()
        self.count(ok)
        return wrap_routine(ctx, "FUNCTION" if kind == "FUNCTION" else "PROCEDURE", target, ", ".join(params),
                    returns, self.decls, ["    " + x for x in body])


def _ind(lines: list[str], n: int = 1) -> list[str]:
    return ["    " * n + x for x in lines]


def convert_tsql(ctx: _Ctx, kind: str, target: str, tokens: list[Tok]) -> str | None:
    if kind == "TRIGGER":
        ctx.todos.append("триггер переводится вручную: в Greenplum логику триггера переносят в процедуры загрузки")
        return None
    if kind == "PROC":
        kind = "PROCEDURE"
    return TsqlConverter(ctx).convert(kind, target, tokens)
