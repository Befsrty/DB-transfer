"""Сквозной перенос Greenplum -> Greengage: каталог источника, DDL, развёртывание, данные, сверка сумм и строк.

Источник — схема gpsrc_sales, которую тест создаёт сам. По умолчанию она создаётся на том же сервере, что и цель
(так в матрице CI проверяется чтение каталога и Greengage 6, и Greengage 7). Если задан DBT_GP_SRC_URL
(greenplum://user:пароль@host:port/база), источник — отдельный сервер: в CI это Greengage 6.32 (каталог
Greenplum 6 и Yandex MPP 6.x), а цель — Greengage 7.5.1 (ADB 7.5). На обычном PostgreSQL тест тоже идёт,
без распределения и AO. Запуск: uv run pytest -m db
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from dbtransfer.config import load_db_settings
from dbtransfer.db import DbError, connect, server_dialect
from dbtransfer.deploy import deploy
from dbtransfer.generator import TypeRules, generate
from dbtransfer.importers import greenplum
from dbtransfer.model import ObjectType
from dbtransfer.transfer import compare_tables, transfer_data

pytestmark = pytest.mark.db

ROOT = Path(__file__).parents[2]
SRC = "gpsrc_sales"
PREFIX = "dbtgp"
TARGET = f"{PREFIX}_{SRC}"

# Общая часть DDL источника; {mpp_*} — предложения Greenplum, на PostgreSQL пустые
SOURCE_GREENPLUM = f"""
CREATE SCHEMA {SRC};
CREATE TABLE {SRC}.regions (code char(2) PRIMARY KEY, name varchar(100) NOT NULL) DISTRIBUTED REPLICATED;
COMMENT ON TABLE {SRC}.regions IS 'Справочник регионов';
CREATE TABLE {SRC}.orders (
    order_id bigint PRIMARY KEY, region char(2), amount numeric(15,2), qty integer, price double precision,
    created timestamp(3), attrs jsonb, flag boolean, tags text[], note varchar(50), payload bytea
) DISTRIBUTED BY (order_id);
CREATE TABLE {SRC}.order_lines (order_id bigint NOT NULL, line_no integer NOT NULL, price double precision,
    qty numeric(12,3))
WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=3) DISTRIBUTED BY (order_id);
CREATE TABLE {SRC}.fact_sales (sale_date date NOT NULL, region char(2) NOT NULL, qty integer, amount numeric(18,2))
WITH (appendonly=true, orientation=row, compresstype=zstd, compresslevel=5) DISTRIBUTED RANDOMLY
PARTITION BY RANGE (sale_date) (
    START (date '2024-01-01') INCLUSIVE END (date '2025-01-01') EXCLUSIVE EVERY (interval '6 months'),
    PARTITION h2025 START (date '2025-07-01') INCLUSIVE END (date '2026-01-01') EXCLUSIVE,
    DEFAULT PARTITION other);
CREATE TABLE {SRC}.customers (id integer, country char(2), name text, PRIMARY KEY (id, country))
DISTRIBUTED BY (id) PARTITION BY LIST (country) (
    PARTITION ru VALUES ('RU'), PARTITION kz VALUES ('KZ', 'BY'), DEFAULT PARTITION other);
"""
SOURCE_POSTGRES = f"""
CREATE SCHEMA {SRC};
CREATE TABLE {SRC}.regions (code char(2) PRIMARY KEY, name varchar(100) NOT NULL);
COMMENT ON TABLE {SRC}.regions IS 'Справочник регионов';
CREATE TABLE {SRC}.orders (
    order_id bigint PRIMARY KEY, region char(2), amount numeric(15,2), qty integer, price double precision,
    created timestamp(3), attrs jsonb, flag boolean, tags text[], note varchar(50), payload bytea);
CREATE TABLE {SRC}.order_lines (order_id bigint NOT NULL, line_no integer NOT NULL, price double precision,
    qty numeric(12,3));
CREATE TABLE {SRC}.fact_sales (sale_date date NOT NULL, region char(2) NOT NULL, qty integer, amount numeric(18,2))
PARTITION BY RANGE (sale_date);
CREATE TABLE {SRC}.fact_sales_1_prt_2 PARTITION OF {SRC}.fact_sales FOR VALUES FROM ('2024-01-01') TO ('2024-07-01');
CREATE TABLE {SRC}.fact_sales_1_prt_3 PARTITION OF {SRC}.fact_sales FOR VALUES FROM ('2024-07-01') TO ('2025-01-01');
CREATE TABLE {SRC}.fact_sales_1_prt_h2025 PARTITION OF {SRC}.fact_sales
    FOR VALUES FROM ('2025-07-01') TO ('2026-01-01');
CREATE TABLE {SRC}.fact_sales_1_prt_other PARTITION OF {SRC}.fact_sales DEFAULT;
CREATE TABLE {SRC}.customers (id integer, country char(2), name text, PRIMARY KEY (id, country))
PARTITION BY LIST (country);
CREATE TABLE {SRC}.customers_1_prt_ru PARTITION OF {SRC}.customers FOR VALUES IN ('RU');
CREATE TABLE {SRC}.customers_1_prt_kz PARTITION OF {SRC}.customers FOR VALUES IN ('KZ', 'BY');
CREATE TABLE {SRC}.customers_1_prt_other PARTITION OF {SRC}.customers DEFAULT;
"""
SOURCE_COMMON = f"""
CREATE VIEW {SRC}.v_daily AS SELECT sale_date, sum(amount) AS amount FROM {SRC}.fact_sales GROUP BY sale_date;
CREATE FUNCTION {SRC}.load_day(p_day date) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
  DELETE FROM {SRC}.fact_sales WHERE sale_date = p_day;
  INSERT INTO {SRC}.fact_sales SELECT p_day, region, 1, 0 FROM {SRC}.regions;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
INSERT INTO {SRC}.regions VALUES ('RU', 'Россия'), ('KZ', 'Казахстан'), ('BY', 'Беларусь');
INSERT INTO {SRC}.orders VALUES
    (1, 'RU', 1500.50, 3, 0.1, '2026-01-15 10:30:00.123', '{{"vip": true}}', true, '{{a,b}}', 'первый', '\\x0a0b'),
    (2, 'KZ', 250.25, NULL, 1e-20, NULL, NULL, NULL, NULL, '', NULL),
    (3, 'RU', NULL, 1, 2.5, '2024-02-29 00:00:00', '{{}}', false, '{{}}', NULL, '\\x');
INSERT INTO {SRC}.order_lines SELECT i % 100, i, i / 7.0, i * 0.125 FROM generate_series(1, 2000) i;
INSERT INTO {SRC}.fact_sales
SELECT date '2024-01-01' + (i % 900), (ARRAY['RU', 'KZ', 'BY'])[1 + i % 3], i % 17, (i * 13 % 1000) / 4.0
FROM generate_series(1, 5000) i;
INSERT INTO {SRC}.customers VALUES (1, 'RU', 'Ромашка'), (2, 'KZ', 'Алтын'), (3, 'BY', 'Зубр'), (4, 'AM', 'Арарат');
"""


def _target():
    try:
        return connect(load_db_settings(ROOT / ".env"), timeout=5)
    except DbError as e:
        pytest.skip(str(e))


def _drop(conn, *schemas):
    conn.rollback()
    with conn.cursor() as cur:
        for s in schemas:
            cur.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
    conn.commit()


@pytest.fixture
def servers():
    target = _target()
    url = os.environ.get("DBT_GP_SRC_URL")
    source = greenplum.connect(url) if url else connect(load_db_settings(ROOT / ".env"), timeout=5)
    source.autocommit = False
    version = source.execute("SELECT version()").fetchone()[0]
    mpp = greenplum.is_greenplum(version)
    _drop(source, SRC)
    _drop(target, *(f"{PREFIX}_{x}" for x in ("stg", "md", "dwh", "mart", "meta", SRC)))
    with source.cursor() as cur:
        cur.execute((SOURCE_GREENPLUM if mpp else SOURCE_POSTGRES) + SOURCE_COMMON)
    source.commit()
    yield source, target, mpp
    _drop(source, SRC)
    _drop(target, *(f"{PREFIX}_{x}" for x in ("stg", "md", "dwh", "mart", "meta", SRC)))
    source.close()
    target.close()


def one(conn, sql: str):
    value = conn.execute(sql).fetchone()[0]
    conn.rollback()
    return value


def test_greenplum_catalog_ddl_data_and_reconcile(servers, tmp_path):
    source, target, mpp = servers
    major = os.environ.get("DBT_EXPECT_SOURCE_MAJOR")
    if major:  # в сквозном прогоне CI источник — Greenplum 6 (ядро 9.4), а не тот же сервер
        assert source.info.server_version // 10000 == (9 if major == "6" else int(major)), source.info.server_version

    model = greenplum.import_greenplum(conn=source, schemas=[SRC])
    model.schema_prefix = PREFIX
    assert sorted(model.tables) == [f"{SRC}.{t}" for t in ("customers", "fact_sales", "order_lines", "orders",
                                                            "regions")]
    fact = model.tables[f"{SRC}.fact_sales"]
    assert [p.name for p in fact.partitioning.partitions] == ["p2", "p3", "h2025", "other"]
    assert model.tables[f"{SRC}.customers"].partitioning.kind == "LIST"
    assert model.tables[f"{SRC}.orders"].key == ["order_id"]
    if mpp:
        assert model.tables[f"{SRC}.regions"].distribution == "REPLICATED"
        assert fact.distribution == "RANDOM" and fact.storage == "ao_row"
        assert model.tables[f"{SRC}.order_lines"].storage == "ao_column"
        assert model.tables[f"{SRC}.order_lines"].distributed_by == ["order_id"]
    kinds = {o.name: o.type for o in model.others}
    assert kinds[f"{SRC}.load_day(p_day date)"] == ObjectType.DB_ROUTINE
    assert kinds[f"{SRC}.v_daily"] == ObjectType.VIEW

    rules = TypeRules.load()
    package = generate(model, rules, server_dialect(target))
    result = deploy(target, package, recreate=True)
    assert result.ok, result.error

    children = one(target, f"SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhparent "
                           f"JOIN pg_namespace n ON n.oid = c.relnamespace "
                           f"WHERE n.nspname = '{TARGET}' AND c.relname = 'fact_sales'")
    assert children == 4
    if server_dialect(target).startswith("greenplum"):
        assert one(target, f"SELECT pg_get_table_distributedby('{TARGET}.regions'::regclass)") \
            == "DISTRIBUTED REPLICATED"
        assert one(target, f"SELECT pg_get_table_distributedby('{TARGET}.order_lines'::regclass)") \
            == "DISTRIBUTED BY (order_id)"

    loaded = transfer_data(source, target, model, rules, tmp_path)
    assert {r.object_name: r.rows for r in loaded} == {
        f"{SRC}.customers": 4, f"{SRC}.fact_sales": 5000, f"{SRC}.order_lines": 2000, f"{SRC}.orders": 3,
        f"{SRC}.regions": 3}
    checks = compare_tables(source, target, model, rules)
    assert [c.problems for c in checks if not c.ok] == []
    orders = next(c for c in checks if c.table == f"{SRC}.orders")
    assert {c.column: c.target for c in orders.sums}["amount"] == Decimal("1750.75")

    rows = target.execute(f"SELECT order_id, note, attrs::text, tags, encode(payload, 'hex'), flag, price, "
                          f"created::text FROM {TARGET}.orders ORDER BY 1").fetchall()
    target.rollback()
    assert rows == [
        (1, "первый", '{"vip": true}', ["a", "b"], "0a0b", True, 0.1, "2026-01-15 10:30:00.123"),
        (2, "", None, None, None, None, 1e-20, None),  # пустая строка осталась пустой, NULL — NULL
        (3, None, "{}", [], "", False, 2.5, "2024-02-29 00:00:00"),
    ]
    # Строки вне секций (разрыв 2025-01 … 2025-07 и после 2026) легли в секцию по умолчанию, как в источнике
    other = "fact_sales_1_prt_other" if server_dialect(target).startswith("greenplum") else "fact_sales_other"
    assert one(target, f"SELECT count(*) FROM {TARGET}.{other}") == one(
        source, f"SELECT count(*) FROM {SRC}.fact_sales_1_prt_other") > 0

    # Повторный перенос полный: строки таблиц без ключа не удваиваются
    transfer_data(source, target, model, rules, tmp_path, [f"{SRC}.order_lines"])
    assert one(target, f"SELECT count(*) FROM {TARGET}.order_lines") == 2000

    # Расхождение видно в сверке
    target.execute(f"DELETE FROM {TARGET}.order_lines WHERE line_no = 7")
    target.commit()
    bad = compare_tables(source, target, model, rules, [f"{SRC}.order_lines"])[0]
    assert not bad.ok and bad.problems[0] == "строк 2000 в источнике, 1999 в цели"


def test_transfer_keeps_old_data_when_load_fails(servers, tmp_path):
    source, target, _ = servers
    model = greenplum.import_greenplum(conn=source, schemas=[SRC])
    model.schema_prefix = PREFIX
    rules = TypeRules.load()
    assert deploy(target, generate(model, rules, server_dialect(target)), recreate=True).ok
    transfer_data(source, target, model, rules, tmp_path, [f"{SRC}.regions"])
    # В источнике появилось значение длиннее, чем допускает модель (varchar(100) сузили в модели до 3)
    model.tables[f"{SRC}.regions"].fields[1].length = 3
    with pytest.raises(Exception, match="не загружен"):
        transfer_data(source, target, model, rules, tmp_path, [f"{SRC}.regions"])
    assert one(target, f"SELECT count(*) FROM {TARGET}.regions") == 3


def test_source_url_is_checked():
    with pytest.raises(greenplum.DictImportFailed):
        greenplum.connect("mysql://x@localhost/db")
    with pytest.raises(psycopg.OperationalError):
        greenplum.connect("greenplum://nobody@127.0.0.1:1/none")
