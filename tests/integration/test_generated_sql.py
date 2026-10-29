"""Сгенерированный SQL в живой базе: развёртывание, повтор, прогон примера приложения Д (ПИ-18).

Тесты работают и с Greengage (диалект greenplum), и с обычным PostgreSQL (диалект postgres):
диалект выбирается по версии сервера. Объекты создаются в отдельных схемах dbtest_*,
демо-схемы dbt_* не трогаются. Запуск: uv run pytest -m db
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from dbtransfer import naming
from dbtransfer.config import load_db_settings
from dbtransfer.db import DbError, connect
from dbtransfer.generator import TypeRules, generate
from dbtransfer.model import Model

pytestmark = pytest.mark.db

ROOT = Path(__file__).parents[2]
MICRO = ROOT / "demo" / "data" / "micro"
PREFIX = "dbtest"


def _value(raw: str, datatype: str):
    if datatype == "DATS":
        return None if raw in ("", "00000000") else date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    if datatype in ("CURR", "QUAN", "DEC"):
        return Decimal(raw.replace(",", ".")) if raw else None
    return raw


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


@pytest.fixture(scope="module")
def model() -> Model:
    from dbtransfer.importers.bw_csv import import_bw_export

    m = import_bw_export(ROOT / "demo" / "bw_export")
    m.schema_prefix = PREFIX
    return m


@pytest.fixture(scope="module")
def conn(model):
    settings = load_db_settings(ROOT / ".env")
    try:
        connection = connect(settings, timeout=5)
    except DbError as e:
        pytest.skip(str(e))
    connection.autocommit = True
    version = connection.execute("SELECT version()").fetchone()[0]
    connection.dialect = "greenplum" if ("Greenplum" in version or "Greengage" in version) else "postgres"
    connection.package = generate(model, TypeRules.load(), dialect=connection.dialect)
    _drop(connection)
    yield connection
    _drop(connection)
    connection.close()


def _drop(connection) -> None:
    for layer in ("stg", "md", "dwh", "mart", "meta"):
        connection.execute(f"DROP SCHEMA IF EXISTS {PREFIX}_{layer} CASCADE")


def _deploy(connection) -> None:
    script = connection.package.files()["deploy_all.sql"].replace("\\set ON_ERROR_STOP on", "")
    connection.execute(script)


def _load_file(connection, model: Model, path: Path, load_id: int) -> None:
    ds = model.find("ZSALES_FF")
    table = naming.data_table(model, ds)
    columns = [naming.column(f.name) for f in ds.fields]
    connection.execute(f"INSERT INTO {PREFIX}_meta.load_log (load_id, object_name, file_name, status) "
                       "VALUES (%s, %s, %s, 'loaded')", (load_id, ds.name, path.name))
    with connection.cursor() as cur:
        for record_no, row in enumerate(_read(path), start=1):
            values = [_value(row[f.name], f.datatype) for f in ds.fields]
            cur.execute(f"INSERT INTO {table} (load_id, record_no, {', '.join(columns)}) "
                        f"VALUES (%s, %s, {', '.join(['%s'] * len(columns))})", [load_id, record_no, *values])


def _run(connection, load_id: int) -> None:
    """Прогон загрузки по потоку ZSALES_FF → ZSD_A01 → ZSD_A02 одной транзакцией (как в шаге 4)."""
    with connection.transaction():
        for fn in ("dwh.tr_zsales_ff__zsd_a01", "dwh.activate_zsd_a01", "mart.tr_zsd_a01__zsd_a02",
                   "mart.activate_zsd_a02"):
            connection.execute(f"SELECT {PREFIX}_{fn}(%s)", (load_id,))


def _expected(model: Model, adso: str, path: Path, extra: tuple[str, ...] = ()) -> list[tuple]:
    obj = model.adsos[adso]
    rows = []
    for row in _read(path):
        values = [row[c] if c != "load_id" else int(row[c]) for c in extra]
        values += [_value(row[f.name], f.datatype) for f in obj.fields]
        rows.append(tuple(values))
    return sorted(rows, key=repr)


def _actual(connection, model: Model, adso: str, table: str, extra: tuple[str, ...] = ()) -> list[tuple]:
    obj = model.adsos[adso]
    columns = list(extra) + [naming.column(f.name, f.info_object) for f in obj.fields]
    rows = connection.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
    return sorted((tuple(r) for r in rows), key=repr)


def test_deploy_twice(conn):
    _deploy(conn)
    _deploy(conn)  # повторное развёртывание без ошибок (IF NOT EXISTS, CREATE OR REPLACE)
    tables = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema LIKE %s",
                          (f"{PREFIX}_%",)).fetchone()[0]
    assert tables == sum(1 for o in conn.package.objects if o.kind == "table")


def test_appendix_d_flow(conn, model):
    """ПИ-18: две загрузки из demo/data/micro дают ровно строки приложения Д."""
    _deploy(conn)
    for load_id, name in ((1, "ZSALES_FF_load1.csv"), (2, "ZSALES_FF_load2.csv")):
        _load_file(conn, model, MICRO / name, load_id)
        _run(conn, load_id)

    a01 = naming.data_table(model, model.adsos["ZSD_A01"])
    assert _actual(conn, model, "ZSD_A01", a01) == _expected(model, "ZSD_A01", MICRO / "expected_ZSD_A01.csv")
    log = f"{PREFIX}_dwh.zsd_a01_changelog"
    assert (_actual(conn, model, "ZSD_A01", log, ("load_id", "recordmode"))
            == _expected(model, "ZSD_A01", MICRO / "expected_ZSD_A01_changelog.csv", ("load_id", "recordmode")))
    a02 = naming.data_table(model, model.adsos["ZSD_A02"])
    assert _actual(conn, model, "ZSD_A02", a02) == _expected(model, "ZSD_A02", MICRO / "expected_ZSD_A02.csv")

    message = conn.execute(f"SELECT message FROM {PREFIX}_meta.load_log WHERE load_id = 2").fetchone()[0]
    assert "ZSD_A01: активировано 6 записей, новых 1, изменённых 5" in message


def test_stub_blocks_run(conn):
    """ФП-5.9: заготовка трансформации с рутиной падает с понятным сообщением."""
    _deploy(conn)
    with pytest.raises(psycopg.errors.RaiseException, match="ZSD_A03 требует ручной доработки: конечная рутина"):
        conn.execute(f"SELECT {PREFIX}_dwh.tr_zsd_a01__zsd_a03(1)")


def test_full_load_with_master_data(conn, model):
    """Полная загрузка ZSD_A01 → ZSD_A04 с чтением атрибута 0MATL_GROUP из справочника."""
    _deploy(conn)
    conn.execute(f"TRUNCATE {PREFIX}_dwh.zsd_a01_active")
    conn.execute(f"INSERT INTO {PREFIX}_md.material_attr (material, matl_group) VALUES ('MAT01', 'ZBEV')")
    conn.execute(f"""INSERT INTO {PREFIX}_dwh.zsd_a01_active
        (doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
        VALUES ('0000000001', '000010', '0000000101', 'MAT01', '10', DATE '2026-09-15', 100, 'RUB', 1, 'ST'),
               ('0000000001', '000020', '0000000101', 'MAT01', '10', DATE '2026-09-20', 50, 'RUB', 2, 'ST'),
               ('0000000002', '000010', '0000000101', 'MAT09', '10', NULL, 7, 'RUB', 3, 'ST')""")
    with conn.transaction():
        conn.execute(f"SELECT {PREFIX}_mart.tr_zsd_a01__zsd_a04(100)")
        conn.execute(f"SELECT {PREFIX}_mart.activate_zsd_a04(100)")
    rows = conn.execute(f"SELECT matl_group, calmonth, netval_inv, inv_qty FROM {PREFIX}_mart.zsd_a04_active "
                        "ORDER BY 1, 2").fetchall()
    # Материала MAT09 нет в справочнике: группа — начальное значение '', пустая дата — месяц '000000'
    assert rows == [("", "000000", Decimal("7.00"), Decimal("3.000")),
                    ("ZBEV", "202609", Decimal("150.00"), Decimal("3.000"))]
