"""Перенос таблиц РСУБД 1:1 (режим по умолчанию): модель, секционирование, CI-ключи, DDL, цель загрузки."""

from pathlib import Path

import pytest

from dbtransfer.analysis.assessment import assess
from dbtransfer.generator import TypeRules, generate
from dbtransfer.importers import mssql_dict, oracle_dict
from dbtransfer.importers.rdbms_dict import TARGET_ADSO
from dbtransfer.model import ObjectType, Severity
from dbtransfer.runtime import resolve_target

DATA = Path(__file__).parents[1] / "data"
RULES = TypeRules.load()


@pytest.fixture(scope="module")
def ora():
    return oracle_dict.import_oracle_dict(DATA / "oracle_dict")


@pytest.fixture(scope="module")
def ms():
    return mssql_dict.import_mssql_dict(DATA / "mssql_dict")


def warnings(model):
    return [i.message for i in model.issues if i.severity == Severity.WARNING]


def test_default_mode_is_table(ora, ms):
    assert sorted(ora.tables) == ["SALES.CUSTOMERS", "SALES.LOG_EVENTS", "SALES.ORDERS", "SALES.ORDER_LINES"]
    assert not ora.adsos and not ora.datasources and not ora.transformations and not ora.dtps
    orders = ora.tables["SALES.ORDERS"]
    assert orders.key == ["ORDER_ID"] and orders.description == "Заказы клиентов"
    assert ora.tables["SALES.ORDER_LINES"].key == ["ORDER_ID", "LINE_NO"]
    assert not ora.tables["SALES.LOG_EVENTS"].key
    assert "dbo.Country" in ms.tables and "sales.Sale" in ms.tables


def test_find_table_by_names(ora):
    t = ora.tables["SALES.ORDERS"]
    assert ora.find("SALES.ORDERS") is t and ora.find("sales_orders") is t and ora.find("orders") is t
    assert ora.object(t.ref) is t and t.ref.type == ObjectType.TABLE


def test_view_related_points_to_tables(ora):
    view = next(o for o in ora.others if o.name == "SALES.V_SALES_BY_REGION")
    assert {r.name for r in view.related} == {"SALES.ORDERS", "SALES.CUSTOMERS"}
    assert all(r.type == ObjectType.TABLE for r in view.related)


def test_oracle_partitioning(ora):
    p = ora.tables["SALES.ORDERS"].partitioning
    assert p.kind == "RANGE" and p.columns == ["ORDER_DATE"]
    assert [(x.name, x.values) for x in p.partitions] == [
        ("P_2025", ["TIMESTAMP '2026-01-01 00:00:00'"]), ("P_2026", ["TIMESTAMP '2027-01-01 00:00:00'"]),
        ("P_MAX", ["MAXVALUE"])]
    assert "интервальное" in p.note
    lst = ora.tables["SALES.CUSTOMERS"].partitioning
    assert lst.kind == "LIST" and [x.values for x in lst.partitions] == [["'C'", "'M'"], ["DEFAULT"]]
    assert lst.partitions[1].is_default
    # HASH не переносится: таблица без секций, но с пояснением
    assert ora.tables["SALES.LOG_EVENTS"].partitioning is None
    assert any("HASH" in i.message for i in ora.issues)


@pytest.mark.parametrize("value, expected", [
    ("TO_DATE(' 2026-01-01 00:00:00', 'SYYYY-MM-DD HH24:MI:SS', 'NLS_CALENDAR=GREGORIAN')",
     "TIMESTAMP '2026-01-01 00:00:00'"),
    ("TIMESTAMP' 2026-01-01 00:00:00'", "TIMESTAMP '2026-01-01 00:00:00'"),
    ("100", "100"), ("'A'", "'A'"), ("MAXVALUE", "MAXVALUE"), ("DEFAULT", "DEFAULT"),
])
def test_oracle_bound_literal(value, expected):
    assert oracle_dict.bound_literal(value) == expected


def test_oracle_split_values():
    assert oracle_dict.split_values("'A', 'B,C', TO_DATE('x', 'y')") == ["'A'", "'B,C'", "TO_DATE('x', 'y')"]


def test_oracle_multicolumn_range_skipped():
    p, notes = oracle_dict.partitioning_from("RANGE", "", "", ["A", "B"], [("P1", "1, 2")], "S.T")
    assert p is None and "нескольким колонкам" in notes


def test_mssql_partitioning_and_ci(ms):
    sale = ms.tables["sales.Sale"]
    p = sale.partitioning
    assert p.kind == "RANGE" and p.columns == ["SaleDate"] and not p.upper_inclusive  # RANGE RIGHT
    assert [x.values for x in p.partitions] == [["DATE '2025-01-01'"], ["DATE '2026-01-01'"], ["MAXVALUE"]]
    assert sale.key == ["SaleId", "SaleDate"]
    country = ms.tables["dbo.Country"]
    assert country.key == ["Code"] and "Code" in country.ci_columns
    assert any("dbo.Country" in m and "регистр" in m for m in warnings(ms))


def test_mssql_range_left_is_inclusive():
    rows = [{"COLUMN_NAME": "D", "FUNCTION_NAME": "pf", "RANGE_RIGHT": "0", "BOUNDARY_ID": str(i),
             "BOUNDARY_VALUE": v, "VALUE_TYPE": "int"} for i, v in ((1, "10"), (2, "20"))]
    p, _ = mssql_dict.partitioning_from(rows, "dbo.T")
    assert p.upper_inclusive and [x.values for x in p.partitions] == [["10"], ["20"], ["MAXVALUE"]]


def test_greenplum_ddl(ora, ms):
    package = generate(ora, RULES)
    files = package.files()
    sql = files["045_tables.sql"]
    assert "Объектов нет" in files["050_adso.sql"] and "Объектов нет" in files["040_datasources.sql"]
    assert "CREATE TABLE IF NOT EXISTS dbt_sales.orders (" in sql
    assert "PARTITION BY RANGE (order_date)" in sql
    assert "END (TIMESTAMP '2026-01-01 00:00:00') EXCLUSIVE" in sql and "DEFAULT PARTITION p_max" in sql
    assert "PARTITION BY LIST (region)" in sql and "VALUES ('C', 'M')" in sql
    assert "PRIMARY KEY (order_id, line_no)" in sql
    # Ключ ORDERS не содержит колонку секционирования: PK снят с предупреждением
    assert any("SALES.ORDERS" in w and "первичный ключ" in w for w in package.warnings)
    assert "inbound" not in sql and "active" not in sql and "changelog" not in sql
    ms_sql = generate(ms, RULES).files()["045_tables.sql"]
    assert "PRIMARY KEY (saleid, saledate)" in ms_sql and "PARTITION BY RANGE (saledate)" in ms_sql


def test_postgres_ddl(ms):
    sql = generate(ms, RULES, "postgres").files()["045_tables.sql"]
    assert "PARTITION BY RANGE (saledate)" in sql
    assert "PARTITION OF dbt_sales.sale" in sql and "FOR VALUES FROM (MINVALUE) TO (DATE '2025-01-01')" in sql
    assert "DEFAULT" in sql and "DISTRIBUTED" not in sql


def test_adso_mode_skips_partitioning():
    model = oracle_dict.import_oracle_dict(DATA / "oracle_dict", target=TARGET_ADSO)
    assert not model.tables and model.adsos
    assert any("режиме ADSO не переносится" in i.message for i in model.issues)


def test_load_target(ms, ora):
    target = resolve_target(ms, RULES, "dbo_Country", [])
    assert target.kind == "table" and target.key == ["code"] and target.ci_key == ["code"]
    assert target.table.schema == "dbt_dbo" and target.table.name == "country"
    keyless = resolve_target(ora, RULES, "SALES_LOG_EVENTS", [])
    assert keyless.kind == "table" and keyless.key == []


def test_assessment_tables(ora):
    by_name = {a.name: a for a in assess(ora).objects}
    assert by_name["SALES.ORDERS"].type == "Таблица"
    assert by_name["SALES.LOG_EVENTS"].type == "Таблица без ключа"
