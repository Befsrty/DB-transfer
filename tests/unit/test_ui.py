"""Веб-интерфейс: схема потоков, карточки и запуск приложения без базы (ТЗ, ФП-4)."""

import shutil
from pathlib import Path

import pytest
from conftest import DEMO
from streamlit.testing.v1 import AppTest

from dbtransfer.analysis.assessment import assess
from dbtransfer.analysis.lineage import DOWN, UP, lineage
from dbtransfer.generator import TypeRules
from dbtransfer.importers.bw_csv import import_bw_export
from dbtransfer.ui import cards
from dbtransfer.ui.schema import HIGHLIGHT, LEVEL_COLOR, flow_dot, lineage_highlight

APP = Path(__file__).resolve().parents[2] / "dbtransfer" / "ui" / "app.py"


@pytest.fixture(scope="module")
def model():
    return import_bw_export(DEMO / "bw_export")


@pytest.fixture(scope="module")
def report(model):
    return assess(model)


@pytest.fixture(scope="module")
def rules():
    return TypeRules.load(DEMO)


def node_names(dot):
    return {line.split('"')[1].split(":")[-1] for line in dot.splitlines() if "[label=" in line}


def test_full_schema_has_all_layers(model, report):
    dot = flow_dot(model, report)
    assert dot.startswith("digraph flow {") and dot.endswith("}")
    for name in ("ZSALES_FF", "ZSD_S01", "ZSD_A01", "ZSD_A02", "ZSD_A03", "ZSD_CP01", "ZSD_Q001"):
        assert f'"{name}\\n' in dot
    # Трансформация с конечной рутиной — красное ребро с подписью
    routine_edge = next(line for line in dot.splitlines() if "рутина" in line)
    assert LEVEL_COLOR[3] in routine_edge
    # Участие в CompositeProvider — пунктир
    assert 'style="dashed"' in dot


def test_focus_down_shows_only_sources(model, report):
    dot = flow_dot(model, report, focus="ZSD_A02", direction="down")
    assert '"ZSD_A02\\n' in dot and '"ZSD_A01\\n' in dot and '"ZSALES_FF\\n' in dot
    assert '"ZSD_A03\\n' not in dot and '"ZSD_CP01\\n' not in dot


def test_filters_by_type_and_layer(model, report):
    dot = flow_dot(model, report, types={"ADSO"})
    assert '"ZSALES_FF\\n' not in dot and '"ZSD_Q001\\n' not in dot and '"ZSD_A01\\n' in dot
    dot = flow_dot(model, report, layers={"mart"})
    assert '"ZSD_A02\\n' in dot and '"ZSD_A01\\n' not in dot


def test_lineage_highlight_marks_path(model, report):
    h = lineage_highlight(lineage(model, "ZSD_A02", "0NETVAL_INV", DOWN))
    assert {"ZSD_A02", "ZSD_A01", "ZSALES_FF"} <= h.objects
    assert ("ZSD_A01", "ZSD_A02") in h.edges and ("ZSALES_FF", "ZSD_A01") in h.edges
    dot = flow_dot(model, report, highlight=h, selected="ZSD_A02")
    a02 = next(line for line in dot.splitlines() if '"ZSD_A02\\n' in line)
    assert HIGHLIGHT in a02 and "peripheries=2" in a02

    up = lineage_highlight(lineage(model, "ZSD_A02", "0NETVAL_INV", UP))
    assert {"ZSD_CP01", "ZSD_Q001"} <= up.objects


def test_object_card(model, report, rules):
    adso = model.adsos["ZSD_A02"]
    fields = cards.object_fields(model, rules, adso)
    keys = set(fields.loc[fields["Ключ"] == "да", "Поле"])
    assert keys == {"0SOLD_TO", "0CALMONTH", "0DOC_CURRCY", "0SALES_UNIT"}
    assert fields.set_index("Поле").loc["0NETVAL_INV", "Тип ADB"] == "numeric(17,2)"
    tables = cards.object_tables(model, rules, adso)
    assert list(tables["Таблица ADB"]) == ["dbt_stg.zsd_a02_inbound", "dbt_mart.zsd_a02_active"]
    assert cards.assessment_of(report, "ZSD_A02")[0] == 1
    ds = model.datasources["ZSALES_FF"] if "ZSALES_FF" in model.datasources else next(iter(model.datasources.values()))
    assert len(cards.object_tables(model, rules, ds)) == 1


def test_transformation_card_and_lineage_steps(model):
    t = next(t for t in model.transformations.values() if t.all_routines)
    assert cards.transformation_title(model, t).startswith("Трансформация ")
    assert {"Поле источника", "Правило", "Параметр", "Поле цели"} == set(cards.transformation_rules(model, t))
    steps = cards.lineage_steps(lineage(model, "ZSD_A02", "0NETVAL_INV", DOWN))
    assert steps["Шаг"].iloc[0] == 0 and steps["Шаг"].max() >= 2
    assert "Как получено" in steps
    assert "Как передаётся" in cards.lineage_steps(lineage(model, "ZSD_A02", "0NETVAL_INV", UP), UP)


def test_lineage_excel(model, tmp_path):
    import openpyxl

    path = tmp_path / "lineage.xlsx"
    path.write_bytes(cards.lineage_excel(lineage(model, "ZSD_A02", "0NETVAL_INV", DOWN)))
    sheet = openpyxl.load_workbook(path)["Шаги"]
    rows = list(sheet.values)
    assert rows[0][:5] == ("Поле", "Направление", "Глубина", "Объект", "Правило")
    assert rows[1][0] == "ZSD_A02.0NETVAL_INV" and rows[1][1] == "откуда пришло"
    assert any(r[3] == "ZSALES_FF" and "NETVAL - DISCOUNT" in r[4] for r in rows[1:])


@pytest.fixture
def project_dir(tmp_path, monkeypatch, model):
    shutil.copytree(DEMO / "bw_export", tmp_path / "bw_export")
    shutil.copy(DEMO / "project.yaml", tmp_path / "project.yaml")
    model.save(tmp_path / "model.json")
    monkeypatch.setenv("DBT_PROJECT", str(tmp_path))
    # Базы нет: вкладки с подключением должны показать ошибку, а не упасть
    monkeypatch.setenv("DBT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("DBT_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DBT_DB_PORT", "1")
    return tmp_path


def test_app_renders_all_tabs_without_database(project_dir):
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Импорт", "Схема", "Сложность", "SQL", "Развёртывание", "Сверка"]
    assert at.sidebar.text_input[0].value == str(project_dir)
    assert "ZSD_A01" in [s.value for s in at.subheader]  # карточка первого объекта на вкладке «Схема»
    assert any("Объектов уровня 1" == m.label for m in at.metric)


def test_app_without_model_asks_for_import(project_dir):
    (project_dir / "model.json").unlink()
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert any("Импортируйте выгрузки" in i.value for i in at.info)
