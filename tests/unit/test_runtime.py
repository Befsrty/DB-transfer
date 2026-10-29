"""Загрузка и прогон без базы: разбор файла, выбор таблицы, проверки и приведение, шаги прогона."""

import pytest
from conftest import DEMO

from dbtransfer.deploy import CREATE, KEEP, DeployPlan, PlanItem
from dbtransfer.generator import SqlObject, TypeRules, generate
from dbtransfer.importers.bw_csv import import_bw_export
from dbtransfer.runtime import (
    LoadError,
    RowError,
    check_sql,
    convert_sql,
    flow_steps,
    map_columns,
    read_csv,
    resolve_target,
)
from dbtransfer.scenario import data_files

RULES = TypeRules.load()


@pytest.fixture(scope="module")
def model():
    return import_bw_export(DEMO / "bw_export")


def test_read_csv(tmp_path):
    header, rows = read_csv(DEMO / "data" / "micro" / "ZSALES_FF_load1.csv")
    assert header[0] == "DOC_NUMBER" and len(rows) == 6 and rows[0][0] == 2
    (tmp_path / "cp.csv").write_bytes("A;B\n\nПривет;1\n".encode("cp1251"))
    assert read_csv(tmp_path / "cp.csv") == (["A", "B"], [(3, ["Привет", "1"])])
    (tmp_path / "empty.csv").write_text("", encoding="utf-8")
    with pytest.raises(LoadError, match="пустой"):
        read_csv(tmp_path / "empty.csv")


def test_resolve_target(model):
    t = resolve_target(model, RULES, "ZSALES_FF", ["DOC_NUMBER"])
    assert t.kind == "datasource" and t.table.qname == "dbt_stg.zsales_ff" and t.with_load_id
    assert resolve_target(model, RULES, "0MATERIAL", ["0MATERIAL", "0MATL_GROUP"]).table.qname == \
        "dbt_md.material_attr"
    assert resolve_target(model, RULES, "0MATERIAL", ["0MATERIAL", "LANGU", "TXTSH"]).kind == "md_text"
    with pytest.raises(LoadError, match="нет таблицы атрибутов"):
        resolve_target(model, RULES, "0COUNTRY", ["0COUNTRY"])
    with pytest.raises(LoadError, match="ADSO"):
        resolve_target(model, RULES, "ZSD_A01", [])
    with pytest.raises(LoadError, match="не найден"):
        resolve_target(model, RULES, "NOPE", [])


def test_map_columns(model):
    t = resolve_target(model, RULES, "ZSALES_FF", [])
    assert [c.name for c in map_columns(t, ["DOC_NUMBER", "netval"])] == ["doc_number", "netval"]
    with pytest.raises(LoadError, match="Колонок PRICE, load_id нет"):
        map_columns(t, ["DOC_NUMBER", "PRICE", "load_id"])
    md = resolve_target(model, RULES, "0SOLD_TO", ["0SOLD_TO", "LANGU"])
    assert [c.name for c in map_columns(md, ["0SOLD_TO", "LANGU", "TXTSH", "TXTLG"])] == \
        ["sold_to", "langu", "txtsh", "txtlg"]
    with pytest.raises(LoadError, match="нет ключевых колонок: langu"):
        map_columns(md, ["0SOLD_TO", "TXTSH"])


def test_check_and_convert(model):
    t = resolve_target(model, RULES, "ZSALES_FF", [])
    cols = {c.name: c for c in t.table.columns}
    assert [p for _, p in check_sql(cols["netval"], "v")] == ["не число", "больше 15 знаков до запятой"]
    assert [p for _, p in check_sql(cols["calday"], "v")] == ["не дата ГГГГММДД или ДД.ММ.ГГГГ"]
    assert [p for _, p in check_sql(cols["s_ord_item"], "v")] == ["не только цифры (NUMC)", "длиннее 6 символов"]
    assert [p for _, p in check_sql(cols["doc_number"], "v")] == ["длиннее 10 символов"]
    assert convert_sql(cols["netval"], "v", RULES) == \
        "nullif(replace(replace(btrim(v), ' ', ''), ',', '.'), '')::numeric(17,2)"
    assert "THEN NULL" in convert_sql(cols["calday"], "v", RULES)
    assert convert_sql(cols["doc_number"], "v", RULES) == "v"
    md = resolve_target(model, RULES, "0SOLD_TO", ["LANGU"])
    langu = md.table.column("langu")
    assert [p for _, p in check_sql(langu, "v")] == ["длиннее 1 символов"]
    assert str(RowError(3, "NETVAL", "abc", "не число")) == "строка 3, поле NETVAL «abc»: не число"


def test_flow_steps(model):
    package = generate(model)
    ds = model.find("ZSALES_FF")
    steps = flow_steps(model, package, ds.ref, "ZSD_A02")
    assert [s.function for s in steps] == ["dbt_dwh.tr_zsales_ff__zsd_a01", "dbt_dwh.activate_zsd_a01",
                                          "dbt_mart.tr_zsd_a01__zsd_a02", "dbt_mart.activate_zsd_a02"]
    blocked = flow_steps(model, package, ds.ref, "ZSD_A03")
    assert blocked[-2].stub == ["конечная рутина ABAP"] and blocked[-1].title == "активация ZSD_A03"
    everything = flow_steps(model, package, ds.ref, None)
    assert {s.title for s in everything} >= {"трансформация ZSD_A01 → ZSD_A04", "активация ZSD_A04"}


def test_deploy_plan_blocking():
    table = SqlObject("adso", "table", "dbt_dwh.t", "")
    meta = SqlObject("meta", "table", "dbt_meta.load_log", "")
    plan = DeployPlan([PlanItem(table, True, KEEP), PlanItem(meta, True, KEEP)], "new", "old", False)
    assert plan.model_changed and plan.blocked and [i.obj.name for i in plan.kept_tables] == ["dbt_dwh.t"]
    assert not DeployPlan(plan.items, "new", "old", True).blocked
    assert not DeployPlan(plan.items, "new", None, False).blocked
    assert not DeployPlan([PlanItem(table, False, CREATE)], "new", "old", False).blocked
    assert plan.counts() == {KEEP: 2}


def test_data_files():
    md, loads, etalons = data_files(DEMO / "data")
    assert [p.name for _, p in md] == ["md_0MATERIAL_attr.csv", "md_0MATERIAL_text.csv", "md_0SOLD_TO_attr.csv",
                                       "md_0SOLD_TO_text.csv"]
    assert loads == [("ZSALES_FF", DEMO / "data" / "ZSALES_FF_load1.csv"),
                     ("ZSALES_FF", DEMO / "data" / "ZSALES_FF_load2.csv")]
    assert [o for o, _ in etalons] == ["ZSD_A01", "ZSD_A02"]
