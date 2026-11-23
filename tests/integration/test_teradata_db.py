"""Перенос из Teradata в живую цель: словарь DBC (синтетика), DDL, развёртывание, выгрузка данных через DB-API,
загрузка, сверка числа строк и сумм.

Живого Teradata в CI нет (Vantage Express — только образ виртуальной машины). Источник данных здесь — sqlite3:
это тоже драйвер DB-API, и выгрузка export_dbapi работает с ним так же, как с teradatasql (SELECT, fetchmany,
значения Python). Различия teradatasql (типы Decimal, datetime, bytes) проверяются в модульных тестах csv_value.
Цель — Greengage 6/7 или PostgreSQL из .env. Запуск: uv run pytest -m db
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dbtransfer.config import load_db_settings
from dbtransfer.db import DbError, connect, server_dialect
from dbtransfer.deploy import deploy
from dbtransfer.generator import TypeRules, generate
from dbtransfer.importers import teradata
from dbtransfer.transfer import compare_tables, transfer_data

pytestmark = pytest.mark.db

ROOT = Path(__file__).parents[2]
DATA = ROOT / "tests" / "data" / "teradata_dict"
PREFIX = "dbttd"
TABLES = ["SALES.Customers", "SALES.Orders", "SALES.OrderLines", "SALES.EventLog"]

SOURCE = """
CREATE TABLE SALES.Customers (CustomerId, Name, Region, Segment, CreatedAt);
INSERT INTO SALES.Customers VALUES (1, 'ООО «Ромашка»', 'RU', 1, '2024-01-15 10:30:00.123456'),
                                   (2, 'Smith & Co', 'BY', NULL, NULL);
CREATE TABLE SALES.Orders (OrderId, CustomerId, OrderDate, Amount, Status, Delivery, Notes);
INSERT INTO SALES.Orders VALUES (10, 1, '2024-03-15', 1500.5, 'NEW', '3 04:05:06', 'первый'),
                                (11, 1, '2024-12-31', 250.25, 'Shipped', NULL, NULL),
                                (12, 2, '2023-06-01', NULL, '', '0 00:30:00', '');
CREATE TABLE SALES.OrderLines (OrderId, LineNo, Sku, Qty, Price);
INSERT INTO SALES.OrderLines VALUES (10, 1, 'SKU-1', 2.5, 100.25), (10, 2, 'SKU-2', 1, 1300), (11, 1, 'SKU-1', 1, NULL);
CREATE TABLE SALES.EventLog (EventTs, EventType, Payload, Validity, RawData, Score, Weight);
INSERT INTO SALES.EventLog VALUES ('2024-01-01 10:00:00+03:00', 'CLICK', '{"a": 1, "b": [1, 2]}',
                                   '(''2024-01-01'', ''2024-12-31'')', X'0A0B', 0.5, 1000.25),
                                  ('2024-01-02 00:00:00+00:00', 'VIEW', NULL, NULL, NULL, NULL, NULL);
"""


@pytest.fixture
def servers():
    try:
        target = connect(load_db_settings(ROOT / ".env"), timeout=5)
    except DbError as e:
        pytest.skip(str(e))
    source = sqlite3.connect(":memory:")
    source.execute("ATTACH ':memory:' AS SALES")
    source.executescript(SOURCE)
    yield source, target
    target.rollback()
    with target.cursor() as cur:
        for layer in ("stg", "md", "dwh", "mart", "meta", "sales"):
            cur.execute(f"DROP SCHEMA IF EXISTS {PREFIX}_{layer} CASCADE")
    target.commit()
    target.close()
    source.close()


def rows(conn, sql: str) -> list[tuple]:
    result = conn.execute(sql).fetchall()
    conn.rollback()
    return result


def test_teradata_ddl_data_and_compare(servers, tmp_path):
    source, target = servers
    model = teradata.import_teradata(DATA)
    model.schema_prefix = PREFIX
    rules = TypeRules.load()
    package = generate(model, rules, server_dialect(target))
    result = deploy(target, package, recreate=True)
    assert result.ok, result.error

    loaded = transfer_data(source, target, model, rules, tmp_path, TABLES)
    assert {r.object_name: r.rows for r in loaded} == {
        "SALES.Customers": 2, "SALES.Orders": 3, "SALES.OrderLines": 3, "SALES.EventLog": 2}
    checks = compare_tables(source, target, model, rules, TABLES)
    assert all(c.ok for c in checks), [(c.table, c.problems) for c in checks]

    s = f"{PREFIX}_sales"
    assert rows(target, f"SELECT customerid, name, region, segment, createdat::text FROM {s}.customers ORDER BY 1") \
        == [(1, "ООО «Ромашка»", "RU", 1, "2024-01-15 10:30:00.123456"), (2, "Smith & Co", "BY", None, None)]
    got = rows(target, f"SELECT orderid, amount::text, status, delivery::text, notes FROM {s}.orders ORDER BY 1")
    assert got == [(10, "1500.50", "NEW", "3 days 04:05:06", "первый"), (11, "250.25", "Shipped", None, None),
                   (12, None, "", "00:30:00", "")]
    # Секции RANGE_N: заказ 2023 года — в секции по умолчанию (NO RANGE)
    if server_dialect(target).startswith("greenplum"):
        assert rows(target, f"SELECT count(*) FROM {s}.orders_1_prt_p_other") == [(1,)]
        assert rows(target, f"SELECT count(*) FROM {s}.orders_1_prt_p202412") == [(1,)]
    else:
        assert rows(target, f"SELECT count(*) FROM {s}.orders_p_other") == [(1,)]
    events = rows(target, f"SELECT eventtype, payload->>'a', validity, encode(rawdata, 'hex'), score, weight::text, "
                          f"eventts = '2024-01-01 07:00:00+00'::timestamptz FROM {s}.eventlog ORDER BY eventts")
    assert events == [("CLICK     ", "1", "('2024-01-01', '2024-12-31')", "0a0b", 0.5, "1000.25", True),
                      ("VIEW      ", None, None, None, None, None, False)]

    # Повторный перенос не удваивает строки (таблица без ключа EventLog очищается)
    transfer_data(source, target, model, rules, tmp_path, ["SALES.EventLog"])
    assert rows(target, f"SELECT count(*) FROM {s}.eventlog") == [(2,)]

    # Расхождение находится
    source.execute("INSERT INTO SALES.OrderLines VALUES (11, 2, 'SKU-3', 1, 5)")
    bad = {c.table: c for c in compare_tables(source, target, model, rules, ["SALES.OrderLines"])}
    assert not bad["SALES.OrderLines"].ok
    assert bad["SALES.OrderLines"].problems[0] == "строк 4 в источнике, 3 в цели"
