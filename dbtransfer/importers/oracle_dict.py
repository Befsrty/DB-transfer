"""Импорт метаданных Oracle из словаря (ALL_TABLES, ALL_TAB_COLUMNS, ALL_CONSTRAINTS, ...).

Два способа получить словарь:
* папка CSV: каждый запрос из ORACLE_QUERIES выгружается в файл под именем представления
  (ALL_TAB_COLUMNS.csv и т. д.), например из SQL Developer или DBeaver;
* подключение к базе через python-oracledb (pip install oracledb): те же запросы выполняются напрямую.

Состав колонок — dbtransfer/rules/oracle_dict.yaml. Как таблицы становятся объектами модели — в rdbms_dict.py.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from dbtransfer.config import source_tables
from dbtransfer.importers.csv_reader import TableFile
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

HOW = ("запросом из `dbtransfer tables --source oracle`",
       "Добавьте колонки в запрос выгрузки или поправьте dbtransfer/rules/oracle_dict.yaml")

# Служебные схемы Oracle: не переносятся, если список схем не задан явно
SYSTEM_SCHEMAS = {
    "SYS", "SYSTEM", "OUTLN", "DBSNMP", "APPQOSSYS", "AUDSYS", "CTXSYS", "DVSYS", "DVF", "GSMADMIN_INTERNAL",
    "LBACSYS", "MDSYS", "OJVMSYS", "OLAPSYS", "ORDDATA", "ORDSYS", "ORDPLUGINS", "SI_INFORMTN_SCHEMA",
    "WMSYS", "XDB", "ANONYMOUS", "DIP", "GGSYS", "GSMCATUSER", "GSMUSER", "SYSBACKUP", "SYSDG", "SYSKM",
    "SYSRAC", "XS$NULL", "REMOTE_SCHEDULER_AGENT", "DBSFWUSER", "ORACLE_OCM", "MDDATA", "SPATIAL_CSW_ADMIN_USR",
    "SPATIAL_WFS_ADMIN_USR", "APEX_PUBLIC_USER", "FLOWS_FILES", "PUBLIC", "OPS$ORACLE",
}

# {schemas} — условие на владельца, подставляется при выгрузке
_OWNER = "OWNER IN ({schemas})"
ORACLE_QUERIES = {
    "ALL_TABLES": f"SELECT OWNER, TABLE_NAME, PARTITIONED, NUM_ROWS, TEMPORARY FROM ALL_TABLES "
                  f"WHERE {_OWNER} AND NESTED = 'NO' AND SECONDARY = 'N' AND TABLE_NAME NOT LIKE 'BIN$%'",
    "ALL_TAB_COLUMNS": f"SELECT OWNER, TABLE_NAME, COLUMN_NAME, COLUMN_ID, DATA_TYPE, DATA_LENGTH, CHAR_LENGTH, "
                       f"DATA_PRECISION, DATA_SCALE, NULLABLE FROM ALL_TAB_COLUMNS WHERE {_OWNER}",
    "ALL_CONSTRAINTS": f"SELECT OWNER, CONSTRAINT_NAME, CONSTRAINT_TYPE, TABLE_NAME, R_OWNER, R_CONSTRAINT_NAME, "
                       f"STATUS FROM ALL_CONSTRAINTS WHERE {_OWNER} AND CONSTRAINT_TYPE IN ('P', 'U', 'R')",
    "ALL_CONS_COLUMNS": f"SELECT OWNER, CONSTRAINT_NAME, TABLE_NAME, COLUMN_NAME, POSITION FROM ALL_CONS_COLUMNS "
                        f"WHERE {_OWNER}",
    "ALL_TAB_COMMENTS": f"SELECT OWNER, TABLE_NAME, TABLE_TYPE, COMMENTS FROM ALL_TAB_COMMENTS "
                        f"WHERE {_OWNER} AND COMMENTS IS NOT NULL",
    "ALL_COL_COMMENTS": f"SELECT OWNER, TABLE_NAME, COLUMN_NAME, COMMENTS FROM ALL_COL_COMMENTS "
                        f"WHERE {_OWNER} AND COMMENTS IS NOT NULL",
    "ALL_VIEWS": f"SELECT OWNER, VIEW_NAME, TEXT FROM ALL_VIEWS WHERE {_OWNER}",
    "ALL_OBJECTS": f"SELECT OWNER, OBJECT_NAME, OBJECT_TYPE, STATUS FROM ALL_OBJECTS WHERE {_OWNER} "
                   "AND OBJECT_TYPE IN ('PROCEDURE', 'FUNCTION', 'PACKAGE', 'TRIGGER', 'TYPE', 'MATERIALIZED VIEW')",
    "ALL_SOURCE": f"SELECT OWNER, NAME, TYPE, COUNT(*) AS LINES FROM ALL_SOURCE WHERE {_OWNER} "
                  "GROUP BY OWNER, NAME, TYPE",
    "ALL_PART_TABLES": f"SELECT OWNER, TABLE_NAME, PARTITIONING_TYPE, SUBPARTITIONING_TYPE, INTERVAL "
                       f"FROM ALL_PART_TABLES WHERE {_OWNER}",
    "ALL_PART_KEY_COLUMNS": f"SELECT OWNER, NAME, OBJECT_TYPE, COLUMN_NAME, COLUMN_POSITION FROM ALL_PART_KEY_COLUMNS "
                            f"WHERE {_OWNER} AND TRIM(OBJECT_TYPE) = 'TABLE'",
    "ALL_TAB_PARTITIONS": "SELECT TABLE_OWNER, TABLE_NAME, PARTITION_NAME, PARTITION_POSITION, HIGH_VALUE "
                          "FROM ALL_TAB_PARTITIONS WHERE TABLE_OWNER IN ({schemas})",
}

ORACLE_HELP = """\
Как выгрузить словарь Oracle:
  1. Подключитесь пользователем, которому видны нужные схемы (ALL_* показывают доступные объекты).
  2. Выполните каждый запрос ниже, подставив свои схемы вместо 'HR', 'SALES'.
  3. Сохраните результат в CSV с заголовком (SQL Developer: Экспорт → csv; DBeaver: Экспорт данных → CSV)
     под именем представления: ALL_TAB_COLUMNS.csv и т. д., все файлы в одну папку.
  Обязательны ALL_TABLES и ALL_TAB_COLUMNS; без остальных не будет ключей, комментариев и отчёта о коде.
  Вместо выгрузки можно подключиться к базе: dbtransfer import --source oracle --url oracle://user@host:1521/service
"""


def help_text(schemas: Iterable[str] = ("HR", "SALES")) -> str:
    owners = sql_in_list(schemas)
    return ORACLE_HELP + "\n" + "\n".join(f"-- {name}.csv\n{sql.format(schemas=owners)};"
                                          for name, sql in ORACLE_QUERIES.items())


# --- типы ---

_TIMESTAMP = re.compile(r"^TIMESTAMP(\(\d+\))?$")
_TIMESTAMP_TZ = re.compile(r"^TIMESTAMP(\(\d+\))? WITH (LOCAL )?TIME ZONE$")
_TEXT = {"VARCHAR2", "NVARCHAR2", "VARCHAR", "CHAR", "NCHAR"}
_LOB_TEXT = {"CLOB", "NCLOB", "LONG"}
_BINARY = {"RAW", "LONG RAW", "BLOB"}
_FLOAT = {"FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"}


def integer_type(precision: int) -> str:
    return "INT2" if precision <= 4 else "INT4" if precision <= 9 else "INT8" if precision <= 18 else ""


def map_type(data_type: str, length: int, char_length: int, precision: str, scale: str
             ) -> tuple[str, str, int, int, bool]:
    """Тип Oracle -> (как показать, тип модели, длина, знаков после запятой, распознан)."""
    t = data_type.strip().upper()
    if t in _TEXT:
        n = char_length or length
        # CHAR(n) дополняется пробелами и сравнивается без них: в Greenplum так же ведёт себя char(n)
        return f"{t}({n})", "FIXCHAR" if t in ("CHAR", "NCHAR") else "CHAR", n, 0, True
    if t in _LOB_TEXT or t == "ROWID" or t == "UROWID":
        return t, "STRING", 0, 0, True
    if t == "NUMBER":
        if not precision.strip():
            if scale.strip() == "0":  # INTEGER, SMALLINT: NUMBER(*,0)
                return "NUMBER(*,0)", "DEC", 38, 0, True
            return "NUMBER", "NUMERIC", 0, 0, True
        p, s = to_int(precision), to_int(scale)
        shown = f"NUMBER({p},{s})" if s else f"NUMBER({p})"
        # NUMBER(p,-s) — целые до p+s знаков (NUMBER(9,-2) до 99 999 999 900)
        if s <= 0 and (it := integer_type(p - s)):
            return shown, it, p, 0, True
        if s < 0 or s > p:  # NUMBER(5,-2), NUMBER(3,5): точно не представимы, берём numeric без ограничений
            return shown, "NUMERIC", 0, 0, True
        return shown, "DEC", p, s, True
    if t in _FLOAT:
        return t, "FLTP", 0, 0, True
    if t == "DATE":
        return t, "TIMESTAMP", 0, 0, True  # DATE в Oracle хранит и время
    if _TIMESTAMP.match(t):
        return t, "TIMESTAMP", 0, 0, True
    if _TIMESTAMP_TZ.match(t):
        return t, "TIMESTAMPTZ", 0, 0, True
    if t in _BINARY:
        return t, "BINARY", 0, 0, True
    # Нераспознанный тип: ключ для type_rules.yaml без размеров, INTERVAL DAY(2) TO SECOND(6) -> INTERVAL DAY TO SECOND
    return t, re.sub(r"\(\d+\)", "", t), 0, 0, False


_FRACTION = re.compile(r"^TIMESTAMP\((\d+)\)")


def type_caveat(data_type: str, precision: str, scale: str) -> tuple[str, bool]:
    """Чем перенос отличается от Oracle (строки ⚠ справочника типов): (пояснение, меняются ли значения)."""
    t = data_type.strip().upper()
    if (m := _FRACTION.match(t)) and int(m.group(1)) > 6:
        return (f"{t}: доли секунды точнее микросекунд, при загрузке округляются до 6 знаков; "
                "сверка по таким колонкам — до микросекунд"), True
    if _TIMESTAMP_TZ.match(t) and "LOCAL" not in t:
        return (f"{t}: timestamptz хранит момент времени в UTC, исходный часовой пояс (регион или смещение) "
                "не сохраняется; если он нужен, перенесите его отдельной колонкой"), True
    if t == "NUMBER" and precision.strip():
        p, s = to_int(precision), to_int(scale)
        if s < 0 or s > p:
            kept = "целым числом" if s < 0 else "numeric без ограничений"
            return (f"NUMBER({p},{s}): масштаб вне 0..точности, перенесено {kept}; "
                    "округление при записи, которое делал Oracle, теперь не выполняется"), False
    if t == "NUMBER" and not precision.strip() and not scale.strip():
        return "NUMBER без точности: перенесено как numeric без ограничений, сверка как чисел", False
    if t in ("ROWID", "UROWID"):
        return f"{t} — физический адрес строки Oracle, в новой базе не имеет смысла; переносится как текст", False
    if t in ("CHAR", "NCHAR"):
        return f"{t}: дополнение пробелами справа сохраняется (char(n)), сравнение без учёта этих пробелов", False
    return "", False


# --- секционирование ---

_TO_DATE = re.compile(r"^TO_DATE\(\s*'\s*([^']+?)\s*'", re.I)
_TS_LITERAL = re.compile(r"^(TIMESTAMP|DATE)\s*'\s*([^']+?)\s*'$", re.I)
_NUMBER = re.compile(r"^[-+]?\d+(\.\d+)?$")


def split_values(text: str) -> list[str]:
    """Значения через запятую на верхнем уровне: TO_DATE('…', '…'), 100 -> два значения."""
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


def bound_literal(value: str) -> str | None:
    """HIGH_VALUE Oracle -> литерал SQL Greenplum; None — не разобрано."""
    v = value.strip()
    if v.upper() in ("MAXVALUE", "DEFAULT"):
        return v.upper()
    if m := _TO_DATE.match(v):
        return f"TIMESTAMP '{m.group(1)}'"
    if m := _TS_LITERAL.match(v):
        return f"TIMESTAMP '{m.group(2)}'"
    if _NUMBER.match(v):
        return v
    if v.startswith("'") and v.endswith("'"):
        return v
    if v.upper() == "NULL":
        return "NULL"
    return None


def partitioning_from(kind: str, sub: str, interval: str, columns: list[str], parts: list[tuple[str, str]],
                      table: str) -> tuple[Partitioning | None, str]:
    """Секционирование таблицы и замечание для импорта."""
    kind = kind.upper()
    if kind == "HASH":
        return None, (f"Таблица {table} секционирована по HASH ({', '.join(columns)}) в Oracle: в Greenplum это "
                      "делает распределение, секции не создаются")
    if kind not in ("RANGE", "LIST"):
        return None, f"Секционирование {kind} таблицы {table} в Greenplum не переносится"
    if not parts:
        return None, (f"У секционированной таблицы {table} нет секций в ALL_TAB_PARTITIONS: "
                      "секционирование не перенесено")
    partitions = []
    for name, high in parts:
        values = [bound_literal(v) for v in split_values(high)]
        if any(v is None for v in values) or not values:
            return None, (f"Граница секции {name} таблицы {table} не разобрана ({high}): секционирование "
                          "не перенесено, задайте его вручную")
        if kind == "RANGE" and len(values) > 1 and len(columns) > 1:
            return None, (f"Таблица {table} секционирована по нескольким колонкам ({', '.join(columns)}): "
                          "в Greenplum 6 так нельзя, секционирование не перенесено")
        partitions.append(Partition(name=name, values=values))
    notes = []
    if interval.strip():
        # Oracle сам заводит секции под новые значения, Greenplum — нет: без секции по умолчанию строка
        # за последней границей не загрузится
        if not partitions[-1].is_default:
            partitions.append(Partition(name="P_DEFAULT", values=["MAXVALUE"]))
        notes.append(f"интервальное секционирование Oracle (INTERVAL {interval.strip()}): перенесены существующие "
                     "секции и добавлена секция по умолчанию для новых значений; новые секции создавайте заранее "
                     "(ALTER TABLE ... SPLIT DEFAULT PARTITION)")
    if sub.strip().upper() not in ("", "NONE"):
        notes.append(f"подсекции {sub.strip().upper()} не перенесены")
    note = "; ".join(notes)
    return (Partitioning(kind=kind, columns=columns, partitions=partitions, note=note),
            f"Таблица {table}: секционирование {kind} ({', '.join(columns)}), секций {len(partitions)}"
            + (f"; {note}" if note else ""))


# --- разбор словаря ---

def catalog_from(files: dict[str, TableFile], keep) -> Catalog:
    def rows(name: str):
        f = files[name]
        return [r for r in f.rows if keep(r["OWNER"])] if f.usable else []

    tab_comments = {(r["OWNER"], r["TABLE_NAME"]): r["COMMENTS"] for r in rows("ALL_TAB_COMMENTS")}
    col_comments = {(r["OWNER"], r["TABLE_NAME"], r["COLUMN_NAME"]): r["COMMENTS"] for r in rows("ALL_COL_COMMENTS")}
    columns: dict[tuple[str, str], list[SourceColumn]] = defaultdict(list)
    for r in rows("ALL_TAB_COLUMNS"):
        shown, datatype, length, decimals, known = map_type(r["DATA_TYPE"], to_int(r["DATA_LENGTH"]),
                                                            to_int(r["CHAR_LENGTH"]), r["DATA_PRECISION"],
                                                            r["DATA_SCALE"])
        key = (r["OWNER"], r["TABLE_NAME"])
        caveat, lossy = type_caveat(r["DATA_TYPE"], r["DATA_PRECISION"], r["DATA_SCALE"])
        columns[key].append(SourceColumn(
            name=r["COLUMN_NAME"], position=to_int(r["COLUMN_ID"]), native_type=shown, datatype=datatype,
            length=length, decimals=decimals, nullable=r["NULLABLE"].upper() != "N",
            comment=col_comments.get((*key, r["COLUMN_NAME"]), ""), known=known, caveat=caveat, lossy=lossy))

    cons_columns: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for r in rows("ALL_CONS_COLUMNS"):
        cons_columns[(r["OWNER"], r["CONSTRAINT_NAME"])].append((to_int(r["POSITION"]), r["COLUMN_NAME"]))
    constraints = rows("ALL_CONSTRAINTS")
    owner_of = {(r["OWNER"], r["CONSTRAINT_NAME"]): f"{r['OWNER']}.{r['TABLE_NAME']}" for r in constraints}

    mviews = {(r["OWNER"], r["OBJECT_NAME"]) for r in rows("ALL_OBJECTS") if r["OBJECT_TYPE"] == "MATERIALIZED VIEW"}
    part_tables = {(r["OWNER"], r["TABLE_NAME"]): r for r in rows("ALL_PART_TABLES")}
    part_keys: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for r in rows("ALL_PART_KEY_COLUMNS"):
        if r.get("OBJECT_TYPE", "TABLE").strip().upper() == "TABLE":
            part_keys[(r["OWNER"], r["NAME"])].append((to_int(r["COLUMN_POSITION"]), r["COLUMN_NAME"]))
    part_bounds: dict[tuple[str, str], list[tuple[int, str, str]]] = defaultdict(list)
    f = files.get("ALL_TAB_PARTITIONS")
    for r in (f.rows if f is not None and f.usable else []):
        if keep(r["TABLE_OWNER"]):
            part_bounds[(r["TABLE_OWNER"], r["TABLE_NAME"])].append(
                (to_int(r["PARTITION_POSITION"]), r["PARTITION_NAME"], r["HIGH_VALUE"]))
    catalog = Catalog()
    tables: dict[tuple[str, str], SourceTable] = {}
    for r in rows("ALL_TABLES"):
        key = (r["OWNER"], r["TABLE_NAME"])
        if key in mviews:  # таблица-контейнер материализованного представления: оно попадёт в прочие объекты
            continue
        t = SourceTable(schema=key[0], name=key[1], columns=columns.get(key, []), comment=tab_comments.get(key, ""))
        if r.get("PARTITIONED").upper() == "YES" and key not in part_tables:
            t.notes.append(f"Таблица {t.full_name} секционирована в Oracle, но в выгрузке нет ALL_PART_TABLES: "
                           "секционирование не перенесено")
        elif key in part_tables:
            pt = part_tables[key]
            cols = [c for _, c in sorted(part_keys.get(key, []))]
            parts = [(n, h) for _, n, h in sorted(part_bounds.get(key, []))]
            t.partitioning, note = partitioning_from(pt["PARTITIONING_TYPE"], pt.get("SUBPARTITIONING_TYPE"),
                                                     pt.get("INTERVAL"), cols, parts, t.full_name)
            t.notes.append(note)
        if r.get("TEMPORARY").upper() == "Y":
            t.notes.append(f"Таблица {t.full_name} временная (GLOBAL TEMPORARY) в Oracle, переносится как обычная")
        tables[key] = t
        catalog.tables.append(t)

    # Ключ: первичный, иначе первый по имени уникальный
    for kind, label in (("P", "первичный"), ("U", "уникальный")):
        for r in sorted((r for r in constraints if r["CONSTRAINT_TYPE"] == kind), key=lambda r: r["CONSTRAINT_NAME"]):
            t = tables.get((r["OWNER"], r["TABLE_NAME"]))
            if t is None or t.key:
                continue
            t.key = [c for _, c in sorted(cons_columns.get((r["OWNER"], r["CONSTRAINT_NAME"]), []))]
            t.key_kind, t.key_constraint = label, r["CONSTRAINT_NAME"]
            if r.get("STATUS").upper() == "DISABLED":
                t.notes.append(f"Ключ {r['CONSTRAINT_NAME']} таблицы {t.full_name} отключён (DISABLED) в Oracle: "
                               "данные могут содержать дубли по ключу")
    for r in constraints:
        if r["CONSTRAINT_TYPE"] != "R" or (r["OWNER"], r["TABLE_NAME"]) not in tables:
            continue
        catalog.foreign_keys.append(ForeignKey(
            name=r["CONSTRAINT_NAME"], table=f"{r['OWNER']}.{r['TABLE_NAME']}",
            columns=[c for _, c in sorted(cons_columns.get((r["OWNER"], r["CONSTRAINT_NAME"]), []))],
            ref_table=owner_of.get((r.get("R_OWNER") or r["OWNER"], r.get("R_CONSTRAINT_NAME")), "")))

    for r in rows("ALL_VIEWS"):
        text = r.get("TEXT")
        catalog.objects.append(SourceObject(schema=r["OWNER"], name=r["VIEW_NAME"], type=ObjectType.VIEW,
                                            label="представление", lines=count_lines(text), text=text))
    lines: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows("ALL_SOURCE"):
        lines[(r["OWNER"], r["NAME"])] += to_int(r["LINES"])  # PACKAGE и PACKAGE BODY вместе
    labels = {"PROCEDURE": "процедура PL/SQL", "FUNCTION": "функция PL/SQL", "PACKAGE": "пакет PL/SQL",
              "TRIGGER": "триггер", "TYPE": "объектный тип", "MATERIALIZED VIEW": "материализованное представление"}
    for r in rows("ALL_OBJECTS"):
        kind = r["OBJECT_TYPE"].upper()
        if kind not in labels:
            continue
        status = r.get("STATUS").upper()
        catalog.objects.append(SourceObject(
            schema=r["OWNER"], name=r["OBJECT_NAME"],
            type=ObjectType.VIEW if kind == "MATERIALIZED VIEW" else ObjectType.DB_ROUTINE, label=labels[kind],
            lines=lines.get((r["OWNER"], r["OBJECT_NAME"]), 0), status="INVALID" if status == "INVALID" else ""))
    return catalog


def connect(url: str, password: str | None = None):
    """Подключение python-oracledb (тонкий режим, без Oracle Client): oracle://user@host:port/service."""
    from urllib.parse import unquote, urlparse

    try:
        import oracledb
    except ImportError as e:
        raise DictImportFailed("Для подключения к Oracle установите драйвер: uv sync --extra oracle "
                               "(или pip install oracledb). Без драйвера выгрузите словарь в CSV") from e
    u = urlparse(url)
    dsn = f"{u.hostname}:{u.port or 1521}/{u.path.lstrip('/')}"
    return oracledb.connect(user=unquote(u.username or ""), password=password or unquote(u.password or ""), dsn=dsn)


def import_oracle_dict(folder: Path | str | None = None, *, conn=None, schemas: Iterable[str] | None = None,
                       system: str = "ORACLE", encoding: str | None = None, delimiter: str | None = None,
                       target: str = TARGET_TABLE) -> Model:
    """Построить модель по словарю Oracle: из папки CSV или через открытое подключение conn.

    schemas: какие схемы переносить; по умолчанию все, кроме служебных (SYS, SYSTEM, ...).
    Для подключения список схем обязателен: запросы к ALL_* по всей базе слишком тяжёлые.
    system: имя исходной системы у DataSource (как LOGSYS в BW), например имя базы.
    target: table — таблица 1:1 с исходным именем, adso — поток DataSource → ADSO как в BW.
    """
    specs = source_tables(SourceKind.ORACLE)
    schemas = [s for s in schemas or [] if s.strip()]
    if conn is not None:
        if not schemas:
            raise DictImportFailed("При подключении к Oracle укажите схемы: --schemas HR,SALES")
        owners = sql_in_list(s.upper() for s in schemas)
        files = read_connection(conn, {n: q.format(schemas=owners) for n, q in ORACLE_QUERIES.items()}, specs)
        source = "подключение к Oracle"
    else:
        if folder is None:
            raise ValueError("нужна папка с выгрузкой или подключение")
        files = read_folder(folder, specs, HOW, encoding, delimiter)
        source = str(folder)
    catalog = catalog_from(files, schema_filter(schemas, SYSTEM_SCHEMAS))
    return build_model(catalog, files, SourceKind.ORACLE, source, system, "ORACLE", target)
