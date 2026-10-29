"""Lineage полей на демо-потоке (ПИ-16, ФП-4.6)."""

import pytest
from conftest import DEMO

from dbtransfer.analysis.lineage import DOWN, UP, LineageError, lineage, render
from dbtransfer.importers.bw_csv import import_bw_export
from dbtransfer.model import RuleKind


@pytest.fixture(scope="module")
def model():
    return import_bw_export(DEMO / "bw_export")


def titles(node):
    return [n.title for _, n in node.walk()]


def test_netval_down_branches_on_formula(model):
    root = lineage(model, "ZSD_A02", "0NETVAL_INV", DOWN)
    assert root.location == "dbt_mart.zsd_a02_active.netval_inv"
    (a01,) = root.children
    assert a01.title == "ZSD_A01.0NETVAL_INV" and a01.via.rule == RuleKind.DIRECT
    assert [c.title for c in a01.children] == ["ZSALES_FF.NETVAL", "ZSALES_FF.DISCOUNT"]
    assert a01.children[0].via.param == "NETVAL - DISCOUNT"
    assert a01.children[0].location == "dbt_stg.zsales_ff.netval"


def test_calmonth_down_through_time_rule(model):
    root = lineage(model, "ZSD_A02", "0CALMONTH", DOWN)
    assert titles(root) == ["ZSD_A02.0CALMONTH", "ZSD_A01.0CALDAY", "ZSALES_FF.CALDAY"]
    assert root.children[0].via.rule == RuleKind.TIME


def test_netval_up_reaches_composite_provider(model):
    root = lineage(model, "ZSALES_FF", "NETVAL", UP)
    all_titles = titles(root)
    assert "ZSD_A02.0NETVAL_INV" in all_titles
    assert "ZSD_CP01" in all_titles and "ZSD_Q001" in all_titles
    a03 = next(n for _, n in root.walk() if n.title == "ZSD_A03.0NETVAL_INV")
    assert a03.via.maybe_changed


def test_constant_ends_path(model):
    root = lineage(model, "ZSD_A01", "0DISTR_CHAN", DOWN)
    (const,) = root.children
    assert const.via.rule == RuleKind.CONSTANT and const.via.param == "10"
    assert const.children == [] and "константа" in const.note


def test_read_md_branches_into_master_data(model):
    root = lineage(model, "ZSD_A04", "0MATL_GROUP", DOWN)
    locations = [c.location for c in root.children]
    assert "dbt_dwh.zsd_a01_active.material" in locations
    assert "dbt_md.material_attr.matl_group" in locations


def test_routine_transformation_marks_step(model):
    root = lineage(model, "ZSD_A03", "0NETVAL_INV", DOWN)
    assert root.children[0].via.maybe_changed
    assert "возможно изменено рутиной" in render(root)


def test_unknown_field(model):
    with pytest.raises(LineageError):
        lineage(model, "ZSD_A02", "0NOPE")
