"""Пакет ClickHouse в живом сервере: DDL, пошаговая активация и трансформации на примере приложения Д.

Тот же сценарий, что test_generated_sql.py для Greengage, но шаги SqlObject.steps выполняются по очереди
с параметром {p_load_id:Int64}, как это будет делать прогон. Объекты — в базах dbtest_*.
Запуск: uv run pytest -m clickhouse (сервер: DBT_CH_HOST, DBT_CH_PORT — HTTP, по умолчанию localhost:8123,
DBT_CH_USER, DBT_CH_PASSWORD). В CI — контейнер clickhouse/clickhouse-server.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest
from test_generated_sql import MICRO, _expected, _read, _value

from dbtransfer import naming
from dbtransfer.generator import TypeRules, generate
from dbtransfer.model import Model

pytestmark = pytest.mark.clickhouse
clickhouse_connect = pytest.importorskip("clickhouse_connect")

ROOT = Path(__file__).parents[2]
PREFIX = "dbtest"
LAYERS = ("stg", "md", "dwh", "mart", "meta")


@pytest.fixture(scope="module")
def model() -> Model:
    from dbtransfer.importers.bw_csv import import_bw_export

    m = import_bw_export(ROOT / "demo" / "bw_export")
    m.schema_prefix = PREFIX
    return m


@pytest.fixture(scope="module")
def package(model):
    return generate(model, TypeRules.load(), dialect="clickhouse")


@pytest.fixture(scope="module")
def client():
    try:
        c = clickhouse_connect.get_client(
            host=os.environ.get("DBT_CH_HOST", "localhost"), port=int(os.environ.get("DBT_CH_PORT", "8123")),
            username=os.environ.get("DBT_CH_USER", "default"), password=os.environ.get("DBT_CH_PASSWORD", ""))
    except Exception as e:  # noqa: BLE001 — нет сервера: тесты пропускаются
        pytest.skip(f"ClickHouse недоступен: {e}")
    yield c
    c.close()


@pytest.fixture
def ch(client, package):
    """Чистые базы dbtest_* с развёрнутым DDL пакета."""
    _drop(client)
    for o in package.objects:
        if o.group not in ("activation", "transformations"):
            client.command(o.sql.strip().removesuffix(";"))
    yield client
    _drop(client)


def _drop(client) -> None:
    for layer in LAYERS:
        client.command(f"DROP DATABASE IF EXISTS {PREFIX}_{layer}")


def _run_steps(client, package, group: str, ref: str, load_id: int) -> list[tuple]:
    """Шаги объекта по очереди; результат последнего SELECT — итоги для журнала загрузок."""
    obj = package.function_for(ref, group)
    result: list[tuple] = []
    for _, sql in obj.steps:
        if sql.lstrip().startswith("SELECT"):
            result = client.query(sql, parameters={"p_load_id": load_id}).result_rows
        else:
            client.command(sql, parameters={"p_load_id": load_id})
    return result


def _tr(package, source: str, target: str) -> str:
    return next(o.ref for o in package.objects if o.group == "transformations"
                and o.bw_object == f"Трансформация {source} → {target}")


def _load_file(client, model: Model, path: Path, load_id: int) -> None:
    ds = model.find("ZSALES_FF")
    columns = ["load_id", "record_no"] + [naming.column(f.name) for f in ds.fields]
    rows = [[load_id, record_no] + [_value(row[f.name], f.datatype) for f in ds.fields]
            for record_no, row in enumerate(_read(path), start=1)]
    database, table = naming.data_table(model, ds).split(".")
    client.insert(table, rows, column_names=columns, database=database)


def _actual(client, model: Model, adso: str, table: str, extra: tuple[str, ...] = ()) -> list[tuple]:
    obj = model.adsos[adso]
    columns = list(extra) + [naming.column(f.name, f.info_object) for f in obj.fields]
    rows = client.query(f"SELECT {', '.join(columns)} FROM {table}").result_rows
    return sorted((tuple(r) for r in rows), key=repr)


def test_deploy_twice(ch, package):
    for o in package.objects:  # повторное развёртывание без ошибок (IF NOT EXISTS)
        if o.group not in ("activation", "transformations"):
            ch.command(o.sql.strip().removesuffix(";"))
    tables = ch.query(f"SELECT count() FROM system.tables WHERE startsWith(database, '{PREFIX}_')").result_rows[0][0]
    assert tables == sum(1 for o in package.objects if o.kind == "table")


def test_appendix_d_flow(ch, model, package):
    """ПИ-18 для ClickHouse: две загрузки из demo/data/micro дают ровно строки приложения Д."""
    totals = {}
    for load_id, name in ((1, "ZSALES_FF_load1.csv"), (2, "ZSALES_FF_load2.csv")):
        _load_file(ch, model, MICRO / name, load_id)
        _run_steps(ch, package, "transformations", _tr(package, "ZSALES_FF", "ZSD_A01"), load_id)
        totals[load_id] = _run_steps(ch, package, "activation", "ZSD_A01", load_id)
        _run_steps(ch, package, "transformations", _tr(package, "ZSD_A01", "ZSD_A02"), load_id)
        _run_steps(ch, package, "activation", "ZSD_A02", load_id)

    a01 = naming.data_table(model, model.adsos["ZSD_A01"])
    assert _actual(ch, model, "ZSD_A01", a01) == _expected(model, "ZSD_A01", MICRO / "expected_ZSD_A01.csv")
    log = f"{PREFIX}_dwh.zsd_a01_changelog"
    assert (_actual(ch, model, "ZSD_A01", log, ("load_id", "recordmode"))
            == _expected(model, "ZSD_A01", MICRO / "expected_ZSD_A01_changelog.csv", ("load_id", "recordmode")))
    a02 = naming.data_table(model, model.adsos["ZSD_A02"])
    assert _actual(ch, model, "ZSD_A02", a02) == _expected(model, "ZSD_A02", MICRO / "expected_ZSD_A02.csv")
    assert totals[2] == [(6, 1, 5)]  # активировано, новых, изменённых — как в журнале Greengage


def test_stub_blocks_run(ch, package):
    """ФП-5.9: заготовка трансформации с рутиной останавливает прогон понятным сообщением."""
    with pytest.raises(Exception, match="ZSD_A03 требует ручной доработки: конечная рутина"):
        _run_steps(ch, package, "transformations", _tr(package, "ZSD_A01", "ZSD_A03"), 1)


def test_rejects_delete_records(ch, model, package):
    """recordmode D в загрузке ADSO standard останавливает активацию на первом шаге."""
    ch.command(f"INSERT INTO {PREFIX}_stg.zsd_a01_inbound (load_id, record_no, recordmode, doc_number, s_ord_item) "
               "VALUES (7, 1, 'D', '0000000001', '000010')")
    with pytest.raises(Exception, match="recordmode D или R"):
        _run_steps(ch, package, "activation", "ZSD_A01", 7)


def test_full_load_with_master_data(ch, package):
    """Полная загрузка ZSD_A01 → ZSD_A04 с чтением атрибута 0MATL_GROUP из справочника."""
    ch.command(f"INSERT INTO {PREFIX}_md.material_attr (material, matl_group) VALUES ('MAT01', 'ZBEV')")
    ch.command(f"""INSERT INTO {PREFIX}_dwh.zsd_a01_active
        (doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
        VALUES ('0000000001', '000010', '0000000101', 'MAT01', '10', '2026-09-15', 100, 'RUB', 1, 'ST'),
               ('0000000001', '000020', '0000000101', 'MAT01', '10', '2026-09-20', 50, 'RUB', 2, 'ST'),
               ('0000000002', '000010', '0000000101', 'MAT09', '10', NULL, 7, 'RUB', 3, 'ST')""")
    _run_steps(ch, package, "transformations", _tr(package, "ZSD_A01", "ZSD_A04"), 100)
    assert _run_steps(ch, package, "activation", "ZSD_A04", 100) == [(2, 0)]
    rows = ch.query(f"SELECT matl_group, calmonth, netval_inv, inv_qty FROM {PREFIX}_mart.zsd_a04_active "
                    "ORDER BY 1, 2").result_rows
    # Материала MAT09 нет в справочнике: группа — начальное значение '', пустая дата — месяц '000000'
    assert rows == [("", "000000", Decimal("7.00"), Decimal("3.000")),
                    ("ZBEV", "202609", Decimal("150.00"), Decimal("3.000"))]
    # Повторная активация той же загрузки складывает суммы с уже активными строками
    ch.command(f"INSERT INTO {PREFIX}_stg.zsd_a04_inbound (load_id, record_no, recordmode, matl_group, calmonth, "
               "doc_currcy, sales_unit, netval_inv, inv_qty) VALUES (101, 1, ' ', 'ZBEV', '202609', 'RUB', 'ST', 1, 1)")
    assert _run_steps(ch, package, "activation", "ZSD_A04", 101) == [(0, 1)]
    assert ch.query(f"SELECT netval_inv FROM {PREFIX}_mart.zsd_a04_active WHERE matl_group = 'ZBEV'"
                    ).result_rows == [(Decimal("151.00"),)]
