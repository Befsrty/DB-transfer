"""Импорт метаданных Greenplum из каталога (pg_catalog и gp_distribution_policy).

Источник той же семьи, что и цель: Greenplum 6 и 7, Yandex MPP Analytics for PostgreSQL (Greenplum 6 и
Cloudberry 2), Apache Cloudberry, Greengage, Arenadata DB. Поэтому переносится как есть, без перевода:

* таблица -> таблица 1:1 (режим table) с теми же типами (FieldDef.target_type), ключом, распределением
  (DISTRIBUTED BY / RANDOMLY / REPLICATED), хранением (heap, AO строчное и колоночное с параметрами сжатия)
  и секциями верхнего уровня; можно и режим adso, как для Oracle и MS SQL Server;
* функции и процедуры (PL/pgSQL, SQL, PL/Python) и представления — в отчёт о сложности, без генерации SQL;
* внешние таблицы (gpfdist, PXF, s3) — в отчёт: адреса источника в новой среде другие.

Каталог читается запросами в двух вариантах: для ядра PostgreSQL 9.4 (Greenplum 6, Yandex MPP 6.x) и 12+
(Greenplum 7, Cloudberry, Greengage 7). Колонки результата одинаковые, разбор один. Два способа получить каталог:
* подключение psycopg (драйвер уже есть в зависимостях): greenplum://user@host:5432/база;
* папка CSV: каждый запрос из help_text() выгружается в файл под своим именем (TABLES.csv и т. д.).

Состав колонок — dbtransfer/rules/greenplum_dict.yaml. Как таблицы становятся объектами модели — в rdbms_dict.py.
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
    sql_in_list,
    to_int,
)
from dbtransfer.model import Model, ObjectType, Partition, Partitioning, SourceKind

HOW = ("запросом из `dbtransfer tables --source greenplum`",
       "Добавьте колонки в запрос выгрузки или поправьте dbtransfer/rules/greenplum_dict.yaml")

# Служебные схемы: не переносятся, если список схем не задан явно
SYSTEM_SCHEMAS = {"PG_CATALOG", "INFORMATION_SCHEMA", "GP_TOOLKIT", "PG_TOAST", "PG_AOSEG", "PG_BITMAPINDEX",
                  "PG_EXT_AUX"}
# Версия ядра, с которой каталог устроен как в PostgreSQL 12 (Greenplum 7, Cloudberry): relam, relispartition
CATALOG_12 = 120000
GREENPLUM_NAMES = ("Greenplum", "Greengage", "Cloudberry", "Arenadata")

# Подстановки: {where} — условие на схему (колонка {nsp}), {not_part} — не дочерняя секция
_TABLES_6 = """
SELECT n.nspname AS table_schema, c.relname AS table_name,
       CASE c.relstorage WHEN 'a' THEN 'ao_row' WHEN 'c' THEN 'ao_column' WHEN 'x' THEN 'external'
                         WHEN 'f' THEN 'foreign' ELSE 'heap' END AS storage,
       coalesce(array_to_string(c.reloptions, ', '), '') AS reloptions,
       CASE WHEN c.relhasoids THEN 'Y' ELSE 'N' END AS has_oids,
       coalesce(obj_description(c.oid, 'pg_class'), '') AS comment, c.reltuples::bigint AS row_estimate
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'f') AND {where} AND {not_part}"""
# У секционированной таблицы Greenplum 7 хранение (relam, reloptions) бывает записано только у секций: берётся
# у первой секции (ch). Смешанное хранение секций (часть heap, часть AO) переносится по первой секции.
_TABLES_12 = """
SELECT n.nspname AS table_schema, c.relname AS table_name,
       CASE WHEN c.relkind = 'f' THEN
                CASE WHEN EXISTS (SELECT 1 FROM pg_foreign_table ft JOIN pg_foreign_server s ON s.oid = ft.ftserver
                                  WHERE ft.ftrelid = c.oid AND s.srvname = 'gp_exttable_server')
                     THEN 'external' ELSE 'foreign' END
            ELSE coalesce(ch.amname, am.amname, 'heap') END AS storage,
       coalesce(array_to_string(coalesce(ch.reloptions, c.reloptions), ', '), '') AS reloptions, 'N' AS has_oids,
       coalesce(obj_description(c.oid, 'pg_class'), '') AS comment, c.reltuples::bigint AS row_estimate
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace LEFT JOIN pg_am am ON am.oid = c.relam
LEFT JOIN LATERAL (SELECT a.amname, k.reloptions FROM pg_inherits i JOIN pg_class k ON k.oid = i.inhrelid
                   LEFT JOIN pg_am a ON a.oid = k.relam
                   WHERE c.relkind = 'p' AND i.inhparent = c.oid ORDER BY k.oid LIMIT 1) ch ON true
WHERE c.relkind IN ('r', 'f', 'p') AND {where} AND {not_part}"""
_COLUMNS = """
SELECT n.nspname AS table_schema, c.relname AS table_name, a.attname AS column_name, a.attnum AS position,
       format_type(a.atttypid, a.atttypmod) AS data_type, CASE WHEN a.attnotnull THEN 'Y' ELSE 'N' END AS not_null,
       coalesce(pg_get_expr(d.adbin, d.adrelid), '') AS column_default, tn.nspname AS type_schema,
       coalesce(col_description(c.oid, a.attnum), '') AS comment
FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid JOIN pg_namespace tn ON tn.oid = t.typnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ({kinds}) AND {where} AND {not_part}"""
_CONSTRAINTS = """
SELECT n.nspname AS table_schema, c.relname AS table_name, k.conname AS constraint_name, k.contype AS constraint_type,
       coalesce((SELECT string_agg(a.attname, ',' ORDER BY x.ord) FROM unnest(k.conkey) WITH ORDINALITY x(attnum, ord)
                 JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = x.attnum), '') AS columns,
       coalesce(rn.nspname || '.' || rc.relname, '') AS ref_table, pg_get_constraintdef(k.oid) AS definition
FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_class rc ON rc.oid = k.confrelid LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace
WHERE k.contype IN ('p', 'u', 'f', 'c') AND {where} AND {not_part}"""
# pg_get_table_distributedby есть в Greenplum 6 и 7: DISTRIBUTED BY (a, b) / RANDOMLY / REPLICATED
_DISTRIBUTION = """
SELECT n.nspname AS table_schema, c.relname AS table_name, pg_get_table_distributedby(c.oid) AS distributed_by
FROM gp_distribution_policy p JOIN pg_class c ON c.oid = p.localoid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE {where} AND {not_part}"""
# Greenplum 6: представление pg_partitions, все уровни (уровень 1+ — подсекции, только для замечания)
_PARTITIONS_6 = """
SELECT p.schemaname AS table_schema, p.tablename AS table_name, coalesce(p.partitionname, '') AS partition_name,
       p.partitiontablename AS partition_table, p.partitionlevel AS level, p.partitionposition AS position,
       p.partitiontype AS kind,
       (SELECT string_agg(pc.columnname, ',' ORDER BY pc.position_in_partition_key) FROM pg_partition_columns pc
        WHERE pc.schemaname = p.schemaname AND pc.tablename = p.tablename
          AND pc.partitionlevel = p.partitionlevel) AS key_columns,
       CASE WHEN p.partitionisdefault THEN 'Y' ELSE 'N' END AS is_default,
       coalesce(p.partitionlistvalues, '') AS list_values, coalesce(p.partitionrangestart, '') AS range_start,
       CASE WHEN p.partitionstartinclusive THEN 'Y' ELSE 'N' END AS start_inclusive,
       coalesce(p.partitionrangeend, '') AS range_end,
       CASE WHEN p.partitionendinclusive THEN 'Y' ELSE 'N' END AS end_inclusive,
       '' AS bound, 'N' AS subpartitioned
FROM pg_partitions p WHERE {where}"""
# Greenplum 7 и Cloudberry: декларативные секции (классический синтаксис START … END создаёт такие же)
_PARTITIONS_12 = """
SELECT n.nspname AS table_schema, pc.relname AS table_name, '' AS partition_name, c.relname AS partition_table,
       0 AS level, row_number() OVER (PARTITION BY pc.oid ORDER BY c.oid) AS position,
       lower(split_part(pg_get_partkeydef(pc.oid), ' ', 1)) AS kind,
       substring(pg_get_partkeydef(pc.oid) from '\\((.*)\\)') AS key_columns,
       '' AS is_default, '' AS list_values, '' AS range_start, '' AS start_inclusive, '' AS range_end,
       '' AS end_inclusive, pg_get_expr(c.relpartbound, c.oid) AS bound,
       CASE WHEN c.relkind = 'p' THEN 'Y' ELSE 'N' END AS subpartitioned
FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class pc ON pc.oid = i.inhparent
JOIN pg_namespace n ON n.oid = pc.relnamespace
WHERE c.relispartition AND NOT pc.relispartition AND {where}"""
_VIEWS = """
SELECT n.nspname AS table_schema, c.relname AS table_name, c.relkind AS kind,
       pg_get_viewdef(c.oid) AS view_definition
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('v', 'm') AND {where}"""
_ROUTINES = """
SELECT n.nspname AS routine_schema, p.proname AS routine_name,
       pg_get_function_identity_arguments(p.oid) AS arguments, l.lanname AS language, {kind} AS kind,
       CASE WHEN p.prorettype = 'trigger'::regtype THEN 'Y' ELSE 'N' END AS is_trigger, p.prosrc AS source
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang
WHERE l.lanname <> 'internal' AND {where}
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                  AND d.deptype = 'e')"""

VARIANTS = {
    6: {"TABLES": _TABLES_6, "PARTITIONS": _PARTITIONS_6, "kinds": "'r', 'f'",
        "not_part": "NOT EXISTS (SELECT 1 FROM pg_partition_rule r WHERE r.parchildrelid = c.oid)",
        "kind": "CASE WHEN p.proisagg THEN 'a' WHEN p.proiswindow THEN 'w' ELSE 'f' END"},
    7: {"TABLES": _TABLES_12, "PARTITIONS": _PARTITIONS_12, "kinds": "'r', 'f', 'p'",
        "not_part": "NOT c.relispartition", "kind": "p.prokind"},
}


def queries(version: int = 6, schemas: Iterable[str] | None = None, greenplum: bool = True) -> dict[str, str]:
    """Запросы каталога для ядра Greenplum 6 (version=6) или 7 / Cloudberry (version=7)."""
    v = VARIANTS[7 if version >= 7 else 6]
    names = sql_in_list(schemas or [])

    def where(column: str) -> str:
        if names:
            return f"{column} IN ({names})"
        system = sql_in_list(s.lower() for s in sorted(SYSTEM_SCHEMAS))
        return f"{column} NOT IN ({system}) AND {column} NOT LIKE 'pg\\_temp%' AND {column} NOT LIKE 'pg\\_toast%'"

    common = {"where": where("n.nspname"), "not_part": v["not_part"], "kinds": v["kinds"], "kind": v["kind"]}
    result = {
        "VERSION": "SELECT version() AS version, current_setting('server_version_num') AS version_num, "
                   "current_database() AS database",
        "TABLES": v["TABLES"].format(**common),
        "COLUMNS": _COLUMNS.format(**common),
        "CONSTRAINTS": _CONSTRAINTS.format(**common),
        "DISTRIBUTION": _DISTRIBUTION.format(**common),
        "PARTITIONS": v["PARTITIONS"].format(where=where("p.schemaname") if version < 7 else where("n.nspname")),
        "VIEWS": _VIEWS.format(**common),
        "ROUTINES": _ROUTINES.format(**common),
    }
    if not greenplum:  # обычный PostgreSQL: распределения нет, все таблицы получат распределение по ключу
        del result["DISTRIBUTION"]
    return {k: " ".join(line.strip() for line in sql.strip().splitlines()) for k, sql in result.items()}


GREENPLUM_HELP = """\
Как выгрузить каталог Greenplum (Greenplum 6/7, Yandex MPP, Cloudberry, Greengage, ADB):
  Проще всего подключиться к базе напрямую — драйвер уже установлен:
    dbtransfer import --source greenplum --url greenplum://gpadmin@host:5432/база --schemas sales,dwh
  Пароль — в переменной DBT_SRC_PASSWORD. Нужно право чтения каталога (есть у любого пользователя базы).

  Если прямого доступа нет, выполните запросы ниже (psql, DBeaver) и сохраните результаты в CSV
  с заголовками под именами из комментариев (TABLES.csv и т. д.), все файлы в одну папку.
  Запросов два набора: для Greenplum 6 и Yandex MPP 6.x и для Greenplum 7, Cloudberry и Yandex MPP на
  Cloudberry (узнать: SELECT version()). В psql: \\copy (запрос) to 'TABLES.csv' csv header
  Обязательны TABLES и COLUMNS; без остальных не будет ключей, распределения, секций и отчёта о коде.
"""


def help_text(schemas: Iterable[str] = ("sales", "dwh")) -> str:
    parts = [GREENPLUM_HELP]
    for version, title in ((6, "Greenplum 6, Yandex MPP 6.x (PostgreSQL 9.4)"),
                           (7, "Greenplum 7, Cloudberry, Greengage 7 (PostgreSQL 12+)")):
        parts.append(f"\n-- ===== {title} =====")
        parts += [f"-- {name}.csv\n{sql};" for name, sql in queries(version, schemas).items()]
    return "\n".join(parts)


# --- типы ---

# Типы, которых нет в PostgreSQL 12 (Greenplum 7, Cloudberry): при переносе из Greenplum 6 становятся text
REMOVED_IN_12 = {"abstime", "reltime", "tinterval"}
_SHORT = ((r"\bcharacter varying\b", "varchar"), (r"\bcharacter\b", "char"), (r"\bbit varying\b", "varbit"),
          (r"^timestamp(\(\d+\))? with time zone", r"timestamptz\1"), (r"^time(\(\d+\))? with time zone", r"timetz\1"),
          (r" without time zone", ""))
_TYPE = re.compile(r"^(?P<base>[a-z][a-z0-9_ ]*?)(?:\((?P<args>[^)]*)\))?(?P<array>(\[\])*)$")
_MODEL_TYPES = {"smallint": "INT2", "integer": "INT4", "bigint": "INT8", "real": "FLTP",
                "double precision": "FLTP", "text": "STRING", "date": "DATS", "time": "TIMS",
                "timestamp": "TIMESTAMP", "timestamptz": "TIMESTAMPTZ", "bytea": "BINARY"}


def short_type(data_type: str) -> str:
    """format_type -> короткая запись: character varying(20) -> varchar(20), timestamp(3) with time zone ->
    timestamptz(3). Так тип читается в DDL и переводится в ClickHouse по базовому имени."""
    t = data_type.strip()
    for pattern, repl in _SHORT:
        t = re.sub(pattern, repl, t)
    return t


def map_type(data_type: str, type_schema: str = "pg_catalog") -> tuple[str, str, int, int, bool, str]:
    """Тип Greenplum -> (как показать, тип модели, длина, знаков после запятой, распознан, тип цели как есть).

    Тип модели нужен загрузчику для проверки значений CSV (число, дата, длина строки); сам тип колонки в
    целевой базе — как в источнике. NATIVE — тип без проверки (boolean, uuid, jsonb, interval, массивы):
    значение приводится к типу колонки, ошибка приведения отменяет загрузку файла."""
    shown = short_type(data_type)
    m = _TYPE.match(shown)
    base, args, array = (m["base"], m["args"] or "", m["array"]) if m else (shown, "", "")
    if (type_schema and type_schema != "pg_catalog") or base in REMOVED_IN_12:
        # Тип пользователя (домен, перечисление, составной) или убранный в PostgreSQL 12: значения — текстом
        return shown, "STRING", 0, 0, True, "text"
    if array:
        return shown, "NATIVE", 0, 0, True, shown
    numbers = [to_int(x) for x in args.split(",")] if args else []
    if base == "numeric":
        if numbers:
            return shown, "DEC", numbers[0], numbers[1] if len(numbers) > 1 else 0, True, shown
        return shown, "NUMERIC", 0, 0, True, shown
    if base == "varchar":
        return (shown, "CHAR", numbers[0], 0, True, shown) if numbers else (shown, "STRING", 0, 0, True, shown)
    if base == "char" and numbers:
        return shown, "FIXCHAR", numbers[0], 0, True, shown
    if base in _MODEL_TYPES:
        return shown, _MODEL_TYPES[base], 0, 0, True, shown
    return shown, "NATIVE", 0, 0, True, shown


def type_caveat(shown: str, type_schema: str = "pg_catalog") -> tuple[str, bool]:
    """Чем перенос отличается от источника: (пояснение, меняются ли значения)."""
    base = shown.split("(")[0].rstrip("[]")
    if type_schema and type_schema != "pg_catalog":
        return (f"тип {shown} создан в базе источника (домен, перечисление или составной): колонка переносится "
                "как text; чтобы сохранить тип, создайте его в цели и поменяйте тип колонки"), True
    if base in REMOVED_IN_12:
        return (f"тип {base} убран в PostgreSQL 12 (Greenplum 7, Greengage 7, ADB 7, Cloudberry): колонка "
                "переносится как text; переведите значения в timestamptz или interval"), True
    if base == "money":
        return ("money зависит от lc_monetary сервера: выгружайте и загружайте при одинаковой настройке "
                "или переведите колонку в numeric"), False
    return "", False


# --- распределение, хранение, секции ---

_DIST = re.compile(r"^DISTRIBUTED\s+(BY\s*\((?P<cols>.*)\)|RANDOMLY|REPLICATED)\s*$", re.IGNORECASE | re.DOTALL)


_ITEM = re.compile(r'\s*(?:"(?P<quoted>(?:[^"]|"")*)"|(?P<plain>[^\s,"]+))(?P<rest>[^,]*)')


def _items(text: str) -> list[tuple[str, str]]:
    """«a, "Order Id" int4_ops» -> [(a, ''), (Order Id, int4_ops)]: имя колонки и класс операторов."""
    result = []
    for m in _ITEM.finditer(text or ""):
        name = m["quoted"].replace('""', '"') if m["quoted"] is not None else m["plain"]
        result.append((name, m["rest"].strip()))
    return result


def _split_names(text: str) -> list[str]:
    return [name for name, _ in _items(text)]


def parse_distribution(text: str) -> tuple[str, list[str], list[str]]:
    """Вывод pg_get_table_distributedby -> (HASH / RANDOM / REPLICATED, колонки, классы операторов не по умолчанию)."""
    m = _DIST.match((text or "").strip())
    if not m:
        return "", [], []
    if m["cols"] is None:
        return ("REPLICATED" if "REPLICATED" in text.upper() else "RANDOM"), [], []
    items = _items(m["cols"])
    return "HASH", [name for name, _ in items], [rest for _, rest in items if rest]


def storage_options(reloptions: str) -> str:
    """Параметры WITH без appendonly и orientation: их задаёт само хранение (ao_row, ao_column)."""
    keep = [o.strip() for o in reloptions.split(",")
            if o.strip() and o.split("=", 1)[0].strip().lower() not in ("appendonly", "appendoptimized",
                                                                        "orientation")]
    return ", ".join(keep)


_BOUND_RANGE = re.compile(r"^FOR VALUES FROM \((?P<start>.*)\) TO \((?P<end>.*)\)$", re.DOTALL)
_BOUND_LIST = re.compile(r"^FOR VALUES IN \((?P<values>.*)\)$", re.DOTALL)


def _partition_name(row, table: str) -> str:
    name = row["PARTITION_NAME"]
    if name:
        return name
    child = row.get("PARTITION_TABLE")
    m = re.match(rf"^{re.escape(table)}_\d+_prt_(.+)$", child)
    name = m.group(1) if m else child
    return f"p{name}" if name.isdigit() else name  # безымянная секция EVERY: fact_1_prt_3 -> p3


def partitioning_from(rows: list, table: str, full_name: str) -> tuple[Partitioning | None, list[str]]:
    """Строки PARTITIONS одной таблицы -> секционирование верхнего уровня и замечания."""
    notes = []
    top = sorted((r for r in rows if to_int(r.get("LEVEL")) == 0), key=lambda r: to_int(r["POSITION"]))
    if any(to_int(r.get("LEVEL")) > 0 or r.get("SUBPARTITIONED") == "Y" for r in rows):
        notes.append(f"Таблица {full_name}: подсекции (SUBPARTITION) не переносятся, перенесён только верхний "
                     "уровень секций; задайте подсекции вручную")
    if not top:
        return None, notes
    kind = top[0]["KIND"].strip().upper()
    columns = _split_names(top[0]["KEY_COLUMNS"])
    if kind not in ("RANGE", "LIST"):
        notes.append(f"Таблица {full_name}: секционирование {kind} не переносится (в Greenplum есть RANGE и LIST); "
                     "строки распределяются по сегментам ключом распределения")
        return None, notes
    partitions: list[Partition] = []
    flags: set[tuple[str, str]] = set()
    for r in top:
        name = _partition_name(r, table)
        bound = r.get("BOUND").strip()
        if r.get("IS_DEFAULT") == "Y" or bound == "DEFAULT":
            partitions.append(Partition(name=name, values=["DEFAULT"]))
            continue
        if bound:  # Greenplum 7 / Cloudberry
            if m := _BOUND_RANGE.match(bound):
                start, end = m["start"], m["end"]
                partitions.append(Partition(name=name, start="" if start.upper() == "MINVALUE" else start,
                                            values=[] if end.upper() == "MAXVALUE" else [end]))
            elif m := _BOUND_LIST.match(bound):
                partitions.append(Partition(name=name, values=[m["values"]]))
            else:
                notes.append(f"Таблица {full_name}: граница секции {name} «{bound}» не разобрана, секция пропущена")
            continue
        if kind == "LIST":
            partitions.append(Partition(name=name, values=[r["LIST_VALUES"]]))
            continue
        flags.add((r.get("START_INCLUSIVE") or "Y", r.get("END_INCLUSIVE") or "N") if r["RANGE_START"] else
                  ("Y", r.get("END_INCLUSIVE") or "N"))
        partitions.append(Partition(name=name, start=r["RANGE_START"],
                                    values=[r["RANGE_END"]] if r["RANGE_END"] else []))
    upper_inclusive = flags == {("N", "Y")}
    if len(flags) > 1:
        notes.append(f"Таблица {full_name}: у секций разные INCLUSIVE / EXCLUSIVE границ; перенесено как "
                     "START INCLUSIVE END EXCLUSIVE, проверьте секции на границах")
    if not partitions:
        return None, notes
    # Секция по умолчанию — последней: в pg_partitions у неё нет позиции, в Greenplum 7 её порядок произволен
    partitions.sort(key=lambda x: x.values == ["DEFAULT"])
    notes.append(f"Таблица {full_name}: секционирование {kind} ({', '.join(columns)}), секций {len(partitions)}")
    return Partitioning(kind=kind, columns=columns, partitions=partitions, upper_inclusive=upper_inclusive), notes


# --- разбор каталога ---

ROUTINE_KINDS = {"f": "функция", "p": "процедура", "a": "агрегатная функция", "w": "оконная функция"}
LANGUAGES = {"plpgsql": "PL/pgSQL", "sql": "SQL", "plpythonu": "PL/Python", "plpython3u": "PL/Python",
             "plperl": "PL/Perl", "plperlu": "PL/Perl", "c": "C", "plr": "PL/R", "pljava": "PL/Java"}


def catalog_from(files: dict[str, TableFile], keep) -> Catalog:
    def rows(name: str, schema_column: str | None = "TABLE_SCHEMA"):
        f = files.get(name)
        if f is None or not f.usable:
            return []
        return [r for r in f.rows if schema_column is None or keep(r[schema_column])]

    catalog = Catalog()
    version = rows("VERSION", None)
    v = version[0]["VERSION"] if version else ""
    num = to_int(version[0].get("VERSION_NUM")) if version else 0
    core12 = num >= CATALOG_12

    columns: dict[tuple[str, str], list[SourceColumn]] = defaultdict(list)
    sequences: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in rows("COLUMNS"):
        shown, datatype, length, decimals, known, target = map_type(r["DATA_TYPE"], r.get("TYPE_SCHEMA"))
        caveat, lossy = type_caveat(shown, r.get("TYPE_SCHEMA"))
        key = (r["TABLE_SCHEMA"], r["TABLE_NAME"])
        columns[key].append(SourceColumn(
            name=r["COLUMN_NAME"], position=to_int(r["POSITION"]), native_type=shown, datatype=datatype,
            length=length, decimals=decimals, nullable=r["NOT_NULL"].upper() != "Y", comment=r.get("COMMENT"),
            known=known, caveat=caveat, lossy=lossy, target_type=target))
        if "nextval(" in r.get("COLUMN_DEFAULT"):
            sequences[key].append(r["COLUMN_NAME"])

    distribution = {(r["TABLE_SCHEMA"], r["TABLE_NAME"]): r["DISTRIBUTED_BY"] for r in rows("DISTRIBUTION")}
    partitions: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows("PARTITIONS"):
        partitions[(r["TABLE_SCHEMA"], r["TABLE_NAME"])].append(r)

    tables: dict[tuple[str, str], SourceTable] = {}
    oids: list[str] = []
    for r in rows("TABLES"):
        key = (r["TABLE_SCHEMA"], r["TABLE_NAME"])
        storage = r["STORAGE"].strip().lower()
        if storage in ("external", "foreign"):
            label = "внешняя таблица" if storage == "external" else "сторонняя таблица (FDW)"
            catalog.objects.append(SourceObject(schema=key[0], name=key[1], type=ObjectType.EXTERNAL_TABLE,
                                                label=label))
            continue
        t = SourceTable(schema=key[0], name=key[1], columns=columns.get(key, []), comment=r.get("COMMENT"),
                        storage=storage if storage in ("heap", "ao_row", "ao_column") else "",
                        storage_options=storage_options(r.get("RELOPTIONS")))
        if storage not in ("heap", "ao_row", "ao_column"):
            t.notes.append(f"Таблица {t.full_name}: метод хранения {storage} неизвестен, хранение — по "
                           "type_rules.yaml")
        if key in distribution:
            kind, cols, opclasses = parse_distribution(distribution[key])
            t.distribution, t.distributed_by = kind, cols
            if opclasses:
                t.notes.append(f"Таблица {t.full_name}: ключ распределения с классами операторов "
                               f"{', '.join(opclasses)} (часто legacy cdbhash после обновления с Greenplum 5); "
                               "в цели — классы по умолчанию, строки перераспределятся при загрузке")
        if key in partitions:
            t.partitioning, notes = partitioning_from(partitions[key], key[1], t.full_name)
            t.notes.extend(notes)
        if r.get("HAS_OIDS") == "Y":
            oids.append(t.full_name)
        if key in sequences:
            t.notes.append(f"Таблица {t.full_name}: значения по умолчанию из последовательностей "
                           f"({', '.join(sequences[key])}) не переносятся; данные переносятся с готовыми "
                           "значениями, последовательности создайте вручную с нужным START")
        tables[key] = t
        catalog.tables.append(t)

    constraints = rows("CONSTRAINTS")
    for kind, label in (("p", "первичный"), ("u", "уникальный")):
        for r in sorted((r for r in constraints if r["CONSTRAINT_TYPE"] == kind), key=lambda r: r["CONSTRAINT_NAME"]):
            t = tables.get((r["TABLE_SCHEMA"], r["TABLE_NAME"]))
            if t is None or t.key:
                continue
            t.key = [c for c in r["COLUMNS"].split(",") if c]
            t.key_kind, t.key_constraint = label, r["CONSTRAINT_NAME"]
    for r in constraints:
        key = (r["TABLE_SCHEMA"], r["TABLE_NAME"])
        if key not in tables:
            continue
        if r["CONSTRAINT_TYPE"] == "f":
            catalog.foreign_keys.append(ForeignKey(name=r["CONSTRAINT_NAME"], table=tables[key].full_name,
                                                   columns=[c for c in r["COLUMNS"].split(",") if c],
                                                   ref_table=r.get("REF_TABLE")))
        elif r["CONSTRAINT_TYPE"] == "c":
            tables[key].notes.append(f"Таблица {tables[key].full_name}: ограничение {r['CONSTRAINT_NAME']} "
                                     f"{r.get('DEFINITION')} не переносится; если оно нужно в цели, добавьте его после "
                                     "загрузки (ALTER TABLE … ADD CONSTRAINT)")

    for r in rows("VIEWS"):
        text = r.get("VIEW_DEFINITION")
        label = "материализованное представление" if r.get("KIND") == "m" else "представление"
        catalog.objects.append(SourceObject(schema=r["TABLE_SCHEMA"], name=r["TABLE_NAME"], type=ObjectType.VIEW,
                                            label=label, lines=count_lines(text), text=text))
    for r in rows("ROUTINES", "ROUTINE_SCHEMA"):
        language = LANGUAGES.get(r.get("LANGUAGE").lower(), r.get("LANGUAGE"))
        kind = "триггерная функция" if r.get("IS_TRIGGER") == "Y" else ROUTINE_KINDS.get(r.get("KIND"), "функция")
        label = f"{kind} {language}".strip()
        if r.get("KIND") == "p":
            label += " (процедуры есть только в Greenplum 7: в 6 — функция, COMMIT внутри нельзя)"
        # Перегрузки: одно имя с разными аргументами — разные объекты отчёта
        name = r["ROUTINE_NAME"] + (f"({r['ARGUMENTS']})" if r.get("ARGUMENTS") else "()")
        text = r.get("SOURCE")
        catalog.objects.append(SourceObject(schema=r["ROUTINE_SCHEMA"], name=name, type=ObjectType.DB_ROUTINE,
                                            label=label, lines=count_lines(text), text=text))

    if v:
        family = "PostgreSQL 12+ (Greenplum 7, Cloudberry)" if core12 else "PostgreSQL 9.4 (Greenplum 6)"
        catalog.notes.append(f"Источник: {v.split(' on ')[0] if ' on ' in v else v}; каталог прочитан как {family}")
    if oids:
        catalog.notes.append(f"Таблицы WITH OIDS ({', '.join(oids[:10])}{' …' if len(oids) > 10 else ''}): "
                             "в PostgreSQL 12 (Greenplum 7) скрытой колонки oid нет; если код читает oid, "
                             "добавьте явную колонку")
    return catalog


def is_greenplum(version: str) -> bool:
    return any(name in version for name in GREENPLUM_NAMES)


def connect(url: str, password: str | None = None):
    """Подключение psycopg: greenplum://user@host:5432/база (также postgres://, postgresql://, cloudberry://)."""
    from urllib.parse import unquote, urlparse

    import psycopg

    u = urlparse(url)
    if u.scheme not in ("greenplum", "gp", "cloudberry", "postgres", "postgresql"):
        raise DictImportFailed(f"Адрес {u.scheme}://… не подходит: нужен greenplum://user@host:5432/база")
    return psycopg.connect(host=u.hostname, port=u.port or 5432, user=unquote(u.username or ""),
                           password=password or unquote(u.password or ""), dbname=u.path.lstrip("/"),
                           connect_timeout=10, client_encoding="utf8", autocommit=True)


def server_version(conn) -> tuple[str, int]:
    cur = conn.cursor()
    try:
        cur.execute("SELECT version(), current_setting('server_version_num')")
        version, num = cur.fetchone()
    finally:
        cur.close()
    if not getattr(conn, "autocommit", True):
        conn.rollback()
    return version, int(num)


def import_greenplum(folder: Path | str | None = None, *, conn=None, schemas: Iterable[str] | None = None,
                     system: str | None = None, encoding: str | None = None, delimiter: str | None = None,
                     target: str = TARGET_TABLE) -> Model:
    """Построить модель по каталогу Greenplum: из папки CSV или через открытое подключение psycopg conn.

    schemas: какие схемы переносить; по умолчанию все, кроме служебных (pg_catalog, gp_toolkit, ...).
    system: имя исходной системы у DataSource (режим adso); по умолчанию имя базы.
    """
    specs = source_tables(SourceKind.GREENPLUM)
    schemas = [s.strip() for s in schemas or [] if s.strip()]
    if conn is not None:
        version, num = server_version(conn)
        sql = queries(7 if num >= CATALOG_12 else 6, schemas, greenplum=is_greenplum(version))
        autocommit = getattr(conn, "autocommit", True)
        if not autocommit:
            conn.autocommit = True  # ошибка необязательного запроса не должна обрывать следующие
        try:
            files = read_connection(conn, sql, specs)
        finally:
            if not autocommit:
                conn.autocommit = False
        source = "подключение к Greenplum"
    else:
        if folder is None:
            raise ValueError("нужна папка с выгрузкой или подключение")
        files = read_folder(folder, specs, HOW, encoding, delimiter)
        source = str(folder)
    if system is None:
        names = {r["DATABASE"] for r in files["VERSION"].rows if r.get("DATABASE")} if files["VERSION"].usable \
            else set()
        system = names.pop().upper() if len(names) == 1 else "GREENPLUM"
    wanted = {s.lower() for s in schemas}
    keep = (lambda s: s.lower() in wanted) if wanted else (lambda s: s.upper() not in SYSTEM_SCHEMAS
                                                            and not s.lower().startswith(("pg_temp", "pg_toast")))
    catalog = catalog_from(files, keep)
    return build_model(catalog, files, SourceKind.GREENPLUM, source, system, "GREENPLUM", target)
