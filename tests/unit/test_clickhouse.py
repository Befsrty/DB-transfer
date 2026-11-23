"""Целевая СУБД ClickHouse: эталонные файлы tests/golden/clickhouse, типы, движки, пошаговые запросы.

Эталон обновляется так же, как для Greengage: UPDATE_GOLDEN=1 uv run pytest tests/unit/test_clickhouse.py
"""

import os
import re
import shutil

import pytest
from conftest import DEMO, ROOT
from test_formula_sql import resolve

from dbtransfer.generator import RulesError, TypeRules, generate
from dbtransfer.generator.clickhouse import ClickHouseTypes
from dbtransfer.generator.formula_sql import translate
from dbtransfer.generator.sql_functions import ClickHouseSql, ch_literal
from dbtransfer.importers.bw_csv import import_bw_export
from dbtransfer.project import open_project

GOLDEN = ROOT / "tests" / "golden" / "clickhouse"


@pytest.fixture
def model():
    m = import_bw_export(DEMO / "bw_export")
    config = open_project(DEMO).config
    m.name, m.bw_version, m.schema_prefix = config.name, config.bw_version, config.schema_prefix
    return m


@pytest.fixture
def package(model):
    return generate(model, dialect="clickhouse")


def tables(sql: str) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\S+)\n\(.*?;", sql, re.S)}


def test_golden_files(package):
    files = package.files()
    if os.environ.get("UPDATE_GOLDEN"):
        shutil.rmtree(GOLDEN, ignore_errors=True)
        GOLDEN.mkdir(parents=True)
        for name, text in files.items():
            (GOLDEN / name).write_bytes(text.encode("utf-8"))
    assert sorted(files) == sorted(p.name for p in GOLDEN.iterdir())
    for name, text in files.items():
        assert text == (GOLDEN / name).read_bytes().decode("utf-8"), f"{name} отличается от эталона"


def test_deterministic(model, tmp_path):
    first = generate(model, dialect="clickhouse").write(tmp_path / "a")
    second = generate(model, dialect="clickhouse").write(tmp_path / "b")
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]


def test_no_postgres_syntax(package):
    text = "\n".join(package.files().values())
    for pg in ("plpgsql", "$$", "DISTRIBUTED", "COMMENT ON", "CREATE SCHEMA", "CASCADE", "to_char(", "to_date(",
               "DATE '", "p_load_id bigint", "\\set", "SEQUENCE", "varchar", "numeric("):
        assert pg not in text, pg
    assert "CREATE DATABASE IF NOT EXISTS dbt_stg;" in text
    assert "DROP DATABASE IF EXISTS dbt_meta;" in package.files()["drop_all.sql"]


def test_tables_engines_and_keys(package):
    ddl = tables("\n".join(o.sql for o in package.objects if o.kind == "table"))
    assert len(ddl) == 24  # 22 таблицы Greengage + 2 рабочие таблицы cumulative ADSO
    for name, text in ddl.items():
        assert "\nENGINE = " in text and "\nORDER BY " in text, name
        order = re.search(r"ORDER BY \(?([^)\n]*)\)?", text).group(1)
        for col in order.split(", "):
            if col != "tuple(":
                assert re.search(rf"^    {col}\s+(?!Nullable)\S+ COMMENT", text, re.M), f"{name}: {col} Nullable"
    assert "ENGINE = ReplacingMergeTree\nORDER BY material" in ddl["dbt_md.material_attr"]
    assert "ORDER BY (doc_number, s_ord_item)" in ddl["dbt_dwh.zsd_a01_active"]
    assert "ORDER BY (load_id, record_no)" in ddl["dbt_stg.zsd_a01_inbound"]
    a01 = ddl["dbt_dwh.zsd_a01_active"]
    assert "s_ord_item String COMMENT" in a01 and "netval_inv Nullable(Decimal(17,2))" in a01
    assert "calday     Nullable(Date32)" in a01


def test_deploy_all_has_only_ddl(package):
    deploy = package.files()["deploy_all.sql"]
    assert "060_activation" not in deploy and "070_transformations" not in deploy
    assert "{p_load_id:Int64}" not in deploy


def test_activation_steps(package):
    std = package.function_for("ZSD_A01", "activation")
    cum = package.function_for("ZSD_A02", "activation")
    assert std.kind == cum.kind == "steps"
    assert [t.split(":")[0].split(" (")[0] for t, _ in std.steps] == [
        "проверка", "before image", "after image", "удаление заменяемых строк активных данных",
        "новые строки активных данных", "итог для журнала загрузок"]
    assert std.steps[0][1].startswith("SELECT throwIf(")
    assert "LIMIT 1 BY doc_number, s_ord_item" in std.steps[1][1]
    assert std.steps[3][1].startswith("DELETE FROM dbt_dwh.zsd_a01_active\nWHERE (doc_number, s_ord_item) IN")
    assert cum.steps[0][1] == "TRUNCATE TABLE IF EXISTS dbt_stg.zsd_a02_work"
    assert "coalesce(a.netval_inv, 0) + coalesce(g.netval_inv, 0)" in cum.steps[1][1]
    for o in package.objects:
        for title, sql in o.steps:
            assert title and sql and not sql.endswith(";") and "-- Шаг" not in sql, (o.name, title)
            assert ";\n" not in sql, (o.name, title)  # один запрос в шаге


def test_transformation_steps(package):
    steps = {o.bw_object: o.steps for o in package.objects if o.group == "transformations"}
    insert = steps["Трансформация ZSALES_FF → ZSD_A01"]
    assert len(insert) == 1 and insert[0][1].endswith("SETTINGS join_use_nulls = 1")
    assert "WHERE s.load_id = {p_load_id:Int64}" in insert[0][1]
    a04 = steps["Трансформация ZSD_A01 → ZSD_A04"][0][1]
    assert "formatDateTime(s.calday, '%Y%m')" in a04 and "LEFT JOIN dbt_md.material_attr m1" in a04
    stub = steps["Трансформация ZSD_A01 → ZSD_A03"]
    assert stub == [("остановка прогона: трансформация требует ручной доработки",
                     "SELECT throwIf(1, 'Трансформация ZSD_A01 → ZSD_A03 требует ручной доработки: "
                     "конечная рутина ABAP')")]


def test_greengage_unchanged_by_clickhouse(model):
    """Диалект ClickHouse не влияет на пакет Greengage: проверка эталона — в test_generator."""
    assert "plpgsql" in generate(model).files()["060_activation.sql"]


def test_dialect_functions():
    d = ClickHouseSql()
    assert translate("SUBSTRING(MATERIAL; 0; 3)", resolve, d) == "substringUTF8(s.material, 0 + 1, 3)"
    assert translate("STRLEN(MATERIAL)", resolve, d) == "lengthUTF8(s.material)"
    assert translate("TOUPPER(MATERIAL)", resolve, d) == "upperUTF8(s.material)"
    assert translate("DATE_MONTH(CALDAY)", resolve, d) == "coalesce(formatDateTime(s.calday, '%Y%m'), '000000')"
    assert d.date_to_text("x", "IYYYIW") == "toString(toYearWeek(x, 3))"
    assert d.date_to_text("x", "YYYYQ") == "concat(toString(toYear(x)), toString(toQuarter(x)))"
    assert d.text_to_date("x").startswith("toDate32OrNull(") and "'00000000'" in d.text_to_date("x")
    assert ch_literal("it's C:\\tmp") == "'it\\'s C:\\\\tmp'"


def test_type_translation():
    ch = ClickHouseTypes(TypeRules.load())
    assert ch.column_type("varchar(10)", True) == "String"
    assert ch.column_type("numeric(17,2)", False) == "Nullable(Decimal(17,2))"
    assert ch.column_type("numeric", False) == "Nullable(Decimal(38,10))"
    assert ch.column_type("double precision", True) == "Float64"
    with pytest.raises(RulesError, match="uuid"):
        ch.column_type("uuid", True)


def test_project_rules_override(model, tmp_path):
    (tmp_path / "type_rules.yaml").write_text(
        "clickhouse:\n  types:\n    varchar: LowCardinality(String)\n  engines:\n    active: ReplacingMergeTree\n",
        encoding="utf-8")
    package = generate(model, TypeRules.load(tmp_path), "clickhouse")
    a01 = tables(package.files()["050_adso.sql"])["dbt_dwh.zsd_a01_active"]
    assert "doc_number LowCardinality(String)" in a01 and "ENGINE = ReplacingMergeTree" in a01
    assert "Decimal(17,2)" in a01  # прочие типы из правил по умолчанию


@pytest.mark.parametrize("source", ["oracle", "mssql"])
def test_rdbms_tables_1to1(source):
    """Таблицы Oracle и MS SQL при переносе 1:1 тоже получают DDL ClickHouse; секции не переносятся."""
    from dbtransfer.importers import mssql_dict, oracle_dict

    data = ROOT / "tests" / "data"
    m = (oracle_dict.import_oracle_dict(data / "oracle_dict") if source == "oracle"
         else mssql_dict.import_mssql_dict(data / "mssql_dict"))
    files = generate(m, dialect="clickhouse").files()
    ddl = tables(files["045_tables.sql"])
    assert ddl and all("\nENGINE = MergeTree\n" in t for t in ddl.values())
    assert "PARTITION" not in files["045_tables.sql"] and "timestamptz" not in files["deploy_all.sql"]
    if source == "oracle":
        assert "updated_at Nullable(DateTime64(6, 'UTC'))" in ddl["dbt_sales.order_lines"]
        assert "ORDER BY order_id" in ddl["dbt_sales.orders"]
