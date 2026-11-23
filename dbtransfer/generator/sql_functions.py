"""Выражения SQL, которые пишутся по-разному в PostgreSQL/Greengage и ClickHouse.

Трансформации и формулы строятся одним кодом, а функции дат, строк и параметр номера загрузки
берутся из объекта диалекта. PostgresSql даёт тот же текст, что был до появления ClickHouse,
поэтому эталонные файлы Greengage не меняются.
"""

from __future__ import annotations

from dbtransfer.generator.sql import literal
from dbtransfer.generator.types import NUMERIC_TYPES, TypeRules
from dbtransfer.model import FieldDef

# Форматы преобразования времени BW: ключ -> формат to_char в PostgreSQL
PG_FORMATS = {"YYYYMMDD": "YYYYMMDD", "YYYYMM": "YYYYMM", "YYYY": "YYYY", "YYYYQ": "YYYYQ", "MM": "MM",
              "IYYYIW": "IYYYIW"}


class PostgresSql:
    """PostgreSQL и Greengage: функции PL/pgSQL, параметр p_load_id."""

    name = "postgres"
    load_id = "p_load_id"
    # Выражения, которые уже не NULL: их не нужно оборачивать в coalesce для колонок NOT NULL
    not_null_prefixes: tuple[str, ...] = ("'", "coalesce(", "DATE ")

    def literal(self, value: str) -> str:
        return literal(value)

    def date_to_text(self, expr: str, fmt: str) -> str:
        """Дата -> строка по формату BW (ключ PG_FORMATS); NULL остаётся NULL."""
        return f"to_char({expr}, '{PG_FORMATS[fmt]}')"

    def text_to_date(self, expr: str) -> str:
        """Строка ГГГГММДД -> дата; пустая строка и 00000000 дают NULL."""
        return f"to_date(nullif(nullif({expr}, ''), '00000000'), 'YYYYMMDD')"

    def date_literal(self, iso: str) -> str:
        return f"DATE '{iso}'"

    def substr(self, expr: str, start0: str, length: str) -> str:
        """Подстрока с позиции start0, считая с нуля, как в формулах BW."""
        return f"substr({expr}, {start0} + 1, {length})"

    def strlen(self, expr: str) -> str:
        return f"length({expr})"

    def upper(self, expr: str) -> str:
        return f"upper({expr})"

    def initial_value(self, rules: TypeRules, f: FieldDef) -> str:
        return rules.initial_value(f)


class ClickHouseSql(PostgresSql):
    """ClickHouse: параметр запроса {p_load_id:Int64}, функции дат и строк ClickHouse.

    Строковые функции — варианты UTF8, иначе длина и подстрока считаются в байтах и ломаются на кириллице.
    """

    name = "clickhouse"
    load_id = "{p_load_id:Int64}"
    not_null_prefixes = ("'", "coalesce(", "toDate32(")
    CH_FORMATS = {"YYYYMMDD": "%Y%m%d", "YYYYMM": "%Y%m", "YYYY": "%Y", "MM": "%m"}

    def __init__(self, date_key_placeholder: str = "1900-01-01"):
        self.date_key_placeholder = date_key_placeholder

    def literal(self, value: str) -> str:
        return ch_literal(value)

    def date_to_text(self, expr: str, fmt: str) -> str:
        if fmt == "YYYYQ":
            return f"concat(toString(toYear({expr})), toString(toQuarter({expr})))"
        if fmt == "IYYYIW":
            # Режим 3 — неделя по ISO 8601, как IYYYIW в PostgreSQL
            return f"toString(toYearWeek({expr}, 3))"
        return f"formatDateTime({expr}, '{self.CH_FORMATS[fmt]}')"

    def text_to_date(self, expr: str) -> str:
        # ГГГГММДД -> ГГГГ-ММ-ДД: toDate32OrNull понимает только ISO; ошибочная дата тоже даёт NULL.
        # Пустую дату убираем заранее: 0000-00-00 ClickHouse не отвергает, а сдвигает к нижней границе
        x = f"nullIf(nullIf({expr}, ''), '00000000')"
        return f"toDate32OrNull(concat(substring({x}, 1, 4), '-', substring({x}, 5, 2), '-', substring({x}, 7, 2)))"

    def date_literal(self, iso: str) -> str:
        return f"toDate32('{iso}')"

    def substr(self, expr: str, start0: str, length: str) -> str:
        return f"substringUTF8({expr}, {start0} + 1, {length})"

    def strlen(self, expr: str) -> str:
        return f"lengthUTF8({expr})"

    def upper(self, expr: str) -> str:
        return f"upperUTF8({expr})"

    def initial_value(self, rules: TypeRules, f: FieldDef) -> str:
        if f.datatype == "DATS":
            return self.date_literal(self.date_key_placeholder)
        if f.datatype == "TIMS":
            return "'00:00:00'"  # время хранится строкой ЧЧ:ММ:СС
        if f.datatype in NUMERIC_TYPES:
            return "0"
        return rules.initial_value(f)


def ch_literal(value: str) -> str:
    """Строковый литерал ClickHouse: обратная косая черта в нём — начало escape-последовательности."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


POSTGRES = PostgresSql()
SqlDialect = PostgresSql
