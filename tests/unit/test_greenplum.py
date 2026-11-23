"""Импорт каталога Greenplum (6, 7, Yandex MPP, Cloudberry): типы, распределение, хранение, секции, код, DDL."""

import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbtransfer.analysis.assessment import assess
from dbtransfer.cli import app
from dbtransfer.generator import TypeRules, generate
from dbtransfer.importers import greenplum
from dbtransfer.importers.csv_reader import Row
from dbtransfer.importers.rdbms_dict import TARGET_ADSO
from dbtransfer.importers.sources import export_help, import_model
from dbtransfer.model import ObjectType, Severity, SourceKind
from dbtransfer.runtime import NULL_MARKER, convert_sql, resolve_target
from dbtransfer.transfer import ColumnCheck, TableCheck, TransferError, compare_tables

DATA = Path(__file__).parents[1] / "data" / "greenplum_dict"
RULES = TypeRules.load()
runner = CliRunner()


@pytest.fixture(scope="module")
def gp():
    return greenplum.import_greenplum(DATA)


def table_sql(model, dialect: str, name: str) -> str:
    package = generate(model, TypeRules.load(), dialect)
    return next(o.sql for o in package.objects if o.kind == "table" and o.name == name).split("COMMENT ON")[0]


def messages(model, severity=None) -> list[str]:
    return [i.message for i in model.issues if severity is None or i.severity == severity]


# --- типы ---

@pytest.mark.parametrize(("data_type", "expected"), [
    ("character varying(20)", ("varchar(20)", "CHAR", 20, 0, True, "varchar(20)")),
    ("character varying", ("varchar", "STRING", 0, 0, True, "varchar")),
    ("character(2)", ("char(2)", "FIXCHAR", 2, 0, True, "char(2)")),
    ("numeric(15,2)", ("numeric(15,2)", "DEC", 15, 2, True, "numeric(15,2)")),
    ("numeric", ("numeric", "NUMERIC", 0, 0, True, "numeric")),
    ("timestamp(3) without time zone", ("timestamp(3)", "TIMESTAMP", 0, 0, True, "timestamp(3)")),
    ("timestamp with time zone", ("timestamptz", "TIMESTAMPTZ", 0, 0, True, "timestamptz")),
    ("time(0) with time zone", ("timetz(0)", "NATIVE", 0, 0, True, "timetz(0)")),
    ("double precision", ("double precision", "FLTP", 0, 0, True, "double precision")),
    ("real", ("real", "FLTP", 0, 0, True, "real")),
    ("bytea", ("bytea", "BINARY", 0, 0, True, "bytea")),
    ("boolean", ("boolean", "NATIVE", 0, 0, True, "boolean")),
    ("character varying(10)[]", ("varchar(10)[]", "NATIVE", 0, 0, True, "varchar(10)[]")),
    ("jsonb", ("jsonb", "NATIVE", 0, 0, True, "jsonb")),
    ("abstime", ("abstime", "STRING", 0, 0, True, "text")),
])
def test_map_type(data_type, expected):
    assert greenplum.map_type(data_type) == expected


def test_user_type_becomes_text_with_caveat():
    assert greenplum.map_type("sales.order_status", "sales")[1:] == ("STRING", 0, 0, True, "text")
    text, lossy = greenplum.type_caveat("sales.order_status", "sales")
    assert lossy and "создайте его в цели" in text
    assert greenplum.type_caveat("abstime")[1] is True
    assert greenplum.type_caveat("integer") == ("", False)


# --- распределение и хранение ---

@pytest.mark.parametrize(("text", "expected"), [
    ("DISTRIBUTED BY (order_id)", ("HASH", ["order_id"], [])),
    ('DISTRIBUTED BY (a, "Order Id" cdbhash_int4_ops)', ("HASH", ["a", "Order Id"], ["cdbhash_int4_ops"])),
    ("DISTRIBUTED RANDOMLY", ("RANDOM", [], [])),
    ("DISTRIBUTED REPLICATED", ("REPLICATED", [], [])),
    ("", ("", [], [])),
])
def test_parse_distribution(text, expected):
    assert greenplum.parse_distribution(text) == expected


def test_storage_options_drop_appendonly_and_orientation():
    assert greenplum.storage_options("appendonly=true, orientation=column, compresstype=zstd, compresslevel=1") \
        == "compresstype=zstd, compresslevel=1"
    assert greenplum.storage_options("") == ""


# --- секции ---

def test_partitions_greenplum7_bounds():
    rows = [Row(i, {"LEVEL": "0", "POSITION": str(i), "KIND": "range", "KEY_COLUMNS": "dt",
                    "PARTITION_TABLE": child, "BOUND": bound, "SUBPARTITIONED": sub})
            for i, (child, bound, sub) in enumerate([
                ("sales_1_prt_jan", "FOR VALUES FROM ('2024-01-01') TO ('2024-02-01')", "N"),
                ("sales_1_prt_rest", "FOR VALUES FROM ('2024-02-01') TO (MAXVALUE)", "Y"),
                ("sales_1_prt_other", "DEFAULT", "N")], start=1)]
    p, notes = greenplum.partitioning_from(rows, "sales", "s.sales")
    assert [(x.name, x.start, x.values) for x in p.partitions] == [
        ("jan", "'2024-01-01'", ["'2024-02-01'"]), ("rest", "'2024-02-01'", []), ("other", "", ["DEFAULT"])]
    assert not p.partitions[1].is_default and p.partitions[2].is_default
    assert any("подсекции" in n for n in notes)


def test_partitions_list_and_hash():
    lst = [Row(1, {"LEVEL": "0", "POSITION": "1", "KIND": "list", "KEY_COLUMNS": "region",
                   "PARTITION_NAME": "ru", "LIST_VALUES": "'RU'::bpchar, 'BY'::bpchar"})]
    p, _ = greenplum.partitioning_from(lst, "t", "s.t")
    assert (p.kind, p.partitions[0].values) == ("LIST", ["'RU'::bpchar, 'BY'::bpchar"])
    hashed = [Row(1, {"LEVEL": "0", "POSITION": "1", "KIND": "hash", "KEY_COLUMNS": "id",
                      "BOUND": "FOR VALUES WITH (modulus 4, remainder 0)"})]
    p, notes = greenplum.partitioning_from(hashed, "t", "s.t")
    assert p is None and "HASH не переносится" in notes[0]


# --- модель из каталога Greenplum 6 ---

def test_model_tables_keys_distribution_storage(gp):
    assert gp.source_kind == SourceKind.GREENPLUM and gp.source_label == "Greenplum"
    assert sorted(gp.tables) == ["sales.fact_sales", "sales.order_lines", "sales.orders", "sales.regions"]
    orders = gp.tables["sales.orders"]
    assert orders.key == ["order_id"] and orders.distribution == "HASH" and orders.distributed_by == ["order_id"]
    assert [f.target_type for f in orders.fields] == ["bigint", "char(2)", "numeric(15,2)", "timestamp(3)", "jsonb",
                                                      "text", "text"]
    lines = gp.tables["sales.order_lines"]
    assert (lines.storage, lines.storage_options) == ("ao_column", "compresstype=quicklz, compresslevel=1")
    assert gp.tables["sales.fact_sales"].distribution == "RANDOM"
    assert gp.tables["sales.regions"].distribution == "REPLICATED"
    assert gp.tables["sales.regions"].storage_options == "fillfactor=90"


def test_model_partitions_with_gap(gp):
    p = gp.tables["sales.fact_sales"].partitioning
    assert p.kind == "RANGE" and p.columns == ["sale_date"] and not p.upper_inclusive
    assert [(x.name, x.start) for x in p.partitions] == [
        ("p2", "'2024-01-01'::date"), ("p3", "'2024-07-01'::date"), ("h2025", "'2025-07-01'::date"),
        ("other", "")]


def test_model_code_views_external(gp):
    others = {o.name: o for o in gp.others}
    assert others["sales.ext_orders"].type == ObjectType.EXTERNAL_TABLE
    routine = others["sales.load_day(p_day date)"]
    assert routine.type == ObjectType.DB_ROUTINE and routine.lines == 4
    assert routine.info.startswith("функция PL/pgSQL") and {r.name for r in routine.related} == {
        "sales.fact_sales", "sales.orders"}
    assert "sales.load_day()" in others  # перегрузка — отдельный объект
    assert others["sales.median(numeric)"].info.startswith("агрегатная функция SQL")
    assert others["sales.v_daily"].type == ObjectType.VIEW
    assert "pg_catalog.pg_class" not in gp.tables


def test_model_notes(gp):
    text = "\n".join(messages(gp))
    for part in ("legacy cdbhash", "подсекции", "последовательностей (order_id)", "WITH OIDS (sales.regions)",
                 "abstime убран в PostgreSQL 12", "sales.order_status создан в базе источника",
                 "fact_sales_qty_check", "Внешний ключ orders_region_fkey", "Greenplum Database 6.28.1"):
        assert part in text, part


def test_schema_filter(gp):
    only = greenplum.import_greenplum(DATA, schemas=["SALES"])
    assert sorted(only.tables) == sorted(gp.tables)
    assert not greenplum.import_greenplum(DATA, schemas=["other"]).tables


def test_adso_mode(gp):
    model = greenplum.import_greenplum(DATA, target=TARGET_ADSO)
    assert "sales_orders" in model.adsos and not model.tables
    assert any("в режиме ADSO берутся из type_rules.yaml" in m for m in messages(model))


# --- DDL ---

def test_ddl_greenplum6(gp):
    sql = table_sql(gp, "greenplum", "dbt_sales.fact_sales")
    assert "WITH (appendonly=true, orientation=row, compresstype=zstd, compresslevel=5)" in sql
    assert "DISTRIBUTED RANDOMLY" in sql
    assert "PARTITION p2 START ('2024-01-01'::date) INCLUSIVE END ('2024-07-01'::date) EXCLUSIVE" in sql
    assert "PARTITION h2025 START ('2025-07-01'::date) INCLUSIVE END ('2026-01-01'::date) EXCLUSIVE" in sql
    assert "DEFAULT PARTITION other" in sql
    assert "orientation=column, compresstype=quicklz" in table_sql(gp, "greenplum", "dbt_sales.order_lines")
    regions = table_sql(gp, "greenplum", "dbt_sales.regions")
    assert "DISTRIBUTED REPLICATED" in regions and "WITH (fillfactor=90)" in regions
    orders = table_sql(gp, "greenplum", "dbt_sales.orders")
    assert "created     timestamp(3)" in orders and "attrs       jsonb" in orders
    assert "DISTRIBUTED BY (order_id)" in orders


def test_ddl_greenplum7(gp):
    sql = table_sql(gp, "greenplum7", "dbt_sales.order_lines")
    assert "USING ao_column\nWITH (compresstype=zstd, compresslevel=1)" in sql
    assert "DISTRIBUTED BY (order_id)" in sql
    package = generate(gp, TypeRules.load(), "greenplum7")
    assert any("quicklz" in w for w in package.warnings)


def test_ao_with_primary_key_on_greenplum6(gp):
    model = gp.model_copy(deep=True)
    model.tables["sales.orders"].storage = "ao_column"
    assert "USING ao_column" in table_sql(model, "greenplum7", "dbt_sales.orders")
    sql = table_sql(model, "greenplum", "dbt_sales.orders")
    assert "appendonly" not in sql
    assert any("PRIMARY KEY не бывает" in w for w in generate(model, TypeRules.load(), "greenplum").warnings)


def test_rules_override_wins(tmp_path, gp):
    (tmp_path / "type_rules.yaml").write_text("overrides:\n  sales.orders:\n    distributed_by: [order_id]\n"
                                              "  sales.regions:\n    distributed_by: [code]\n", encoding="utf-8")
    rules = TypeRules.load(tmp_path)
    sql = next(o.sql for o in generate(gp, rules, "greenplum7").objects if o.name == "dbt_sales.regions")
    assert "DISTRIBUTED BY (code)" in sql


def test_postgres_target_partitions(gp):
    package = generate(gp, TypeRules.load(), "postgres")
    sql = next(o.sql for o in package.objects if o.name == "dbt_sales.fact_sales")
    assert "FOR VALUES FROM ('2025-07-01'::date) TO ('2026-01-01'::date)" in sql
    assert "dbt_sales.fact_sales_other PARTITION OF dbt_sales.fact_sales DEFAULT" in sql


# --- загрузка и сверка ---

def test_native_type_conversion(gp):
    target = resolve_target(gp, RULES, "sales.orders", ["order_id", "attrs"])
    col = target.table.column("attrs")
    assert convert_sql(col, "r.attrs", RULES) == "nullif(r.attrs, '')::jsonb"
    assert NULL_MARKER == "\\N"


def test_column_check_tolerance():
    assert ColumnCheck("a", Decimal("1.10"), Decimal("1.1")).ok
    assert not ColumnCheck("a", Decimal("1.10"), Decimal("1.11")).ok
    assert ColumnCheck("f", 0.1 + 0.2, 0.3, exact=False).ok
    assert not ColumnCheck("f", 1.0, 1.001, exact=False).ok
    assert ColumnCheck("n", None, None).ok and not ColumnCheck("n", None, 0).ok
    check = TableCheck("s.t", "dbt_s.t", 3, 2, [ColumnCheck("a", 1, 2)])
    assert not check.ok and check.problems == ["строк 3 в источнике, 2 в цели", "сумма a: 1 в источнике, 2 в цели"]


def test_transfer_needs_greenplum_model():
    from dbtransfer.importers.oracle_dict import import_oracle_dict

    with pytest.raises(TransferError, match="только для источников Greenplum и Teradata"):
        compare_tables(None, None, import_oracle_dict(DATA.parent / "oracle_dict"), RULES)


# --- отчёт, запросы, командная строка ---

def test_assessment_same_family(gp):
    rows = {a.name: a for a in assess(gp).objects}
    assert rows["sales.load_day(p_day date)"].level == 2
    assert "без перевода" in rows["sales.load_day(p_day date)"].reason
    assert rows["sales.v_daily"].level == 1
    assert rows["sales.ext_orders"].type == "Внешняя таблица"


@pytest.mark.parametrize("version", [6, 7])
def test_queries_variants(version):
    sql = greenplum.queries(version, ["sales"])
    assert set(sql) == {"VERSION", "TABLES", "COLUMNS", "CONSTRAINTS", "DISTRIBUTION", "PARTITIONS", "VIEWS",
                        "ROUTINES"}
    assert "n.nspname IN ('sales')" in sql["TABLES"]
    if version == 6:
        assert "relstorage" in sql["TABLES"] and "pg_partitions" in sql["PARTITIONS"] and "proisagg" in sql["ROUTINES"]
    else:
        assert "pg_am" in sql["TABLES"] and "relpartbound" in sql["PARTITIONS"] and "prokind" in sql["ROUTINES"]
    assert "DISTRIBUTION" not in greenplum.queries(version, greenplum=False)
    assert "NOT IN ('gp_toolkit'" in greenplum.queries(version)["TABLES"]


def test_is_greenplum():
    assert greenplum.is_greenplum("PostgreSQL 12.12 (Greenplum Database 7.1.0 build ...)")
    assert greenplum.is_greenplum("PostgreSQL 14.4 (Apache Cloudberry 2.0.0-incubating build 1)")
    assert not greenplum.is_greenplum("PostgreSQL 16.1 on x86_64")


def test_help_and_import_model():
    text = export_help("greenplum")
    assert "greenplum://" in text and "-- TABLES.csv" in text and "Greenplum 7, Cloudberry" in text
    assert import_model("greenplum", DATA).tables


def test_cli_import(tmp_path):
    shutil.copytree(Path(__file__).parents[2] / "demo", tmp_path / "demo")
    result = runner.invoke(app, ["import", str(DATA), "--source", "greenplum", "-p", str(tmp_path / "demo")])
    assert result.exit_code == 0, result.output
    assert "Greenplum: таблиц 4 (без ключа 2, секционированных 1)" in result.output
    result = runner.invoke(app, ["tables", "--source", "greenplum"])
    assert result.exit_code == 0 and "DISTRIBUTION" in result.output


def test_cli_bad_url(tmp_path):
    shutil.copytree(Path(__file__).parents[2] / "demo", tmp_path / "demo")
    result = runner.invoke(app, ["compare", "--url", "mysql://x@y/z", "-p", str(tmp_path / "demo")])
    assert result.exit_code == 2 and "greenplum://" in result.output


def test_severity_of_lossy_caveats(gp):
    warnings = messages(gp, Severity.WARNING)
    assert any("abstime" in m for m in warnings)
