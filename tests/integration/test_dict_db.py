"""Перенос таблиц из словаря Oracle в живую базу: развёртывание, загрузка CSV, прогон, повторная загрузка,
сверка. Работает с Greengage и с PostgreSQL. Запуск: uv run pytest -m db
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from dbtransfer.config import load_db_settings
from dbtransfer.db import DbError, connect, server_dialect
from dbtransfer.deploy import deploy
from dbtransfer.generator import TypeRules, generate
from dbtransfer.importers.mssql_dict import import_mssql_dict
from dbtransfer.importers.oracle_dict import import_oracle_dict
from dbtransfer.importers.rdbms_dict import TARGET_ADSO
from dbtransfer.reconcile import reconcile
from dbtransfer.runtime import LoadError, load_file, run_load

pytestmark = pytest.mark.db

ROOT = Path(__file__).parents[2]
DATA = ROOT / "tests" / "data"
PREFIX = "dbtora"


@pytest.fixture
def conn():
    try:
        connection = connect(load_db_settings(ROOT / ".env"), timeout=5)
    except DbError as e:
        pytest.skip(str(e))
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        for layer in ("stg", "md", "dwh", "mart", "meta", "sales", "dbo"):
            cur.execute(f"DROP SCHEMA IF EXISTS {PREFIX}_{layer} CASCADE")
    connection.commit()
    connection.close()


def test_oracle_tables_flow(conn, tmp_path):
    model = import_oracle_dict(DATA / "oracle_dict", target=TARGET_ADSO)
    model.schema_prefix = PREFIX
    rules = TypeRules.load()
    package = generate(model, rules, server_dialect(conn))
    result = deploy(conn, package, recreate=True)
    assert result.ok, result.error

    first = load_file(conn, model, rules, "SALES_ORDERS", DATA / "oracle_data" / "SALES_ORDERS.csv")
    assert first.rows == 3
    run = run_load(conn, model, package, first.load_id, "SALES_ORDERS")
    assert [s.function for s in run.steps] == [f"{PREFIX}_dwh.tr_sales_orders__sales_orders",
                                               f"{PREFIX}_dwh.activate_sales_orders"]
    second = load_file(conn, model, rules, "SALES_ORDERS", DATA / "oracle_data" / "SALES_ORDERS_update.csv")
    run_load(conn, model, package, second.load_id, "SALES_ORDERS")

    rows = conn.execute(f"SELECT order_id, amount, status, order_date::text, total_weight "
                        f"FROM {PREFIX}_dwh.sales_orders_active ORDER BY 1").fetchall()
    conn.rollback()
    assert [(r[0], str(r[1]) if r[1] is not None else None, r[2]) for r in rows] == [
        (1, "1500.50", "NEW"), (2, "250.00", "SHIPPED"), (3, None, "NEW"), (4, "99.99", "NEW")]
    assert rows[0][3] == "2026-01-15 10:30:00"
    assert str(rows[2][4]) == "1000"  # NUMBER без точности -> numeric, 1e3 из CSV

    assert reconcile(conn, model, "SALES_ORDERS", DATA / "oracle_data" / "etalon_SALES_ORDERS.csv", tmp_path).ok

    # Составной ключ из уникального ограничения, timestamptz, double precision и bytea
    lines = load_file(conn, model, rules, "SALES_ORDER_LINES", DATA / "oracle_data" / "SALES_ORDER_LINES.csv")
    run_load(conn, model, package, lines.load_id, "SALES_ORDER_LINES")
    got = conn.execute(f"SELECT line_no, price, encode(checksum, 'hex'), updated_at IS NOT NULL "
                       f"FROM {PREFIX}_dwh.sales_order_lines_active ORDER BY 1").fetchall()
    conn.rollback()
    assert got == [(1, 10.5, "0a0b", True), (2, 125.0, None, True)]


def test_oracle_load_rejects_bad_timestamp(conn, tmp_path):
    model = import_oracle_dict(DATA / "oracle_dict", target=TARGET_ADSO)
    model.schema_prefix = PREFIX
    rules = TypeRules.load()
    assert deploy(conn, generate(model, rules, server_dialect(conn)), recreate=True).ok
    bad = tmp_path / "bad.csv"
    bad.write_text("ORDER_ID,CUSTOMER_ID,ORDER_DATE\n1,10,15.01.26 10:30\n", encoding="utf-8")
    with pytest.raises(LoadError) as e:
        load_file(conn, model, rules, "SALES_ORDERS", bad)
    assert [x.problem for x in e.value.errors] == ["не дата и время ГГГГ-ММ-ДД ЧЧ:ММ:СС"]


def test_mssql_table_flow(conn):
    model = import_mssql_dict(DATA / "mssql_dict", target=TARGET_ADSO)
    model.schema_prefix = PREFIX
    rules = TypeRules.load()
    package = generate(model, rules, server_dialect(conn))
    assert deploy(conn, package, recreate=True).ok
    loaded = load_file(conn, model, rules, "dbo_Order", DATA / "mssql_data" / "dbo_Order.csv")
    run_load(conn, model, package, loaded.load_id, "dbo_Order")
    rows = conn.execute(f'SELECT orderid, created::text, total, "comment", encode(rowver, \'hex\'), shiptime::text '
                        f"FROM {PREFIX}_dwh.dbo_order_active ORDER BY 1").fetchall()
    conn.rollback()
    assert rows == [(100, "2026-05-01 09:00:00.123457", Decimal("10.5000"), "без НДС", "00000000000007d1",
                     "10:15:00"),
                    (101, "2026-05-02 18:30:00", None, "", "00000000000007d2", None)]


# --- перенос 1:1 (режим по умолчанию) ---

def test_oracle_partitioned_table_1to1(conn, tmp_path):
    model = import_oracle_dict(DATA / "oracle_dict")
    model.schema_prefix = PREFIX
    rules = TypeRules.load()
    package = generate(model, rules, server_dialect(conn))
    result = deploy(conn, package, recreate=True)
    assert result.ok, result.error
    names = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", [f"{PREFIX}_sales"])}
    conn.rollback()
    assert {"orders", "customers", "order_lines", "log_events"} <= names
    assert not any(n.endswith(("_inbound", "_active", "_changelog")) for n in names)

    first = load_file(conn, model, rules, "SALES.ORDERS", DATA / "oracle_data" / "SALES_ORDERS.csv")
    assert first.rows == 3
    load_file(conn, model, rules, "SALES.ORDERS", DATA / "oracle_data" / "SALES_ORDERS_update.csv")
    rows = conn.execute(f"SELECT order_id, amount, status FROM {PREFIX}_sales.orders ORDER BY 1").fetchall()
    conn.rollback()
    assert [(r[0], str(r[1]) if r[1] is not None else None, r[2]) for r in rows] == [
        (1, "1500.50", "NEW"), (2, "250.00", "SHIPPED"), (3, None, "NEW"), (4, "99.99", "NEW")]
    assert reconcile(conn, model, "SALES.ORDERS", DATA / "oracle_data" / "etalon_SALES_ORDERS.csv", tmp_path).ok

    # Строки разошлись по секциям: в Greenplum и в PostgreSQL это отдельные таблицы-наследники
    parts = conn.execute(f"SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhparent "
                         f"JOIN pg_namespace n ON n.oid = c.relnamespace "
                         f"WHERE n.nspname = '{PREFIX}_sales' AND c.relname = 'orders'").fetchone()[0]
    conn.rollback()
    assert parts == 3


def test_mssql_ci_key_upsert_and_reconcile(conn, tmp_path):
    model = import_mssql_dict(DATA / "mssql_dict")
    model.schema_prefix = PREFIX
    rules = TypeRules.load()
    package = generate(model, rules, server_dialect(conn))
    assert deploy(conn, package, recreate=True).ok, "deploy"
    load_file(conn, model, rules, "dbo.Country", DATA / "mssql_data" / "dbo_Country.csv")
    load_file(conn, model, rules, "dbo.Country", DATA / "mssql_data" / "dbo_Country_update.csv")
    rows = conn.execute(f"SELECT code, name FROM {PREFIX}_dbo.country ORDER BY lower(code)").fetchall()
    conn.rollback()
    # В MS SQL ключ без учёта регистра: RU заменил ru, а не добавился рядом
    assert rows == [("KZ", "Казахстан"), ("RU", "Российская Федерация")]
    assert reconcile(conn, model, "dbo.Country", DATA / "mssql_data" / "etalon_dbo_Country.csv", tmp_path).ok

    sale = load_file(conn, model, rules, "sales.Sale", DATA / "mssql_data" / "sales_Sale.csv")
    assert sale.rows == 3
    got = conn.execute(f"SELECT saleid, saledate::text, amount FROM {PREFIX}_sales.sale ORDER BY 1").fetchall()
    conn.rollback()
    assert got == [(1, "2024-12-31", Decimal("10.00")), (2, "2025-06-01", Decimal("20.50")),
                   (3, "2026-02-01", Decimal("5.00"))]


def test_table_load_dedupes_within_file(conn, tmp_path):
    model = import_oracle_dict(DATA / "oracle_dict")
    model.schema_prefix = PREFIX
    rules = TypeRules.load()
    assert deploy(conn, generate(model, rules, server_dialect(conn)), recreate=True).ok
    dup = tmp_path / "dup.csv"
    dup.write_text("ORDER_ID,CUSTOMER_ID,ORDER_DATE,STATUS\n1,10,2026-01-15 10:30:00,NEW\n"
                   "1,10,2026-01-15 10:30:00,PAID\n", encoding="utf-8")
    load_file(conn, model, rules, "SALES.ORDERS", dup)
    rows = conn.execute(f"SELECT order_id, status FROM {PREFIX}_sales.orders").fetchall()
    conn.rollback()
    assert rows == [(1, "PAID")]  # побеждает последняя строка файла


def test_mssql_cp1251_file_and_fractional_seconds(conn, tmp_path):
    model = import_mssql_dict(DATA / "mssql_dict")
    model.schema_prefix = PREFIX
    rules = TypeRules.load()
    assert deploy(conn, generate(model, rules, server_dialect(conn)), recreate=True).ok
    # Выгрузка из базы с collation Cyrillic_General_CI_AS часто приходит в Windows-1251
    legacy = tmp_path / "dbo_Country.csv"
    legacy.write_bytes("Code;Name;Capital\nRU;Россия;Москва\n".encode("cp1251"))
    load_file(conn, model, rules, "dbo.Country", legacy)
    assert conn.execute(f"SELECT capital FROM {PREFIX}_dbo.country").fetchone()[0] == "Москва"
    conn.rollback()
    # datetime2(7): 100 нс округляются до микросекунд
    load_file(conn, model, rules, "dbo.Order", DATA / "mssql_data" / "dbo_Order.csv")
    got = conn.execute(f"SELECT created::text FROM {PREFIX}_dbo.order WHERE orderid = 100").fetchone()[0]
    conn.rollback()
    assert got == "2026-05-01 09:00:00.123457"
