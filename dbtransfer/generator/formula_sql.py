"""Перевод формулы трансформации BW в выражение SQL (ТЗ, приложение В).

Разбор — рекурсивный спуск по приоритетам: OR, AND, NOT, сравнения, + -, * /, унарный минус.
Поля формулы подставляет вызывающий через resolve(имя) -> (SQL, поле) или None.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from dbtransfer.generator.types import NUMERIC_TYPES
from dbtransfer.model import FieldDef

Resolver = Callable[[str], tuple[str, FieldDef] | None]


class FormulaError(Exception):
    """Формулу нельзя перевести автоматически; текст понятен пользователю."""


_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<string>'(?:[^']|'')*')
  | (?P<ident>(?:/BIC/|/BI0/)?\d*[A-Za-z_][A-Za-z0-9_]*)
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<op><>|<=|>=|=|<|>|\+|-|\*|/|\(|\)|;|,)
""", re.VERBOSE | re.IGNORECASE)

# Приоритеты: чем больше, тем сильнее связывает
P_OR, P_AND, P_NOT, P_CMP, P_ADD, P_MUL, P_UNARY, P_ATOM = range(1, 9)
COMPARISONS = {"=", "<>", "<", ">", "<=", ">="}


@dataclass
class Token:
    kind: str
    text: str
    pos: int


def tokenize(formula: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    while pos < len(formula):
        m = _TOKEN.match(formula, pos)
        if not m:
            raise FormulaError(f"непонятный символ «{formula[pos]}» в позиции {pos + 1}")
        if m.lastgroup != "space":
            tokens.append(Token(m.lastgroup or "", m.group(), pos))
        pos = m.end()
    return tokens


class _Parser:
    def __init__(self, formula: str, resolve: Resolver):
        self.tokens = tokenize(formula)
        self.i = 0
        self.resolve = resolve

    # --- навигация ---
    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def word(self) -> str:
        t = self.peek()
        return t.text.upper() if t and t.kind == "ident" else ""

    def take(self, text: str | None = None) -> Token:
        t = self.peek()
        if t is None:
            raise FormulaError("формула оборвалась" + (f": ожидалось «{text}»" if text else ""))
        if text is not None and t.text.upper() != text:
            raise FormulaError(f"ожидалось «{text}», а стоит «{t.text}» (позиция {t.pos + 1})")
        self.i += 1
        return t

    @staticmethod
    def wrap(part: tuple[str, int], prec: int) -> str:
        sql, p = part
        return sql if p >= prec else f"({sql})"

    # --- грамматика ---
    def parse(self) -> str:
        sql, _ = self.or_()
        t = self.peek()
        if t is not None:
            raise FormulaError(f"лишнее «{t.text}» в позиции {t.pos + 1}")
        return sql

    def or_(self) -> tuple[str, int]:
        left = self.and_()
        while self.word() == "OR":
            self.take()
            right = self.and_()
            left = (f"{self.wrap(left, P_OR)} OR {self.wrap(right, P_OR + 1)}", P_OR)
        return left

    def and_(self) -> tuple[str, int]:
        left = self.not_()
        while self.word() == "AND":
            self.take()
            right = self.not_()
            left = (f"{self.wrap(left, P_AND)} AND {self.wrap(right, P_AND + 1)}", P_AND)
        return left

    def not_(self) -> tuple[str, int]:
        if self.word() == "NOT":
            self.take()
            return (f"NOT {self.wrap(self.not_(), P_NOT)}", P_NOT)
        return self.cmp()

    def cmp(self) -> tuple[str, int]:
        left = self.add()
        t = self.peek()
        if t and t.text in COMPARISONS:
            self.take()
            right = self.add()
            return (f"{self.wrap(left, P_CMP + 1)} {t.text} {self.wrap(right, P_CMP + 1)}", P_CMP)
        return left

    def add(self) -> tuple[str, int]:
        left = self.mul()
        while (t := self.peek()) and t.text in "+-" and t.kind == "op":
            self.take()
            right = self.mul()
            left = (f"{self.wrap(left, P_ADD)} {t.text} {self.wrap(right, P_ADD + 1)}", P_ADD)
        return left

    def mul(self) -> tuple[str, int]:
        left = self.unary()
        while (t := self.peek()) and t.text in ("*", "/"):
            self.take()
            right = self.unary()
            if t.text == "/":
                # Деление на ноль в BW даёт 0 или ошибку по настройке; в прототипе — NULL
                left = (f"{self.wrap(left, P_MUL)} / NULLIF({right[0]}, 0)", P_MUL)
            else:
                left = (f"{self.wrap(left, P_MUL)} * {self.wrap(right, P_MUL + 1)}", P_MUL)
        return left

    def unary(self) -> tuple[str, int]:
        t = self.peek()
        if t and t.kind == "op" and t.text in "+-":
            self.take()
            inner = self.unary()
            return (f"{t.text}{self.wrap(inner, P_UNARY)}", P_UNARY)
        return self.primary()

    def primary(self) -> tuple[str, int]:
        t = self.take()
        if t.kind == "number":
            return (t.text, P_ATOM)
        if t.kind == "string":
            return (t.text, P_ATOM)
        if t.text == "(":
            inner = self.or_()
            self.take(")")
            return (f"({inner[0]})", P_ATOM)
        if t.kind == "ident":
            nxt = self.peek()
            if nxt is not None and nxt.text == "(":
                return self.call(t)
            if t.text.upper() in ("AND", "OR", "NOT"):
                raise FormulaError(f"«{t.text}» без операнда в позиции {t.pos + 1}")
            resolved = self.resolve(t.text)
            if resolved is None:
                raise FormulaError(f"поле {t.text} не найдено в источнике трансформации")
            return (resolved[0], P_ATOM)
        raise FormulaError(f"неожиданное «{t.text}» в позиции {t.pos + 1}")

    def args(self) -> list[tuple[str, int]]:
        self.take("(")
        result = [self.or_()]
        while (t := self.peek()) and t.text in (";", ","):
            self.take()
            result.append(self.or_())
        self.take(")")
        return result

    def call(self, name_token: Token) -> tuple[str, int]:
        name = name_token.text.upper()
        if name == "IS_INITIAL":
            return self.is_initial()
        args = [a[0] for a in self.args()]
        expected = {"IF": 3, "CONCATENATE": None, "SUBSTRING": 3, "STRLEN": 1, "TOUPPER": 1, "ABS": 1,
                    "ROUND": 2, "DATE_YEAR": 1, "DATE_MONTH": 1}
        if name not in expected:
            raise FormulaError(f"функция {name_token.text} не поддерживается (словарь функций — приложение В ТЗ)")
        count = expected[name]
        if count is not None and len(args) != count:
            raise FormulaError(f"у функции {name} должно быть аргументов: {count}, а передано {len(args)}")
        if name == "IF":
            sql = f"CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2]} END"
        elif name == "CONCATENATE":
            sql = f"concat({', '.join(args)})"
        elif name == "SUBSTRING":
            sql = f"substr({args[0]}, {args[1]} + 1, {args[2]})"
        elif name == "STRLEN":
            sql = f"length({args[0]})"
        elif name == "TOUPPER":
            sql = f"upper({args[0]})"
        elif name == "ABS":
            sql = f"abs({args[0]})"
        elif name == "ROUND":
            sql = f"round({args[0]}, {args[1]})"
        elif name == "DATE_YEAR":
            sql = f"coalesce(to_char({args[0]}, 'YYYY'), '0000')"
        else:
            sql = f"coalesce(to_char({args[0]}, 'YYYYMM'), '000000')"
        return (sql, P_ATOM)

    def is_initial(self) -> tuple[str, int]:
        self.take("(")
        t = self.take()
        nxt = self.peek()
        resolved = self.resolve(t.text) if t.kind == "ident" and nxt is not None and nxt.text == ")" else None
        if resolved is None:
            raise FormulaError("IS_INITIAL переводится только для поля источника")
        self.take(")")
        sql, f = resolved
        if f.datatype in NUMERIC_TYPES:
            check = f"{sql} IS NULL OR {sql} = 0"
        elif f.datatype in ("DATS", "TIMS"):
            check = f"{sql} IS NULL"
        elif f.datatype == "NUMC":
            check = f"{sql} IS NULL OR {sql} = '' OR {sql} = '{'0' * max(f.length, 1)}'"
        else:
            check = f"{sql} IS NULL OR {sql} = ''"
        return (f"({check})", P_ATOM)


def translate(formula: str, resolve: Resolver) -> str:
    """Формула BW -> выражение SQL. FormulaError, если перевести нельзя."""
    if not formula or not formula.strip():
        raise FormulaError("пустая формула")
    return _Parser(formula, resolve).parse()
