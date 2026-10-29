"""Перевод формул BW в SQL по словарю приложения В."""

import re

import pytest

from dbtransfer.generator.formula_sql import FormulaError, tokenize, translate
from dbtransfer.model import FieldDef

FIELDS = {
    "NETVAL": FieldDef(name="NETVAL", position=1, datatype="CURR", length=17, decimals=2),
    "DISCOUNT": FieldDef(name="DISCOUNT", position=2, datatype="CURR", length=17, decimals=2),
    "QTY": FieldDef(name="QTY", position=3, datatype="QUAN", length=17, decimals=3),
    "MATERIAL": FieldDef(name="MATERIAL", position=4, datatype="CHAR", length=40),
    "S_ORD_ITEM": FieldDef(name="S_ORD_ITEM", position=5, datatype="NUMC", length=6),
    "CALDAY": FieldDef(name="CALDAY", position=6, datatype="DATS", length=8),
    "0SOLD_TO": FieldDef(name="0SOLD_TO", position=7, datatype="CHAR", length=10),
}


def resolve(name):
    f = FIELDS.get(name.upper())
    return (f"s.{name.lower().lstrip('0')}", f) if f else None


@pytest.mark.parametrize(("formula", "sql"), [
    ("NETVAL - DISCOUNT", "s.netval - s.discount"),
    ("NETVAL - (DISCOUNT + 1)", "s.netval - (s.discount + 1)"),
    ("NETVAL - DISCOUNT - 1", "s.netval - s.discount - 1"),
    ("NETVAL * 2 + 1", "s.netval * 2 + 1"),
    ("(NETVAL + 1) * 2", "(s.netval + 1) * 2"),
    ("NETVAL / QTY", "s.netval / NULLIF(s.qty, 0)"),
    ("-NETVAL", "-s.netval"),
    ("IF(QTY > 0; NETVAL / QTY; 0)", "CASE WHEN s.qty > 0 THEN s.netval / NULLIF(s.qty, 0) ELSE 0 END"),
    ("IF(QTY >= 1 AND NOT NETVAL = 0 OR DISCOUNT <> 0; 1; 2)",
     "CASE WHEN s.qty >= 1 AND NOT s.netval = 0 OR s.discount <> 0 THEN 1 ELSE 2 END"),
    ("CONCATENATE(MATERIAL; '-'; 0SOLD_TO)", "concat(s.material, '-', s.sold_to)"),
    ("SUBSTRING(MATERIAL; 0; 3)", "substr(s.material, 0 + 1, 3)"),
    ("STRLEN(MATERIAL)", "length(s.material)"),
    ("toupper(MATERIAL)", "upper(s.material)"),
    ("ABS(NETVAL)", "abs(s.netval)"),
    ("ROUND(NETVAL, 0)", "round(s.netval, 0)"),
    ("DATE_YEAR(CALDAY)", "coalesce(to_char(s.calday, 'YYYY'), '0000')"),
    ("DATE_MONTH(CALDAY)", "coalesce(to_char(s.calday, 'YYYYMM'), '000000')"),
    ("IS_INITIAL(MATERIAL)", "(s.material IS NULL OR s.material = '')"),
    ("IS_INITIAL(NETVAL)", "(s.netval IS NULL OR s.netval = 0)"),
    ("IS_INITIAL(CALDAY)", "(s.calday IS NULL)"),
    ("IS_INITIAL(S_ORD_ITEM)", "(s.s_ord_item IS NULL OR s.s_ord_item = '' OR s.s_ord_item = '000000')"),
    ("'it''s'", "'it''s'"),
])
def test_translate(formula, sql):
    assert translate(formula, resolve) == sql


@pytest.mark.parametrize(("formula", "message"), [
    ("LOG(NETVAL)", "функция LOG не поддерживается"),
    ("NETVAL - PRICE", "поле PRICE не найдено"),
    ("NETVAL -", "формула оборвалась"),
    ("NETVAL DISCOUNT", "лишнее «DISCOUNT»"),
    ("NETVAL # 2", "непонятный символ «#»"),
    ("IF(NETVAL; 1)", "аргументов: 3"),
    ("IS_INITIAL(NETVAL + 1)", "только для поля"),
    ("(NETVAL", "ожидалось «)»"),
    ("", "пустая формула"),
    ("AND", "без операнда"),
])
def test_untranslatable(formula, message):
    with pytest.raises(FormulaError, match=re.escape(message)):
        translate(formula, resolve)


def test_tokenize_infoobject_names():
    assert [t.text for t in tokenize("0NETVAL_INV/2 + /BIC/ZPRICE")] == ["0NETVAL_INV", "/", "2", "+", "/BIC/ZPRICE"]
