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
from dbtransfer.model import Model, ObjectType, SourceKind

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
        return f"{t}({n})", "CHAR", n, 0, True
    if t in _LOB_TEXT or t == "ROWID" or t == "UROWID":
        return t, "STRING", 0, 0, True
    if t == "NUMBER":
        if not precision.strip():
            if scale.strip() == "0":  # INTEGER, SMALLINT: NUMBER(*,0)
                return "NUMBER(*,0)", "DEC", 38, 0, True
            return "NUMBER", "NUMERIC", 0, 0, True
        p, s = to_int(precision), to_int(scale)
        shown = f"NUMBER({p},{s})" if s else f"NUMBER({p})"
        if s <= 0 and (it := integer_type(p)):
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
        columns[key].append(SourceColumn(
            name=r["COLUMN_NAME"], position=to_int(r["COLUMN_ID"]), native_type=shown, datatype=datatype,
            length=length, decimals=decimals, nullable=r["NULLABLE"].upper() != "N",
            comment=col_comments.get((*key, r["COLUMN_NAME"]), ""), known=known))

    cons_columns: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for r in rows("ALL_CONS_COLUMNS"):
        cons_columns[(r["OWNER"], r["CONSTRAINT_NAME"])].append((to_int(r["POSITION"]), r["COLUMN_NAME"]))
    constraints = rows("ALL_CONSTRAINTS")
    owner_of = {(r["OWNER"], r["CONSTRAINT_NAME"]): f"{r['OWNER']}.{r['TABLE_NAME']}" for r in constraints}

    mviews = {(r["OWNER"], r["OBJECT_NAME"]) for r in rows("ALL_OBJECTS") if r["OBJECT_TYPE"] == "MATERIALIZED VIEW"}
    catalog = Catalog()
    tables: dict[tuple[str, str], SourceTable] = {}
    for r in rows("ALL_TABLES"):
        key = (r["OWNER"], r["TABLE_NAME"])
        if key in mviews:  # таблица-контейнер материализованного представления: оно попадёт в прочие объекты
            continue
        t = SourceTable(schema=key[0], name=key[1], columns=columns.get(key, []), comment=tab_comments.get(key, ""))
        if r.get("PARTITIONED").upper() == "YES":
            t.notes.append(f"Таблица {t.full_name} секционирована в Oracle: секционирование в Greenplum "
                           "задаётся вручную (PARTITION BY)")
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
                       system: str = "ORACLE", encoding: str | None = None, delimiter: str | None = None) -> Model:
    """Построить модель по словарю Oracle: из папки CSV или через открытое подключение conn.

    schemas: какие схемы переносить; по умолчанию все, кроме служебных (SYS, SYSTEM, ...).
    Для подключения список схем обязателен: запросы к ALL_* по всей базе слишком тяжёлые.
    system: имя исходной системы у DataSource (как LOGSYS в BW), например имя базы.
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
    return build_model(catalog, files, SourceKind.ORACLE, source, system, "ORACLE")
