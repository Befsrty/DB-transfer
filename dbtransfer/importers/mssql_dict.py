"""Импорт метаданных MS SQL Server из словаря (INFORMATION_SCHEMA и sys.*).

Два способа получить словарь:
* папка CSV: каждый запрос из MSSQL_QUERIES выгружается в файл под своим именем (COLUMNS.csv и т. д.),
  например из SSMS («Сохранить результаты как» CSV с заголовками) или DBeaver;
* подключение к базе через pymssql (pip install pymssql): те же запросы выполняются напрямую.

Состав колонок — dbtransfer/rules/mssql_dict.yaml. Как таблицы становятся объектами модели — в rdbms_dict.py.
"""

from __future__ import annotations

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

HOW = ("запросом из `dbtransfer tables --source mssql`",
       "Добавьте колонки в запрос выгрузки или поправьте dbtransfer/rules/mssql_dict.yaml")

# Служебные схемы: не переносятся, если список схем не задан явно
SYSTEM_SCHEMAS = {"SYS", "INFORMATION_SCHEMA", "GUEST", "DB_OWNER", "DB_ACCESSADMIN", "DB_SECURITYADMIN",
                  "DB_DDLADMIN", "DB_BACKUPOPERATOR", "DB_DATAREADER", "DB_DATAWRITER", "DB_DENYDATAREADER",
                  "DB_DENYDATAWRITER"}

# {schemas} — список схем, подставляется при выгрузке
MSSQL_QUERIES = {
    "TABLES": "SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES "
              "WHERE TABLE_SCHEMA IN ({schemas})",
    "COLUMNS": "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, IS_NULLABLE, DATA_TYPE, "
               "CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, DATETIME_PRECISION "
               "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA IN ({schemas})",
    "TABLE_CONSTRAINTS": "SELECT CONSTRAINT_SCHEMA, CONSTRAINT_NAME, TABLE_SCHEMA, TABLE_NAME, CONSTRAINT_TYPE "
                         "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA IN ({schemas}) "
                         "AND CONSTRAINT_TYPE IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')",
    "KEY_COLUMN_USAGE": "SELECT CONSTRAINT_SCHEMA, CONSTRAINT_NAME, TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, "
                        "ORDINAL_POSITION FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA IN ({schemas})",
    "REFERENTIAL_CONSTRAINTS": "SELECT CONSTRAINT_SCHEMA, CONSTRAINT_NAME, UNIQUE_CONSTRAINT_SCHEMA, "
                               "UNIQUE_CONSTRAINT_NAME FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS "
                               "WHERE CONSTRAINT_SCHEMA IN ({schemas})",
    "COMMENTS": "SELECT s.name AS TABLE_SCHEMA, o.name AS TABLE_NAME, COALESCE(c.name, '') AS COLUMN_NAME, "
                "CAST(ep.value AS nvarchar(4000)) AS COMMENT FROM sys.extended_properties ep "
                "JOIN sys.objects o ON o.object_id = ep.major_id JOIN sys.schemas s ON s.schema_id = o.schema_id "
                "LEFT JOIN sys.columns c ON c.object_id = ep.major_id AND c.column_id = ep.minor_id "
                "WHERE ep.class = 1 AND ep.name = 'MS_Description' AND s.name IN ({schemas})",
    "VIEWS": "SELECT s.name AS TABLE_SCHEMA, v.name AS TABLE_NAME, m.definition AS VIEW_DEFINITION "
             "FROM sys.views v JOIN sys.schemas s ON s.schema_id = v.schema_id "
             "JOIN sys.sql_modules m ON m.object_id = v.object_id WHERE s.name IN ({schemas})",
    "ROUTINES": "SELECT s.name AS ROUTINE_SCHEMA, o.name AS ROUTINE_NAME, o.type_desc AS ROUTINE_TYPE, "
                "LEN(m.definition) - LEN(REPLACE(m.definition, CHAR(10), '')) + 1 AS LINES "
                "FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id "
                "JOIN sys.sql_modules m ON m.object_id = o.object_id "
                "WHERE o.type IN ('P', 'FN', 'IF', 'TF', 'TR') AND s.name IN ({schemas})",
}

MSSQL_HELP = """\
Как выгрузить словарь MS SQL Server:
  1. Откройте нужную базу (USE <база>) пользователем с правом VIEW DEFINITION на схемы.
  2. Выполните каждый запрос ниже, подставив свои схемы вместо 'dbo', 'sales'.
  3. Сохраните результат в CSV с заголовками (SSMS: Параметры → Результаты в сетке → «Включать заголовки
     столбцов», затем «Сохранить результаты как»; DBeaver: Экспорт данных → CSV) под именем из комментария:
     COLUMNS.csv и т. д., все файлы в одну папку.
  Обязательны TABLES и COLUMNS; без остальных не будет ключей, комментариев и отчёта о коде.
  Вместо выгрузки можно подключиться к базе: dbtransfer import --source mssql --url mssql://user@host:1433/база
"""


def help_text(schemas: Iterable[str] = ("dbo", "sales")) -> str:
    names = sql_in_list(schemas)
    return MSSQL_HELP + "\n" + "\n".join(f"-- {name}.csv\n{sql.format(schemas=names)};"
                                         for name, sql in MSSQL_QUERIES.items())


# --- типы ---

_TEXT = {"char", "varchar", "nchar", "nvarchar"}
_LONG_TEXT = {"text", "ntext", "xml"}
_INTEGER = {"tinyint": "INT1", "smallint": "INT2", "int": "INT4", "bigint": "INT8", "bit": "INT1"}
_BINARY = {"binary", "varbinary", "image", "timestamp", "rowversion"}
_DATETIME = {"datetime", "datetime2", "smalldatetime"}


def map_type(data_type: str, char_length: str, precision: str, scale: str) -> tuple[str, str, int, int, bool]:
    """Тип MS SQL Server -> (как показать, тип модели, длина, знаков после запятой, распознан)."""
    t = data_type.strip().lower()
    n = to_int(char_length)
    if t in _TEXT:
        if n < 0:  # varchar(max)
            return f"{t}(max)", "STRING", 0, 0, True
        return f"{t}({n})", "CHAR", n, 0, True
    if t in _LONG_TEXT:
        return t, "STRING", 0, 0, True
    if t in _INTEGER:
        return t, _INTEGER[t], to_int(precision), 0, True
    if t in ("decimal", "numeric"):
        p, s = to_int(precision), to_int(scale)
        return f"{t}({p},{s})", "DEC", p, s, True
    if t == "money":
        return t, "DEC", 19, 4, True
    if t == "smallmoney":
        return t, "DEC", 10, 4, True
    if t in ("float", "real"):
        return t, "FLTP", 0, 0, True
    if t == "date":
        return t, "DATS", 8, 0, True
    if t == "time":
        return t, "TIMS", 6, 0, True
    if t in _DATETIME:
        return t, "TIMESTAMP", 0, 0, True
    if t == "datetimeoffset":
        return t, "TIMESTAMPTZ", 0, 0, True
    if t in _BINARY:
        return t, "BINARY", 0, 0, True
    if t == "uniqueidentifier":
        return t, "CHAR", 36, 0, True
    return t, t.upper(), 0, 0, False


# --- разбор словаря ---

CONSTRAINT_KINDS = (("PRIMARY KEY", "первичный"), ("UNIQUE", "уникальный"))
ROUTINE_LABELS = {
    "SQL_STORED_PROCEDURE": "процедура T-SQL",
    "SQL_SCALAR_FUNCTION": "функция T-SQL",
    "SQL_INLINE_TABLE_VALUED_FUNCTION": "табличная функция T-SQL",
    "SQL_TABLE_VALUED_FUNCTION": "табличная функция T-SQL",
    "SQL_TRIGGER": "триггер",
}


def catalog_from(files: dict[str, TableFile], keep) -> Catalog:
    def rows(name: str, schema_column: str = "TABLE_SCHEMA"):
        f = files[name]
        return [r for r in f.rows if keep(r[schema_column])] if f.usable else []

    comments = {(r["TABLE_SCHEMA"], r["TABLE_NAME"], r["COLUMN_NAME"]): r["COMMENT"] for r in rows("COMMENTS")}
    columns: dict[tuple[str, str], list[SourceColumn]] = defaultdict(list)
    for r in rows("COLUMNS"):
        shown, datatype, length, decimals, known = map_type(r["DATA_TYPE"], r["CHARACTER_MAXIMUM_LENGTH"],
                                                            r["NUMERIC_PRECISION"], r["NUMERIC_SCALE"])
        key = (r["TABLE_SCHEMA"], r["TABLE_NAME"])
        columns[key].append(SourceColumn(
            name=r["COLUMN_NAME"], position=to_int(r["ORDINAL_POSITION"]), native_type=shown, datatype=datatype,
            length=length, decimals=decimals, nullable=r["IS_NULLABLE"].upper() != "NO",
            comment=comments.get((*key, r["COLUMN_NAME"]), ""), known=known))

    key_columns: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for r in rows("KEY_COLUMN_USAGE"):
        key_columns[(r["CONSTRAINT_SCHEMA"], r["CONSTRAINT_NAME"])].append(
            (to_int(r["ORDINAL_POSITION"]), r["COLUMN_NAME"]))
    constraints = rows("TABLE_CONSTRAINTS")
    owner_of = {(r["CONSTRAINT_SCHEMA"], r["CONSTRAINT_NAME"]): f"{r['TABLE_SCHEMA']}.{r['TABLE_NAME']}"
                for r in constraints}
    references = {(r["CONSTRAINT_SCHEMA"], r["CONSTRAINT_NAME"]): (r["UNIQUE_CONSTRAINT_SCHEMA"],
                                                                   r["UNIQUE_CONSTRAINT_NAME"])
                  for r in rows("REFERENTIAL_CONSTRAINTS", "CONSTRAINT_SCHEMA")}

    catalog = Catalog()
    tables: dict[tuple[str, str], SourceTable] = {}
    for r in rows("TABLES"):
        if r["TABLE_TYPE"].upper() != "BASE TABLE":
            continue  # представления берутся из VIEWS
        key = (r["TABLE_SCHEMA"], r["TABLE_NAME"])
        t = SourceTable(schema=key[0], name=key[1], columns=columns.get(key, []),
                        comment=comments.get((*key, ""), ""))
        tables[key] = t
        catalog.tables.append(t)

    for kind, label in CONSTRAINT_KINDS:
        for r in sorted((r for r in constraints if r["CONSTRAINT_TYPE"].upper() == kind),
                        key=lambda r: r["CONSTRAINT_NAME"]):
            t = tables.get((r["TABLE_SCHEMA"], r["TABLE_NAME"]))
            if t is None or t.key:
                continue
            t.key = [c for _, c in sorted(key_columns.get((r["CONSTRAINT_SCHEMA"], r["CONSTRAINT_NAME"]), []))]
            t.key_kind, t.key_constraint = label, r["CONSTRAINT_NAME"]
    for r in constraints:
        if r["CONSTRAINT_TYPE"].upper() != "FOREIGN KEY" or (r["TABLE_SCHEMA"], r["TABLE_NAME"]) not in tables:
            continue
        ref = references.get((r["CONSTRAINT_SCHEMA"], r["CONSTRAINT_NAME"]))
        catalog.foreign_keys.append(ForeignKey(
            name=r["CONSTRAINT_NAME"], table=f"{r['TABLE_SCHEMA']}.{r['TABLE_NAME']}",
            columns=[c for _, c in sorted(key_columns.get((r["CONSTRAINT_SCHEMA"], r["CONSTRAINT_NAME"]), []))],
            ref_table=owner_of.get(ref, "") if ref else ""))

    views = {(r["TABLE_SCHEMA"], r["TABLE_NAME"]): r.get("VIEW_DEFINITION") for r in rows("VIEWS")}
    # Представление без текста (нет файла VIEWS) всё равно попадает в отчёт по списку TABLES
    for r in rows("TABLES"):
        if r["TABLE_TYPE"].upper() == "VIEW":
            views.setdefault((r["TABLE_SCHEMA"], r["TABLE_NAME"]), "")
    for (schema, name), text in views.items():
        catalog.objects.append(SourceObject(schema=schema, name=name, type=ObjectType.VIEW, label="представление",
                                            lines=count_lines(text), text=text))
    for r in rows("ROUTINES", "ROUTINE_SCHEMA"):
        kind = r["ROUTINE_TYPE"].upper()
        catalog.objects.append(SourceObject(schema=r["ROUTINE_SCHEMA"], name=r["ROUTINE_NAME"],
                                            type=ObjectType.DB_ROUTINE,
                                            label=ROUTINE_LABELS.get(kind, kind.lower() or "код T-SQL"),
                                            lines=to_int(r.get("LINES"))))
    return catalog


def connect(url: str, password: str | None = None):
    """Подключение pymssql: mssql://user@host:port/база."""
    from urllib.parse import unquote, urlparse

    try:
        import pymssql
    except ImportError as e:
        raise DictImportFailed("Для подключения к MS SQL Server установите драйвер: uv sync --extra mssql "
                               "(или pip install pymssql). Без драйвера выгрузите словарь в CSV") from e
    u = urlparse(url)
    return pymssql.connect(server=u.hostname, port=u.port or 1433, user=unquote(u.username or ""),
                           password=password or unquote(u.password or ""), database=u.path.lstrip("/"))


def import_mssql_dict(folder: Path | str | None = None, *, conn=None, schemas: Iterable[str] | None = None,
                      system: str | None = None, encoding: str | None = None, delimiter: str | None = None) -> Model:
    """Построить модель по словарю MS SQL Server: из папки CSV или через открытое подключение conn.

    schemas: какие схемы переносить; по умолчанию все, кроме служебных (sys, INFORMATION_SCHEMA, db_*).
    Для подключения список схем обязателен.
    system: имя исходной системы у DataSource; по умолчанию имя базы из TABLES.TABLE_CATALOG или MSSQL.
    """
    specs = source_tables(SourceKind.MSSQL)
    schemas = [s for s in schemas or [] if s.strip()]
    if conn is not None:
        if not schemas:
            raise DictImportFailed("При подключении к MS SQL Server укажите схемы: --schemas dbo,sales")
        names = sql_in_list(schemas)
        files = read_connection(conn, {n: q.format(schemas=names) for n, q in MSSQL_QUERIES.items()}, specs)
        source = "подключение к MS SQL Server"
    else:
        if folder is None:
            raise ValueError("нужна папка с выгрузкой или подключение")
        files = read_folder(folder, specs, HOW, encoding, delimiter)
        source = str(folder)
    if system is None:
        catalogs = {r["TABLE_CATALOG"] for r in files["TABLES"].rows if r["TABLE_CATALOG"]}
        system = catalogs.pop().upper() if len(catalogs) == 1 else "MSSQL"
    catalog = catalog_from(files, schema_filter(schemas, SYSTEM_SCHEMAS))
    return build_model(catalog, files, SourceKind.MSSQL, source, system, "MSSQL")
