"""Тестовые данные и эталоны потока «Продажи» (ТЗ 4.3.5, 4.3.7, приложение Д)."""

from decimal import Decimal

import make_demo_data
import pandas as pd
from conftest import DEMO

DATA = DEMO / "data"
MICRO = DATA / "micro"
KEY = ["DOC_NUMBER", "S_ORD_ITEM"]


def read(path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", dtype=str, keep_default_na=False)


def dec(series: pd.Series) -> list[Decimal]:
    return [Decimal(v.replace(",", ".")) for v in series]


def test_committed_files_match_generator():
    # Если тест упал: выполни uv run python demo/tools/make_demo_data.py и закоммить результат
    expected = make_demo_data.build()
    actual = {p.name: p.read_bytes() for p in DATA.glob("*.csv")}
    assert actual == expected


def test_micro_example_from_appendix_d():
    # Скрипт эталона даёт ровно те строки, что посчитаны вручную в приложении Д (ПИ-18)
    loads = [(MICRO / f"ZSALES_FF_load{i}.csv").read_bytes() for i in (1, 2)]
    result = make_demo_data.flow_files(loads)
    for name in ("ZSD_A01", "ZSD_A01_changelog", "ZSD_A02"):
        expected = read(MICRO / f"expected_{name}.csv")
        actual = result[name].astype(str).reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, expected, obj=name)


def test_load1_shape():
    load1 = read(DATA / "ZSALES_FF_load1.csv")
    assert 500 <= len(load1) <= 510
    assert load1.duplicated(KEY).sum() >= 3  # повторы ключа внутри загрузки
    assert (load1["CALDAY"] == "00000000").sum() == 5
    assert set(load1["CURRENCY"]) == {"RUB", "EUR"}
    assert load1["NETVAL"].str.contains(",").all()


def test_load2_cases():
    load1, load2 = read(DATA / "ZSALES_FF_load1.csv"), read(DATA / "ZSALES_FF_load2.csv")
    cases = read(DATA / "load2_cases.csv")
    assert list(cases[KEY].itertuples(index=False)) == list(load2[KEY].itertuples(index=False))
    counts = cases["_case"].value_counts()
    for case in make_demo_data.CASES:
        assert counts[case] >= 5, case
    assert counts[make_demo_data.NEW_CASE] == 50
    changed = load2.merge(load1[KEY].drop_duplicates(), on=KEY)
    assert len(changed) == 50
    assert (load2["CALDAY"] == "").sum() == 2
    assert load2["CALDAY"].str.contains(r"\.").any()  # даты ДД.ММ.ГГГГ


def test_repeat_case_is_unchanged():
    load1, load2 = read(DATA / "ZSALES_FF_load1.csv"), read(DATA / "ZSALES_FF_load2.csv")
    cases = read(DATA / "load2_cases.csv")
    last = load1.drop_duplicates(KEY, keep="last").set_index(KEY)
    repeat = load2[cases["_case"] == "(д) повтор"].set_index(KEY)
    # Даты в загрузке 2 в формате ДД.ММ.ГГГГ, остальные поля совпадают
    cols = [c for c in repeat.columns if c != "CALDAY"]
    pd.testing.assert_frame_equal(repeat[cols], last.loc[repeat.index, cols])


def test_etalon_a01_has_all_items():
    load1 = read(DATA / "ZSALES_FF_load1.csv")
    a01 = read(DATA / "etalon_ZSD_A01.csv")
    assert len(a01) == load1[KEY].drop_duplicates().shape[0] + 50
    assert not a01.duplicated(["0DOC_NUMBER", "0S_ORD_ITEM"]).any()
    assert set(a01["0DISTR_CHAN"]) == {"10"}


def test_etalon_a02_totals_match_a01():
    # Суммы верхнего слоя по валюте равны суммам ZSD_A01: before image гасят старые значения
    a01, a02 = read(DATA / "etalon_ZSD_A01.csv"), read(DATA / "etalon_ZSD_A02.csv")
    for currency in ("RUB", "EUR"):
        assert sum(dec(a01[a01["0DOC_CURRCY"] == currency]["0NETVAL_INV"])) == sum(
            dec(a02[a02["0DOC_CURRCY"] == currency]["0NETVAL_INV"]))
    assert sum(dec(a01["0INV_QTY"])) == sum(dec(a02["0INV_QTY"]))
    assert "000000" in set(a02["0CALMONTH"])  # пустая дата
    assert ((a02["0NETVAL_INV"] == "0,00") & (a02["0INV_QTY"] == "0,000")).any()  # обнулённая строка


def test_broken_etalon_has_exactly_three_differences():
    good, broken = read(DATA / "etalon_ZSD_A02.csv"), read(DATA / "etalon_ZSD_A02_broken.csv")
    assert (good != broken).any(axis=1).sum() == 3
