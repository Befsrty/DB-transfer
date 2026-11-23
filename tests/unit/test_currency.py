"""Суммы CURR: сдвиг запятой по TCURX при загрузке, в типах колонок и при сверке."""

from decimal import Decimal

import pandas as pd
import pytest
from conftest import DEMO

from dbtransfer import currency as cur
from dbtransfer.generator import RulesError, TypeRules
from dbtransfer.importers.bw_csv import import_bw_export
from dbtransfer.model import FieldDef
from dbtransfer.reconcile import convert_etalon, make_spec
from dbtransfer.runtime import convert_sql, currency_factors, map_columns, resolve_target


@pytest.fixture(scope="module")
def model():
    return import_bw_export(DEMO / "bw_export")


def rules_with(tmp_path, text: str) -> TypeRules:
    (tmp_path / "type_rules.yaml").write_text(text, encoding="utf-8")
    return TypeRules.load(tmp_path)


def test_factor_and_sql():
    assert cur.factor(0, cur.INTERNAL, cur.EXTERNAL) == 100  # 10,00 внутри = 1000 JPY
    assert cur.factor(3, cur.INTERNAL, cur.EXTERNAL) == Decimal("0.1")  # 12,34 внутри = 1,234 KWD
    assert cur.factor(0, cur.EXTERNAL, cur.INTERNAL) == Decimal("0.01")
    assert cur.factor(2, cur.INTERNAL, cur.EXTERNAL) == 1
    assert cur.factor_sql("r.waers", {"JPY": 0, "KWD": 3}, cur.INTERNAL, cur.EXTERNAL) == \
        "CASE upper(btrim(r.waers)) WHEN 'JPY' THEN 100 WHEN 'KWD' THEN 0.1 ELSE 1 END"
    assert cur.factor_sql("r.waers", {"JPY": 0}, cur.EXTERNAL, cur.EXTERNAL) is None
    assert cur.widening({"JPY": 0, "KWD": 3}) == (2, 1)
    assert cur.widening({}) == (0, 0)


def test_tcurx_import(model):
    assert model.currency_decimals == {"JPY": 0, "KRW": 0, "KWD": 3}
    assert model.files["TCURX"]["status"] == "прочитан"


def test_tcurx_missing_uses_defaults(tmp_path, model):
    rules = TypeRules.load().bind(None)
    assert rules.currency_decimals["JPY"] == 0 and not rules.currency_from_tcurx
    assert TypeRules.load().bind(model).currency_from_tcurx


def test_column_type_widened_only_for_external():
    f = FieldDef(name="0NETVAL", position=1, datatype="CURR", length=17, decimals=2)
    assert TypeRules.load().bind(None).column_type(f) == "numeric(20,3)"


def test_column_type_internal(tmp_path):
    rules = rules_with(tmp_path, "currency:\n  store: internal\n").bind(None)
    f = FieldDef(name="0NETVAL", position=1, datatype="CURR", length=17, decimals=2)
    assert rules.column_type(f) == "numeric(17,2)"
    q = FieldDef(name="0QTY", position=1, datatype="QUAN", length=17, decimals=3)
    assert rules.column_type(q) == "numeric(17,3)"


def test_bad_format(tmp_path):
    with pytest.raises(RulesError, match="формат сумм «raw» неизвестен"):
        rules_with(tmp_path, "currency:\n  store: raw\n")
    with pytest.raises(RulesError, match="overrides.ZSALES_FF.currency_input"):
        rules_with(tmp_path, "overrides:\n  ZSALES_FF:\n    currency_input: x\n")


def test_flat_file_is_not_converted(model):
    # Плоский файл пишут люди: в нём настоящие суммы, пересчёт не нужен
    rules = TypeRules.load()
    target = resolve_target(model, rules, "ZSALES_FF", [])
    columns = map_columns(target, ["DOC_NUMBER", "S_ORD_ITEM", "NETVAL", "CURRENCY"])
    assert currency_factors(model, rules, target, columns) == ({}, [])


def test_internal_input_is_converted(model, tmp_path):
    rules = rules_with(tmp_path, "overrides:\n  ZSALES_FF:\n    currency_input: internal\n")
    target = resolve_target(model, rules, "ZSALES_FF", [])
    columns = map_columns(target, ["DOC_NUMBER", "S_ORD_ITEM", "NETVAL", "DISCOUNT", "CURRENCY"])
    factors, warnings = currency_factors(model, rules, target, columns)
    case = "CASE upper(btrim(r.currency)) WHEN 'JPY' THEN 100 WHEN 'KRW' THEN 100 WHEN 'KWD' THEN 0.1 ELSE 1 END"
    assert factors == {"netval": case, "discount": case}
    assert warnings == []
    netval = next(c for c in columns if c.name == "netval")
    assert convert_sql(netval, "r.netval", rules, factors["netval"]) == (
        f"(nullif(replace(replace(btrim(r.netval), ' ', ''), ',', '.'), '')::numeric(20,3) * {case})::numeric(20,3)")


def test_no_currency_column_warns(model, tmp_path):
    rules = rules_with(tmp_path, "overrides:\n  ZSALES_FF:\n    currency_input: internal\n")
    target = resolve_target(model, rules, "ZSALES_FF", [])
    columns = map_columns(target, ["DOC_NUMBER", "S_ORD_ITEM", "NETVAL"])
    factors, warnings = currency_factors(model, rules, target, columns)
    assert factors == {} and "колонка валюты" in warnings[0]


def test_convert_etalon(model, tmp_path):
    spec = make_spec(model, "ZSD_A02")
    assert spec.amounts["0NETVAL_INV"].field == "0DOC_CURRCY"
    etalon = pd.DataFrame({"0DOC_CURRCY": ["JPY", "RUB", "KWD"], "0NETVAL_INV": [10.0, 5.5, 12.34]})
    same = convert_etalon(etalon.copy(), spec, TypeRules.load().bind(model))
    assert list(same["0NETVAL_INV"]) == [10.0, 5.5, 12.34]  # эталон уже в настоящих суммах
    rules = rules_with(tmp_path, "currency:\n  etalon: internal\n").bind(model)
    out = convert_etalon(etalon.copy(), spec, rules)
    assert list(out["0NETVAL_INV"]) == pytest.approx([1000.0, 5.5, 1.234])
