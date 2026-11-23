"""Импорт метаданных Teradata из словаря DBC (TablesV, ColumnsV, IndicesV, PartitioningConstraintsV, ...).

Teradata, как и цель, — MPP-СУБД: строки раскладываются по AMP хешем первичного индекса, как в Greenplum по
сегментам хешем ключа распределения. Поэтому перенос 1:1 почти прямой:

* таблица -> таблица 1:1 (режим table, по умолчанию) или поток DataSource → ADSO (режим adso), как для Oracle;
* PRIMARY INDEX (UPI / NUPI) и PRIMARY AMP INDEX -> DISTRIBUTED BY по тем же колонкам, NoPI -> RANDOMLY;
* ключ: UPI, иначе PRIMARY KEY, UNIQUE, уникальный вторичный индекс (USI);
* SET-таблица (без дублей строк) с уникальным ключом переносится как есть, без ключа — предупреждение:
  Teradata отбрасывает одинаковые строки, Greenplum — нет;
* PPI RANGE_N по дате или целому -> секции RANGE, NO RANGE / UNKNOWN -> секция по умолчанию;
  колоночное секционирование (PARTITION BY COLUMN) -> AO колоночное хранение;
* NOT CASESPECIFIC колонки ключа сравниваются через lower(), как *_CI_* MS SQL Server;
* представления, макросы, процедуры SPL, функции, триггеры, join-индексы и скрипты BTEQ / FastLoad /
  MultiLoad / TPT — в отчёт о сложности, с найденными конструкциями Teradata (QUALIFY, SEL, FORMAT …).

Два способа получить словарь:
* папка CSV: каждый запрос из help_text() выгружается в файл под именем представления (TablesV.csv и т. д.);
* подключение через teradatasql (uv sync --extra teradata): teradata://user@host/?logmech=LDAP.

Состав колонок — dbtransfer/rules/teradata_dict.yaml. Как таблицы становятся объектами модели — в rdbms_dict.py.
Допущения (живого Teradata в CI нет): коды DBC.ColumnsV.ColumnType и DBC.IndicesV.IndexType, CheckOpt = 'Y'
у SET-таблиц и вид ConstraintText взяты из документации Teradata 16.20–17.20 и проверены на синтетике.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from dbtransfer.config import source_tables
from dbtransfer.importers.csv_reader import TableFile, decode
from dbtransfer.importers.rdbms_dict import (
    TARGET_TABLE,
    Catalog,
    DictImportFailed,
    ForeignKey,
    SourceColumn,
    SourceObject,
    SourceTable,
    build_model,
    count_lines,
    read_connection,
    read_folder,
    schema_filter,
    sql_in_list,
    to_int,
)
from dbtransfer.model import Model, ObjectType, Partition, Partitioning, SourceKind

HOW = ("запросом из `dbtransfer tables --source teradata`",
       "Добавьте колонки в запрос выгрузки или поправьте dbtransfer/rules/teradata_dict.yaml")

# Служебные базы Teradata и Vantage: не переносятся, если список баз не задан явно
SYSTEM_DATABASES = {
    "DBC", "SYSLIB", "SYSUDTLIB", "SYSSPATIAL", "SYSBAR", "SYSJDBC", "SYSXML", "SYSUIF", "SYSADMIN", "SYSTEMFE",
    "SYS_CALENDAR", "SQLJ", "TDSTATS", "TDQCD", "TDMAPS", "TDWM", "TD_SERVER_DB", "TD_SYSFNLIB", "TD_SYSXML",
    "TD_SYSGPL", "TD_ANALYTICS_DB", "TD_METRIC_SVC", "TD_SYSCONTAINER", "TDBCMGMT", "TDAAS_DB", "TDAAS_BAR",
    "TDAAS_MAINT", "TDAAS_SUPPORT", "TDAAS_TDBCMGMT1", "TDAAS_TDBCMGMT2", "TD_MLDB", "TD_OFSDB", "VAL",
    "CRASHDUMPS", "DBCMNGR", "LOCKLOGSHREDDER", "EXTERNAL_AP", "PUBLIC", "ALL", "DEFAULT", "EXTUSER", "TDPUSER",
    "CONSOLE", "GLOBAL_FUNCTIONS", "SAS_SYSFNLIB", "SYSRESOURCE",
}

# Виды объектов DBC.TablesV.TableKind
TABLE_KINDS = {"T": "таблица", "O": "таблица без первичного индекса (NoPI)", "Q": "таблица-очередь (QUEUE)"}
OBJECT_KINDS = {
    "V": (ObjectType.VIEW, "представление"),
    "I": (ObjectType.VIEW, "join-индекс (материализованное представление)"),
    "M": (ObjectType.DB_ROUTINE, "макрос"),
    "P": (ObjectType.DB_ROUTINE, "процедура SPL"),
    "E": (ObjectType.DB_ROUTINE, "внешняя процедура (C, C++, Java)"),
    "F": (ObjectType.DB_ROUTINE, "функция (UDF)"),
    "A": (ObjectType.DB_ROUTINE, "агрегатная функция (UDF)"),
    "B": (ObjectType.DB_ROUTINE, "агрегатная функция (UDF)"),
    "R": (ObjectType.DB_ROUTINE, "табличная функция (UDF)"),
    "S": (ObjectType.DB_ROUTINE, "функция (UDF)"),
    "G": (ObjectType.DB_ROUTINE, "триггер"),
}
_KINDS_SQL = ", ".join(f"'{k}'" for k in [*TABLE_KINDS, *OBJECT_KINDS, "N"])

_DB = "DatabaseName IN ({databases})"
TERADATA_QUERIES = {
    "DBCInfoV": "SELECT InfoKey, InfoData FROM DBC.DBCInfoV",
    "TablesV": "SELECT DatabaseName, TableName, TableKind, CheckOpt, CommitOpt, CommentString, RequestText "
               f"FROM DBC.TablesV WHERE {_DB} AND TableKind IN ({_KINDS_SQL})",
    "ColumnsV": "SELECT c.DatabaseName, c.TableName, c.ColumnName, c.ColumnId, c.ColumnType, c.ColumnLength, "
                "c.DecimalTotalDigits, c.DecimalFractionalDigits, c.Nullable, c.CharType, c.UpperCaseFlag, "
                "c.ColumnFormat, c.IdColType, c.CommentString FROM DBC.ColumnsV c JOIN DBC.TablesV t "
                "ON t.DatabaseName = c.DatabaseName AND t.TableName = c.TableName "
                "WHERE t.TableKind IN ('T', 'O', 'Q') AND c.DatabaseName IN ({databases})",
    "IndicesV": "SELECT DatabaseName, TableName, IndexNumber, IndexType, UniqueFlag, IndexName, ColumnName, "
                f"ColumnPosition FROM DBC.IndicesV WHERE {_DB}",
    "PartitioningConstraintsV": "SELECT DatabaseName, TableName, ConstraintText, PartitioningLevels, "
                                f"ColumnPartitioningLevel FROM DBC.PartitioningConstraintsV WHERE {_DB}",
    "All_RI_ChildrenV": "SELECT IndexName, ChildDB, ChildTable, ChildKeyColumn, ParentDB, ParentTable "
                        "FROM DBC.All_RI_ChildrenV WHERE ChildDB IN ({databases})",
}

TERADATA_HELP = """\
Как выгрузить словарь Teradata (Teradata 15.10+, Vantage):
  Проще всего подключиться напрямую (драйвер: uv sync --extra teradata, или pip install teradatasql):
    dbtransfer import --source teradata --url teradata://reader@td-host/?logmech=LDAP --schemas SALES,DWH
  Пароль — в переменной DBT_SRC_PASSWORD. Нужно право SELECT на представления DBC (есть у PUBLIC).

  Если прямого доступа нет, выполните запросы ниже (BTEQ, Teradata Studio, DBeaver), подставив свои базы,
  и сохраните результаты в CSV с заголовком под именем представления (TablesV.csv и т. д.), все в одну папку.
  В BTEQ: .SET SEPARATOR ',' и .EXPORT REPORT FILE = TablesV.csv; удобнее Teradata Studio: Export → CSV.
  Обязательны TablesV и ColumnsV; без остальных не будет ключей, распределения, секций и связей.
  Скрипты BTEQ, FastLoad, MultiLoad и TPT положите в подпапку bteq (или укажите --scripts): они попадут
  в отчёт о сложности.

  Данные таблиц для dbtransfer load: CSV с заголовком, даты ГГГГ-ММ-ДД, время ЧЧ:ММ:СС, BYTE/VARBYTE —
  шестнадцатеричной строкой. Проще всего — dbtransfer export-data --url teradata://… (форматы уже нужные).
  Выгружая сами (TPT, BTEQ), задайте формат дат: CAST(col AS DATE FORMAT 'YYYY-MM-DD') или DateForm=ANSIDATE.
"""


def help_text(databases: Iterable[str] = ("SALES", "DWH")) -> str:
    names = sql_in_list(databases)
    return TERADATA_HELP + "\n" + "\n".join(f"-- {name}.csv\n{sql.format(databases=names)};"
                                            for name, sql in TERADATA_QUERIES.items())


# --- типы ---

_INTEGER = {"I1": ("BYTEINT", "INT2"), "I2": ("SMALLINT", "INT2"), "I": ("INTEGER", "INT4"), "I8": ("BIGINT", "INT8")}
_INTERVALS = {"YR": "year", "YM": "year to month", "MO": "month", "DY": "day", "DH": "day to hour",
              "DM": "day to minute", "DS": "day to second", "HR": "hour", "HM": "hour to minute",
              "HS": "hour to second", "MI": "minute", "MS": "minute to second", "SC": "second"}
_PERIODS = {"PD": "PERIOD(DATE)", "PT": "PERIOD(TIME)", "PZ": "PERIOD(TIME WITH TIME ZONE)",
            "PS": "PERIOD(TIMESTAMP)", "PM": "PERIOD(TIMESTAMP WITH TIME ZONE)"}
_AS_TEXT = {"XM": "XML", "UT": "UDT", "A1": "ARRAY", "AN": "ARRAY", "DT": "DATASET", "++": "TD_ANYTYPE"}
_BINARY = {"BF": "BYTE", "BV": "VARBYTE", "BO": "BLOB"}
# CharType: 1 LATIN, 2 UNICODE, 3 KANJISJIS, 4 GRAPHIC, 5 KANJI1. ColumnLength — в байтах, у UNICODE и
# GRAPHIC по 2 байта на символ
_CHARSETS = {"2": "UNICODE", "3": "KANJISJIS", "4": "GRAPHIC", "5": "KANJI1"}
_TWO_BYTE = {"2", "4"}


@dataclass
class TeradataType:
    shown: str  # как показать: VARCHAR(100) UNICODE, DECIMAL(15,2), TIMESTAMP(6)
    datatype: str  # тип модели
    length: int = 0
    decimals: int = 0
    known: bool = True
    target_type: str = ""  # тип цели как есть, если в type_rules.yaml для него нет типа модели


def map_type(code: str, length: str = "", total: str = "", fraction: str = "", char_type: str = "") -> TeradataType:
    """Код DBC.ColumnsV.ColumnType -> тип модели. total и fraction — DecimalTotalDigits и
    DecimalFractionalDigits (у TIME и TIMESTAMP во fraction точность долей секунды)."""
    c = code.strip().upper()
    n, p, s = to_int(length), to_int(total), to_int(fraction)
    ct = char_type.strip()
    if c in ("CF", "CV"):
        chars = n // 2 if ct in _TWO_BYTE else n
        charset = f" {_CHARSETS[ct]}" if ct in _CHARSETS else ""
        name = "CHAR" if c == "CF" else "VARCHAR"
        return TeradataType(f"{name}({chars}){charset}", "FIXCHAR" if c == "CF" else "CHAR", chars)
    if c == "CO":
        return TeradataType("CLOB", "STRING")
    if c in _INTEGER:
        return TeradataType(_INTEGER[c][0], _INTEGER[c][1])
    if c == "D":
        return TeradataType(f"DECIMAL({p},{s})", "DEC", p, s)
    if c == "N":
        if p <= 0 or s < 0:  # NUMBER, NUMBER(*), NUMBER(p): -128 — «любая» точность или масштаб
            return TeradataType("NUMBER" if p <= 0 else f"NUMBER({p})", "NUMERIC")
        return TeradataType(f"NUMBER({p},{s})", "DEC", p, s)
    if c == "F":
        return TeradataType("FLOAT", "FLTP")
    if c == "DA":
        return TeradataType("DATE", "DATS", 8)
    if c == "AT":
        return TeradataType(f"TIME({s})", "TIMS", 6)
    if c == "TS":
        return TeradataType(f"TIMESTAMP({s})", "TIMESTAMP")
    if c == "TZ":
        return TeradataType(f"TIME({s}) WITH TIME ZONE", "NATIVE", target_type=f"timetz({s})")
    if c == "SZ":
        return TeradataType(f"TIMESTAMP({s}) WITH TIME ZONE", "TIMESTAMPTZ")
    if c in _BINARY:
        return TeradataType(f"{_BINARY[c]}({n})" if c != "BO" else "BLOB", "BINARY")
    if c in _INTERVALS:
        # interval с полями (interval day to second) читает «3 04:05:06» и «15» так же, как Teradata
        return TeradataType(f"INTERVAL {_INTERVALS[c].upper()}", "NATIVE", target_type=f"interval {_INTERVALS[c]}")
    if c == "JN":
        return TeradataType("JSON", "NATIVE", target_type="jsonb")
    if c in _PERIODS:
        return TeradataType(_PERIODS[c], "STRING")
    if c in _AS_TEXT:
        return TeradataType(_AS_TEXT[c], "STRING")
    return TeradataType(c or "(пусто)", c or "UNKNOWN", known=False)


def type_caveat(code: str, total: str = "", fraction: str = "") -> tuple[str, bool]:
    """Чем перенос отличается от Teradata (строки ⚠ справочника типов): (пояснение, меняются ли значения)."""
    c = code.strip().upper()
    if c in _PERIODS:
        return (f"{_PERIODS[c]}: в Greenplum нет типа PERIOD, значение переносится текстом «(начало, конец)»; "
                "чтобы работать с границами, разложите его на две колонки (BEGIN(col), END(col)) при выгрузке"), True
    if c in ("UT", "A1", "AN", "DT", "++"):
        return (f"{_AS_TEXT[c]}: тип Teradata без аналога, значение переносится текстом; "
                "создайте подходящий тип в цели и поменяйте тип колонки"), True
    if c == "SZ":
        return ("TIMESTAMP WITH TIME ZONE: timestamptz хранит момент времени в UTC, исходное смещение "
                "не сохраняется; если оно нужно, перенесите его отдельной колонкой"), True
    if c == "XM":
        return "XML переносится как text: тип xml есть не во всех сборках Greengage", False
    if c == "JN":
        return ("JSON переносится в jsonb: пробелы и порядок ключей не сохраняются, из повторяющихся ключей — "
                "последний"), False
    if c == "I1":
        return "BYTEINT (−128…127) переносится в smallint: диапазон шире, значения те же", False
    if c == "N" and (to_int(total) <= 0 or to_int(fraction) < 0):
        return "NUMBER без точности или масштаба: перенесено как numeric без ограничений, сверка как чисел", False
    if c == "F":
        return "FLOAT в Teradata всегда 8 байт: переносится в double precision", False
    if c == "CF":
        return "CHAR(n): дополнение пробелами справа сохраняется (char(n)), сравнение без учёта этих пробелов", False
    return "", False


def _is_ci(flag: str) -> bool:
    """UpperCaseFlag: N — NOT CASESPECIFIC, U — UPPERCASE (тоже без учёта регистра), C и B — CASESPECIFIC."""
    return flag.strip().upper() in ("N", "U")


# --- секционирование (PPI) ---

MAX_PARTITIONS = 1000  # больше секций Greenplum создаёт и обслуживает медленно; дневные секции укрупняются


def _inside(text: str, start: int) -> str:
    """Текст внутри скобок, открытых в text[start]; кавычки учитываются."""
    depth, quoted = 0, False
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    return text[start + 1:]


def partition_functions(text: str) -> list[tuple[str, str]]:
    """Функции секционирования в выражении по порядку уровней: [(RANGE_N, аргументы), (CASE_N, …)]."""
    return [(m.group(1).upper(), _inside(text, m.end() - 1))
            for m in re.finditer(r"\b(RANGE_N|CASE_N)\s*\(", text, re.IGNORECASE)]


def _split_top(text: str) -> list[str]:
    parts, depth, quoted, cur = [], 0, False, ""
    for ch in text:
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        elif not quoted and depth == 0 and ch == ",":
            parts.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


_DATE = re.compile(r"^(?:DATE\s*)?'\s*(\d{4})-(\d{2})-(\d{2})\s*'(?:\s*\(\s*DATE\s*\))?$", re.IGNORECASE)
_INT = re.compile(r"^[-+]?\d+$")
_EACH_INTERVAL = re.compile(r"^INTERVAL\s*'\s*(\d+)\s*'\s*(DAY|MONTH|YEAR)$", re.IGNORECASE)


def _bound(value: str) -> date | int | None | str:
    """Граница RANGE_N: дата, целое, None для «*»; иначе — исходный текст (не разобрано)."""
    v = value.strip()
    if v == "*":
        return None
    if m := _DATE.match(v):
        return date(int(m[1]), int(m[2]), int(m[3]))
    if _INT.match(v):
        return int(v)
    return v


def _add(value: date | int, step: tuple[int, str]) -> date | int:
    n, unit = step
    if unit == "INT":
        return value + n
    if unit == "DAY":
        return value + timedelta(days=n)
    months = n * (12 if unit == "YEAR" else 1)
    y, m = divmod(value.month - 1 + months, 12)
    y, m = value.year + y, m + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(value.day, last))


def _next(value: date | int) -> date | int:
    """Следующее значение после включённой верхней границы Teradata: END EXCLUSIVE в Greenplum."""
    return value + timedelta(days=1) if isinstance(value, date) else value + 1


def _literal(value: date | int) -> str:
    return f"DATE '{value.isoformat()}'" if isinstance(value, date) else str(value)


def _name(value: date | int | None, unit: str) -> str:
    if value is None:
        return "p_min"
    if isinstance(value, date):
        return "p" + value.strftime({"YEAR": "%Y", "MONTH": "%Y%m"}.get(unit, "%Y%m%d"))
    return f"p{value}" if value >= 0 else f"pm{-value}"


@dataclass
class _Range:
    start: date | int | None
    end: date | int | None  # включительно, как в Teradata; None — без верхней границы
    step: tuple[int, str] | None


def _expand(ranges: list[_Range], coarse: str = "") -> list[Partition]:
    parts: list[Partition] = []
    for r in ranges:
        step = r.step
        if step and coarse and isinstance(r.start, date):
            step = (1, coarse)
        if not step or r.start is None or r.end is None:
            end = None if r.end is None else _next(r.end)
            parts.append(Partition(name=_name(r.start, ""), start="" if r.start is None else _literal(r.start),
                                   values=[] if end is None else [_literal(end)]))
            continue
        cur, stop = r.start, _next(r.end)
        while cur < stop:
            nxt = min(_add(cur, step), stop)
            parts.append(Partition(name=_name(cur, step[1]), start=_literal(cur), values=[_literal(nxt)]))
            cur = nxt
            if len(parts) > MAX_PARTITIONS * 40:  # дневные секции за век — ещё разумно; дальше не считаем
                return parts
    return parts


def parse_range_n(args: str, columns: dict[str, str], full_name: str) -> tuple[Partitioning | None, list[str]]:
    """Аргументы RANGE_N(col BETWEEN … AND … EACH …, NO RANGE, UNKNOWN) -> секции RANGE и замечания.
    columns — имена колонок таблицы в верхнем регистре -> имя как в словаре."""
    m = re.match(r'^\s*("(?:[^"]|"")+"|[\w$#]+)\s+BETWEEN\s+(.*)$', args, re.IGNORECASE | re.DOTALL)
    if not m:
        return None, [f"Таблица {full_name}: выражение секционирования RANGE_N({args.strip()}) не разобрано, "
                      "секционирование не перенесено; задайте секции вручную"]
    column = m[1].strip('"').replace('""', '"')
    if column.upper() not in columns:
        return None, [f"Таблица {full_name}: секционирование по выражению, а не по колонке ({column}), "
                      "не перенесено; задайте секции вручную"]
    column = columns[column.upper()]
    no_range = unknown = False
    ranges: list[_Range] = []
    for part in _split_top(m[2]):
        words = re.sub(r"\s+", " ", part.upper())
        if words in ("NO RANGE", "UNKNOWN", "NO RANGE OR UNKNOWN"):
            no_range |= words.startswith("NO RANGE")
            unknown |= words.endswith("UNKNOWN")
            continue
        r = re.match(r"^(?P<start>.+?)(?:\s+AND\s+(?P<end>.+?))?(?:\s+EACH\s+(?P<each>.+))?$", part.strip(),
                     re.IGNORECASE | re.DOTALL)
        start, end = _bound(r["start"]), _bound(r["end"]) if r["end"] else "next"
        step: tuple[int, str] | None = None
        if r["each"]:
            each = r["each"].strip()
            if em := _EACH_INTERVAL.match(each):
                step = (int(em[1]), em[2].upper())
            elif _INT.match(each):
                step = (int(each), "INT")
            else:
                start = each  # не разобрано
        bad = [x for x in (start, end) if isinstance(x, str) and x != "next"]
        if bad or (step and step[0] <= 0):
            return None, [f"Таблица {full_name}: граница секций «{part}» не разобрана (поддерживаются даты, "
                          "целые числа и шаг EACH INTERVAL 'n' DAY|MONTH|YEAR); секционирование не перенесено"]
        ranges.append(_Range(start, None if end == "next" else end, step))  # type: ignore[arg-type]
    # Диапазоны без AND (BETWEEN *, 10, 20 AND *) заканчиваются там, где начинается следующий
    for i, r in enumerate(ranges):
        if r.end is None and i + 1 < len(ranges) and ranges[i + 1].start is not None and r.step is None:
            nxt = ranges[i + 1].start
            r.end = nxt - timedelta(days=1) if isinstance(nxt, date) else nxt - 1  # type: ignore[operator]
    notes = []
    parts = _expand(ranges)
    if len(parts) > MAX_PARTITIONS:
        for coarse in ("MONTH", "YEAR"):
            if any(r.step and r.step[1] in ("DAY", "MONTH" if coarse == "YEAR" else "DAY") for r in ranges):
                candidate = _expand(ranges, coarse)
                if len(candidate) <= MAX_PARTITIONS:
                    notes.append(f"секций в Teradata {len(parts)}, больше {MAX_PARTITIONS}: секции укрупнены до "
                                 f"{'месяца' if coarse == 'MONTH' else 'года'}")
                    parts = candidate
                    break
        else:
            return None, [f"Таблица {full_name}: секций RANGE_N больше {MAX_PARTITIONS} ({len(parts)}+), "
                          "секционирование не перенесено; задайте укрупнённые секции вручную"]
    if no_range or unknown:
        parts.append(Partition(name="p_other", values=["DEFAULT"]))
        if unknown and not no_range:
            notes.append("UNKNOWN (NULL) — в секции по умолчанию; туда же попадут значения вне диапазонов, "
                         "которые Teradata отклонила бы")
    if not parts:
        return None, [f"Таблица {full_name}: у RANGE_N нет диапазонов, секционирование не перенесено"]
    note = "; ".join(notes)
    return (Partitioning(kind="RANGE", columns=[column], partitions=parts, note=note),
            [f"Таблица {full_name}: секционирование RANGE_N ({column}), секций {len(parts)}"
             + (f"; {note}" if note else "")])


def partitioning_from(text: str, columns: dict[str, str], full_name: str
                      ) -> tuple[Partitioning | None, list[str]]:
    """Выражение PPI из ConstraintText (или PARTITION BY из RequestText) -> секционирование верхнего уровня."""
    functions = partition_functions(text)
    if not functions:
        return None, []
    notes = []
    if len(functions) > 1:
        notes.append(f"Таблица {full_name}: многоуровневое секционирование ({', '.join(f for f, _ in functions)}): "
                     "перенесён только первый уровень, остальные задайте подсекциями вручную")
    kind, args = functions[0]
    if kind == "CASE_N":
        return None, [*notes, f"Таблица {full_name}: секционирование CASE_N не переносится (условия, а не "
                              "диапазоны); задайте секции LIST или RANGE вручную"]
    partitioning, more = parse_range_n(args, columns, full_name)
    return partitioning, [*notes, *more]


# --- код: конструкции Teradata, которые придётся переписать ---

FEATURES = [
    ("QUALIFY", r"\bQUALIFY\b"),
    ("SEL/INS/UPD/DEL", r"(?:^|;|\()\s*(?:SEL|INS|UPD|DEL)\s"),
    ("TOP n", r"\bSELECT\s+(?:DISTINCT\s+)?TOP\s+\d"),
    ("SAMPLE", r"\bSAMPLE\s+[\d.]"),
    ("LOCKING … FOR ACCESS", r"\bLOCK(?:ING)?\b[^;]*?\bFOR\s+ACCESS\b"),
    ("FORMAT", r"\bFORMAT\s+'"),
    ("CASESPECIFIC", r"\b(?:NOT\s+)?CASESPECIFIC\b|\(\s*(?:NOT\s+)?CS\s*\)"),
    ("MERGE", r"\bMERGE\s+INTO\b"),
    ("UPDATE … ELSE INSERT", r"\bELSE\s+INSERT\b"),
    ("ZEROIFNULL/NULLIFZERO", r"\b(?:ZEROIFNULL|NULLIFZERO)\s*\("),
    ("HASHROW/HASHAMP", r"\bHASH(?:ROW|AMP|BUCKET)\s*\("),
    ("volatile-таблицы", r"\bVOLATILE\s+(?:MULTISET\s+|SET\s+)?TABLE\b"),
    ("COLLECT STATISTICS", r"\bCOLLECT\s+STAT"),
    ("** (степень)", r"\*\*"),
    ("EXEC макроса", r"(?:^|;)\s*EXEC(?:UTE)?\s+[\w\"]"),
    ("команды BTEQ", r"^\s*\.(?:IF|EXPORT|IMPORT|LOGON|QUIT|GOTO|LABEL|SET|RUN|OS|REPEAT)\b"),
]
_FEATURES = [(name, re.compile(rx, re.IGNORECASE | re.MULTILINE)) for name, rx in FEATURES]


def teradata_features(text: str) -> list[str]:
    """Конструкции SQL Teradata в тексте, которых нет в Greenplum в том же виде."""
    clean = re.sub(r"'(?:[^']|'')*'", "''", text or "")  # без строковых литералов
    clean = re.sub(r"/\*.*?\*/", " ", clean, flags=re.DOTALL)
    clean = re.sub(r"--[^\n]*", " ", clean)
    return [name for name, rx in _FEATURES if rx.search(clean)]


def _with_features(label: str, text: str) -> str:
    found = teradata_features(text)
    return label + (f" (конструкции Teradata: {', '.join(found)})" if found else "")


# --- скрипты BTEQ, FastLoad, MultiLoad, TPT ---

SCRIPT_DIRS = ("bteq", "scripts")
SCRIPT_EXTENSIONS = {".bteq", ".btq", ".bt", ".sql", ".fld", ".fl", ".fastload", ".mld", ".ml", ".mload",
                     ".tpt", ".fexp", ".fe"}
_SCRIPT_KINDS = [
    ("скрипт TPT", r"\bDEFINE\s+JOB\b"),
    ("скрипт MultiLoad", r"^\s*\.BEGIN\s+(?:IMPORT\s+|DELETE\s+)?MLOAD\b"),
    ("скрипт FastExport", r"^\s*\.BEGIN\s+EXPORT\b"),
    ("скрипт FastLoad", r"^\s*\.?BEGIN\s+LOADING\b"),
]


def read_scripts(folder: Path | str, encoding: str | None = None) -> list[SourceObject]:
    """Скрипты в папке (с подпапками): строки, вид утилиты, база по умолчанию из DATABASE x; для связей."""
    folder = Path(folder)
    result = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SCRIPT_EXTENSIONS):
        text, _ = decode(path.read_bytes(), encoding)
        kind = next((label for label, rx in _SCRIPT_KINDS if re.search(rx, text, re.IGNORECASE | re.MULTILINE)),
                    "скрипт BTEQ")
        database = re.search(r"^\s*DATABASE\s+\"?([\w$#]+)\"?\s*;", text, re.IGNORECASE | re.MULTILINE)
        lines = sum(1 for line in text.splitlines() if line.strip())
        name = path.relative_to(folder).as_posix()
        result.append(SourceObject(schema=database[1] if database else "", name=name, type=ObjectType.SCRIPT,
                                   label=_with_features(kind, text), lines=lines, text=text, title=name))
    return result


# --- разбор словаря ---

def _index_columns(rows) -> list[str]:
    return [r["COLUMNNAME"] for r in sorted(rows, key=lambda r: to_int(r["COLUMNPOSITION"]))]


def catalog_from(files: dict[str, TableFile], keep) -> Catalog:
    def rows(name: str, column: str | None = "DATABASENAME"):
        f = files.get(name)
        if f is None or not f.usable:
            return []
        return [r for r in f.rows if column is None or keep(r[column])]

    catalog = Catalog()
    info = {r["INFOKEY"].upper(): r["INFODATA"] for r in rows("DBCINFOV", None)}
    if info.get("VERSION"):
        mode = info.get("LANGUAGE SUPPORT MODE", "")
        catalog.notes.append(f"Источник: Teradata {info['VERSION']}" + (f", режим {mode}" if mode else ""))

    columns: dict[tuple[str, str], list[SourceColumn]] = defaultdict(list)
    ci: dict[tuple[str, str], list[str]] = defaultdict(list)
    identity: dict[tuple[str, str], list[str]] = defaultdict(list)
    formats: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in rows("COLUMNSV"):
        key = (r["DATABASENAME"], r["TABLENAME"])
        code = r["COLUMNTYPE"].strip().upper()
        t = map_type(code, r["COLUMNLENGTH"], r["DECIMALTOTALDIGITS"], r["DECIMALFRACTIONALDIGITS"], r["CHARTYPE"])
        caveat, lossy = type_caveat(code, r["DECIMALTOTALDIGITS"], r["DECIMALFRACTIONALDIGITS"])
        columns[key].append(SourceColumn(
            name=r["COLUMNNAME"], position=to_int(r["COLUMNID"]), native_type=t.shown, datatype=t.datatype,
            length=t.length, decimals=t.decimals, nullable=r.get("NULLABLE", "Y").upper() != "N",
            comment=r["COMMENTSTRING"], known=t.known, caveat=caveat, lossy=lossy, target_type=t.target_type))
        if code in ("CF", "CV", "CO") and _is_ci(r["UPPERCASEFLAG"]):
            ci[key].append(r["COLUMNNAME"])
        if r["IDCOLTYPE"].strip():
            identity[key].append(r["COLUMNNAME"])
        fmt = r["COLUMNFORMAT"].strip().upper()
        if code == "DA" and fmt and fmt not in ("YYYY-MM-DD", "YYYYMMDD"):
            formats[key].append(f"{r['COLUMNNAME']} ({fmt})")

    indexes: dict[tuple[str, str], dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows("INDICESV"):
        indexes[(r["DATABASENAME"], r["TABLENAME"])][r["INDEXNUMBER"]].append(r)

    partitions: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows("PARTITIONINGCONSTRAINTSV"):
        partitions[(r["DATABASENAME"], r["TABLENAME"])].append(r)

    tables: dict[tuple[str, str], SourceTable] = {}
    for r in rows("TABLESV"):
        key = (r["DATABASENAME"], r["TABLENAME"])
        kind = r["TABLEKIND"].strip().upper()
        text = r["REQUESTTEXT"]
        if kind in OBJECT_KINDS:
            otype, label = OBJECT_KINDS[kind]
            catalog.objects.append(SourceObject(schema=key[0], name=key[1], type=otype,
                                                label=_with_features(label, text), lines=count_lines(text),
                                                text=text))
            continue
        if kind == "N":
            catalog.notes.append(f"Hash-индекс {key[0]}.{key[1]} не переносится: в Greenplum его роль играет "
                                 "ключ распределения или обычный индекс")
            continue
        if kind not in TABLE_KINDS:
            continue
        t = SourceTable(schema=key[0], name=key[1], columns=columns.get(key, []), comment=r["COMMENTSTRING"],
                        ci_columns=ci.get(key, []))
        tables[key] = t
        catalog.tables.append(t)
        _table_details(t, kind, r, indexes.get(key, {}), partitions.get(key, []))
        if key in identity:
            t.notes.append(f"Таблица {t.full_name}: колонки IDENTITY ({', '.join(identity[key])}) переносятся "
                           "с готовыми значениями; для новых строк создайте последовательность с нужным START")
        if key in formats:
            t.notes.append(f"Таблица {t.full_name}: у дат свой FORMAT ({', '.join(formats[key])}); выгружайте "
                           "их как ГГГГ-ММ-ДД (CAST(col AS DATE FORMAT 'YYYY-MM-DD') или export-data)")

    for r in rows("ALL_RI_CHILDRENV", "CHILDDB"):
        key = (r["CHILDDB"], r["CHILDTABLE"])
        if key not in tables:
            continue
        name = r["INDEXNAME"] or f"{key[1]}_fk"
        fk = next((f for f in catalog.foreign_keys if f.table == tables[key].full_name and f.name == name), None)
        if fk is None:
            fk = ForeignKey(name=name, table=tables[key].full_name, columns=[],
                            ref_table=f"{r['PARENTDB']}.{r['PARENTTABLE']}")
            catalog.foreign_keys.append(fk)
        fk.columns.append(r["CHILDKEYCOLUMN"])

    ci_other = sum(len(v) for v in ci.values())
    if ci_other:
        catalog.notes.append(f"Текстовых колонок NOT CASESPECIFIC: {ci_other}. В Teradata они сравниваются без "
                             "учёта регистра, в Greenplum — с учётом: в переводимых запросах сравнивайте через "
                             "lower() или ILIKE (ключи загрузчик и сверка уже сравнивают через lower())")
    return catalog


def _table_details(t: SourceTable, kind: str, r, indexes: dict[str, list], partitions: list) -> None:
    """Первичный индекс -> распределение, ключ, SET / MULTISET, секции и хранение одной таблицы."""
    by_type: dict[str, list[tuple[str, list]]] = defaultdict(list)
    for _number, idx in sorted(indexes.items(), key=lambda x: to_int(x[0])):
        first = idx[0]
        by_type[first["INDEXTYPE"].strip().upper()].append((first["UNIQUEFLAG"].strip().upper(), idx))

    primary = by_type.get("P", []) + by_type.get("Q", [])
    amp = by_type.get("A", [])
    pi_unique = False
    if primary:
        flag, idx = primary[0]
        t.distribution, t.distributed_by, pi_unique = "HASH", _index_columns(idx), flag == "Y"
    elif amp:
        t.distribution, t.distributed_by = "HASH", _index_columns(amp[0][1])
        t.notes.append(f"Таблица {t.full_name}: PRIMARY AMP INDEX ({', '.join(t.distributed_by)}) перенесён "
                       "ключом распределения")
    elif kind == "O" or indexes or "NO PRIMARY INDEX" in r["REQUESTTEXT"].upper():
        t.distribution = "RANDOM"
    # иначе (нет IndicesV в выгрузке) — распределение по type_rules.yaml: первое поле ключа

    # Ключ: UPI (он же ключ распределения), иначе PRIMARY KEY, UNIQUE, уникальный вторичный индекс
    candidates = []
    if pi_unique:
        name = primary[0][1][0]["INDEXNAME"] or "UPI"
        candidates.append(("первичный", f"{name} (UNIQUE PRIMARY INDEX)", t.distributed_by))
    for itype, label, title in (("K", "первичный", "PRIMARY KEY"), ("U", "уникальный", "UNIQUE"),
                                ("S", "уникальный", "UNIQUE SECONDARY INDEX")):
        for flag, idx in by_type.get(itype, []):
            if itype == "S" and flag != "Y":
                continue
            name = idx[0]["INDEXNAME"] or title
            candidates.append((label, f"{name} ({title})" if name != title else title, _index_columns(idx)))
    if candidates:
        t.key_kind, t.key_constraint, t.key = candidates[0]

    set_table = r["CHECKOPT"].strip().upper() == "Y"
    if set_table and not t.key:
        t.notes.append(f"Таблица {t.full_name} — SET-таблица без уникального ключа: Teradata отбрасывает "
                       "полностью одинаковые строки, а в Greenplum дубли сохранятся. Выгрузка дублей не содержит, "
                       "но повторная загрузка добавлением удвоит строки; если нужна та же защита, задайте ключ "
                       "или убирайте дубли в загрузке (SELECT DISTINCT)")
    if kind == "Q":
        t.notes.append(f"Таблица {t.full_name} — очередь (QUEUE): переносится как обычная таблица, выборка "
                       "SELECT AND CONSUME в Greenplum не поддерживается")
    if r["COMMITOPT"].strip().upper() in ("D", "P"):
        t.notes.append(f"Таблица {t.full_name} — глобальная временная (GLOBAL TEMPORARY) в Teradata, "
                       "переносится как обычная")
    if re.search(r"\bCOMPRESS\b", r["REQUESTTEXT"], re.IGNORECASE):
        t.notes.append(f"Таблица {t.full_name}: поколоночное сжатие значений (COMPRESS) не переносится; "
                       "в Greenplum сжимает хранение AO (compresstype)")

    column_names = {c.name.upper(): c.name for c in t.columns}
    text = " ".join(p["CONSTRAINTTEXT"] for p in partitions) or r["REQUESTTEXT"]
    columnar = any(to_int(p["COLUMNPARTITIONINGLEVEL"]) > 0 for p in partitions) or bool(
        re.search(r"PARTITION\s+BY\s+\(?\s*COLUMN\b", r["REQUESTTEXT"], re.IGNORECASE))
    if columnar:
        t.storage, t.storage_options = "ao_column", "compresstype=zstd, compresslevel=1"
        t.notes.append(f"Таблица {t.full_name}: колоночное секционирование (PARTITION BY COLUMN) перенесено "
                       "колоночным хранением AO со сжатием zstd")
    if partitions or re.search(r"\bPARTITION\s+BY\b", r["REQUESTTEXT"], re.IGNORECASE):
        t.partitioning, notes = partitioning_from(text, column_names, t.full_name)
        t.notes.extend(notes)
    # PRIMARY KEY объявляется, только если в него входят колонки секционирования (иначе ключ распределения
    # остаётся первичным индексом, см. generator/tables.py)
    declared = t.key and (t.partitioning is None or set(t.partitioning.columns) <= set(t.key))
    if declared and t.distributed_by and not set(t.distributed_by) <= set(t.key):
        t.notes.append(f"Таблица {t.full_name}: первичный индекс ({', '.join(t.distributed_by)}) не входит в ключ "
                       f"{t.key_constraint} ({', '.join(t.key)}), а Greenplum требует, чтобы ключ распределения "
                       "входил в PRIMARY KEY: распределение будет по первому полю ключа. Совместное размещение "
                       "строк, как в Teradata, теряется; соединения по колонкам индекса пойдут с перераспределением")


def connect(url: str, password: str | None = None):
    """Подключение teradatasql: teradata://user@host[:1025]/[база]?logmech=LDAP&encryptdata=true."""
    from urllib.parse import parse_qsl, unquote, urlparse

    u = urlparse(url)
    if u.scheme not in ("teradata", "td", "vantage"):
        raise DictImportFailed(f"Адрес {u.scheme}://… не подходит: нужен teradata://user@host/")
    try:
        import teradatasql
    except ImportError as e:
        raise DictImportFailed("Для подключения к Teradata установите драйвер: uv sync --extra teradata "
                               "(или pip install teradatasql). Без драйвера выгрузите словарь в CSV") from e
    params = {"host": u.hostname or "", "user": unquote(u.username or ""),
              "password": password or unquote(u.password or "")}
    if u.port:
        params["dbs_port"] = str(u.port)
    if u.path.strip("/"):
        params["database"] = u.path.strip("/")
    params.update(dict(parse_qsl(u.query)))
    return teradatasql.connect(**params)


def import_teradata(folder: Path | str | None = None, *, conn=None, schemas: Iterable[str] | None = None,
                    system: str = "TERADATA", encoding: str | None = None, delimiter: str | None = None,
                    target: str = TARGET_TABLE, scripts: Path | str | None = None) -> Model:
    """Построить модель по словарю Teradata: из папки CSV или через открытое подключение teradatasql conn.

    schemas: какие базы (DATABASE) переносить; по умолчанию все, кроме служебных (DBC, SYSLIB, TD_SYSFNLIB …).
    Для подключения список обязателен: словарь всей системы бывает очень большим.
    scripts: папка со скриптами BTEQ / FastLoad / MultiLoad / TPT; по умолчанию подпапка bteq или scripts.
    """
    specs = source_tables(SourceKind.TERADATA)
    schemas = [s.strip() for s in schemas or [] if s.strip()]
    if conn is not None:
        if not schemas:
            raise DictImportFailed("При подключении к Teradata укажите базы: --schemas SALES,DWH")
        names = sql_in_list(schemas)
        sql = {n.upper(): q.format(databases=names) for n, q in TERADATA_QUERIES.items()}
        files = read_connection(conn, sql, specs)
        source = "подключение к Teradata"
    else:
        if folder is None:
            raise ValueError("нужна папка с выгрузкой или подключение")
        files = read_folder(folder, specs, HOW, encoding, delimiter)
        source = str(folder)
        if scripts is None:
            scripts = next((Path(folder) / d for d in SCRIPT_DIRS if (Path(folder) / d).is_dir()), None)
    catalog = catalog_from(files, schema_filter(schemas, SYSTEM_DATABASES))
    if scripts is not None:
        if not Path(scripts).is_dir():
            raise DictImportFailed(f"Папка скриптов не найдена: {scripts}")
        catalog.objects.extend(read_scripts(scripts, encoding))
    return build_model(catalog, files, SourceKind.TERADATA, source, system, "TERADATA", target)
