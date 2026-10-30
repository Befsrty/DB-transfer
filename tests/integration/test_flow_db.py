"""Развёртывание, загрузка, прогон и сверка в живой базе (ПИ-6, ПИ-8…ПИ-12, ПИ-17, ФП-9.1).

Работают с Greengage и с PostgreSQL. Демо-проект копируется во временную папку с префиксом
схем dbtrt, демо-схемы dbt_* не трогаются. Запуск: uv run pytest -m db
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbtransfer.cli import app
from dbtransfer.config import load_db_settings
from dbtransfer.db import DbError, connect, server_dialect
from dbtransfer.deploy import DeployError, deploy
from dbtransfer.generator import TypeRules, generate
from dbtransfer.importers.bw_csv import import_bw_export
from dbtransfer.project import open_project
from dbtransfer.reconcile import DIFF, reconcile
from dbtransfer.runtime import ACTIVATED, LOADED, LoadError, RunError, load_file, rollback_load, run_load

pytestmark = pytest.mark.db

ROOT = Path(__file__).parents[2]
DATA = ROOT / "demo" / "data"
PREFIX = "dbtrt"
runner = CliRunner()


@pytest.fixture
def project(tmp_path):
    shutil.copytree(ROOT / "demo" / "bw_export", tmp_path / "bw_export")
    shutil.copytree(DATA, tmp_path / "data")
    text = (ROOT / "demo" / "project.yaml").read_text(encoding="utf-8").replace("schema_prefix: dbt",
                                                                               f"schema_prefix: {PREFIX}")
    (tmp_path / "project.yaml").write_text(text, encoding="utf-8")
    proj = open_project(tmp_path)
    proj.save_model(import_bw_export(proj.bw_export_dir))
    return proj


@pytest.fixture
def conn(project):
    try:
        connection = connect(load_db_settings(ROOT / ".env"), timeout=5)
    except DbError as e:
        pytest.skip(str(e))
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        for layer in ("stg", "md", "dwh", "mart", "meta"):
            cur.execute(f"DROP SCHEMA IF EXISTS {PREFIX}_{layer} CASCADE")
    connection.commit()
    connection.close()


def setup(project, conn):
    model = project.load_model()
    rules = TypeRules.load(project.root)
    package = generate(model, rules, server_dialect(conn))
    result = deploy(conn, package, recreate=True)
    assert result.ok, result.error
    return model, rules, package


def status(conn, load_id):
    value = conn.execute(f"SELECT status FROM {PREFIX}_meta.load_log WHERE load_id = %s", (load_id,)).fetchone()[0]
    conn.rollback()
    return value


def test_deploy_idempotent_and_hash_guard(project, conn):
    """ПИ-6 и ФП-6.3: повторное развёртывание без ошибок, изменённая модель требует --recreate."""
    model, rules, package = setup(project, conn)
    again = deploy(conn, package)
    assert again.ok and {i.action for i in again.items} == {"keep", "replace"}
    log = conn.execute(f"SELECT count(DISTINCT deploy_id), bool_and(status = 'ok') FROM {PREFIX}_meta.deploy_log"
                       ).fetchone()
    assert log == (2, True)
    conn.rollback()
    model.adsos["ZSD_A01"].description = "изменено"
    changed = generate(model, rules, package.dialect)
    with pytest.raises(DeployError, match="--recreate"):
        deploy(conn, changed)
    assert deploy(conn, changed, recreate=True).ok


def test_load_errors_and_formats(project, conn, tmp_path):
    """ПИ-11: текст в сумме отменяет загрузку целиком; запятая, 00000000 и пустое поле грузятся."""
    model, rules, _ = setup(project, conn)
    header = "DOC_NUMBER;S_ORD_ITEM;SOLD_TO;MATERIAL;CALDAY;NETVAL;DISCOUNT;CURRENCY;QTY;UNIT\n"
    bad = tmp_path / "bad.csv"
    bad.write_text(header + "0000000001;000010;0000000101;MAT01;20260915;сто;0,00;RUB;1,000;ST\n"
                   "0000000001;000020;0000000101;MAT01;20261340;1,00;0,00;RUB;1,000;ST\n", encoding="utf-8")
    with pytest.raises(LoadError) as e:
        load_file(conn, model, rules, "ZSALES_FF", bad)
    assert [str(x) for x in e.value.errors] == ["строка 2, поле NETVAL «сто»: не число",
                                                "строка 3, поле CALDAY «20261340»: не дата ГГГГММДД или ДД.ММ.ГГГГ"]
    assert conn.execute(f"SELECT count(*) FROM {PREFIX}_stg.zsales_ff").fetchone()[0] == 0
    conn.rollback()

    good = tmp_path / "good.csv"
    good.write_text(header + "0000000001;000010;;MAT01;00000000;1 234,56;0,00;RUB;1,000;ST\n"
                    "0000000002;000010;0000000101;MAT01;29.10.2026;7,5;0,00;RUB;1,000;ST\n", encoding="utf-8")
    res = load_file(conn, model, rules, "ZSALES_FF", good)
    rows = conn.execute(f"SELECT sold_to, calday, netval FROM {PREFIX}_stg.zsales_ff ORDER BY record_no").fetchall()
    conn.rollback()
    assert [(r[0], r[1] and r[1].isoformat(), float(r[2])) for r in rows] == \
        [("", None, 1234.56), ("0000000101", "2026-10-29", 7.5)]
    assert status(conn, res.load_id) == LOADED
    with pytest.raises(LoadError, match="уже загружался"):
        load_file(conn, model, rules, "ZSALES_FF", good)
    assert rollback_load(conn, model, res.load_id) == 2


def test_run_order_repeat_and_stub(project, conn):
    """ПИ-17, ПИ-12: порядок загрузок, запрет повторного прогона, блокировка заготовки."""
    model, rules, package = setup(project, conn)
    for obj in ("0MATERIAL", "0SOLD_TO"):
        load_file(conn, model, rules, obj, DATA / f"md_{obj}_attr.csv")
    first = load_file(conn, model, rules, "ZSALES_FF", DATA / "ZSALES_FF_load1.csv").load_id
    second = load_file(conn, model, rules, "ZSALES_FF", DATA / "ZSALES_FF_load2.csv").load_id
    with pytest.raises(RunError, match=f"Сначала прогоните загрузку {first}"):
        run_load(conn, model, package, second, "ZSD_A02")
    with pytest.raises(RunError, match="ZSD_A01 → ZSD_A03 \\(конечная рутина ABAP\\)"):
        run_load(conn, model, package, first, "ZSD_A03")
    assert status(conn, first) == LOADED
    run_load(conn, model, package, first, "ZSD_A02")
    run_load(conn, model, package, second, "ZSD_A02")
    assert status(conn, second) == ACTIVATED
    total = conn.execute(f"SELECT sum(netval_inv) FROM {PREFIX}_mart.zsd_a02_active").fetchone()[0]
    with pytest.raises(RunError, match=f"Загрузка {second} уже активирована"):
        run_load(conn, model, package, second, "ZSD_A02")
    assert conn.execute(f"SELECT sum(netval_inv) FROM {PREFIX}_mart.zsd_a02_active").fetchone()[0] == total
    conn.rollback()

    # ПИ-8: пары X / ' ' по каждому виду корректировок, N для новых, обнулившаяся строка ZSD_A02 осталась
    modes = dict(conn.execute(f"SELECT recordmode, count(*) FROM {PREFIX}_dwh.zsd_a01_changelog "
                              "WHERE load_id = %s GROUP BY 1", (second,)).fetchall())
    assert modes == {"X": 50, " ": 50, "N": 50}
    zero = conn.execute(f"SELECT count(*) FROM {PREFIX}_mart.zsd_a02_active WHERE netval_inv = 0").fetchone()[0]
    conn.rollback()
    assert zero >= 1

    # ПИ-9, ПИ-10: сверка
    out = project.out_dir
    assert reconcile(conn, model, "ZSD_A01", DATA / "etalon_ZSD_A01.csv", out).ok
    broken = reconcile(conn, model, "ZSD_A02", DATA / "etalon_ZSD_A02_broken.csv", out, project.root)
    assert broken.count(DIFF) == 3 and len(broken.problems) == 3
    assert [x.object_name for x in broken.lower] == ["ZSD_A01"] and broken.lower[0].ok
    saved = conn.execute(f"SELECT count(*) FROM {PREFIX}_meta.reconcile_result WHERE run_id = %s",
                         (broken.run_id,)).fetchone()[0]
    conn.rollback()
    assert saved == 142 * 2 and broken.excel.is_file()


def test_cli_run_demo(project, conn):
    """ФП-9.1, ПИ-1: сквозной сценарий из командной строки."""
    env_file = ROOT / ".env"
    result = runner.invoke(app, ["run-demo", "-p", str(project.root), "--env-file", str(env_file)])
    assert result.exit_code == 0, result.output
    assert "Сверка ZSD_A02           совпало 142 из 142" in result.output
    assert "Демо прошло" in result.output

    args = ["-p", str(project.root), "--env-file", str(env_file)]
    result = runner.invoke(app, ["reconcile", "ZSD_A02", str(DATA / "etalon_ZSD_A02_broken.csv"), *args])
    assert result.exit_code == 1 and "расхождение 3" in result.output
    assert "Нижний слой ZSD_A01: совпал" in result.output
    result = runner.invoke(app, ["deploy", *args])
    assert result.exit_code == 0, result.output
    # ФП-1.3: копия журнала развёртывания в out/
    logs = sorted(project.out_dir.glob("deploy_*.csv"))
    assert len(logs) >= 2 and "Журнал:" in result.output
    assert "object_name" in logs[-1].read_text(encoding="utf-8-sig").splitlines()[0]
    result = runner.invoke(app, ["load", "ZSALES_FF", str(DATA / "micro" / "ZSALES_FF_load1.csv"), *args])
    assert result.exit_code == 0 and "load_id:" in result.output
    load_id = result.output.strip().split("load_id: ")[-1]
    result = runner.invoke(app, ["rollback", load_id, *args])
    assert result.exit_code == 0 and "удалено строк: 6" in result.output
    result = runner.invoke(app, ["run", load_id, *args])
    assert result.exit_code == 1 and "откачена" in result.output
