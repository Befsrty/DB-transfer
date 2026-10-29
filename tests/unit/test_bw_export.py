"""Синтетическая выгрузка BW: совпадает с генератором и с bw_tables.yaml."""

import csv
import io

import make_bw_export
import pytest
from conftest import DEMO

from dbtransfer.config import load_bw_tables

EXPORT = DEMO / "bw_export"


def read(name: str) -> list[dict[str, str]]:
    raw = (EXPORT / f"{name}.csv").read_bytes()
    text = raw.decode(make_bw_export.ENCODING.get(name, "utf-8"))
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def test_committed_files_match_generator():
    # Если тест упал: выполни uv run python demo/tools/make_bw_export.py и закоммить результат
    expected = make_bw_export.render()
    actual = {p.name: p.read_bytes() for p in EXPORT.glob("*.csv")}
    assert actual.keys() == expected.keys()
    for name in expected:
        assert actual[name] == expected[name], f"{name} отличается от вывода генератора"


@pytest.mark.parametrize("table", sorted(load_bw_tables()))
def test_columns_follow_bw_tables_yaml(table):
    spec = load_bw_tables()[table]
    rows = read(table)
    header = next(csv.reader(io.StringIO((EXPORT / f"{table}.csv").read_bytes().decode(
        make_bw_export.ENCODING.get(table, "utf-8"))), delimiter=";"))
    assert header == list(spec.columns)
    for i, row in enumerate(rows, start=2):
        if row[spec.version_column] != "A":
            continue
        # Первая колонка — имя объекта, она заполнена всегда
        first = next(iter(spec.columns))
        assert row[first] != "", f"{table}.csv, строка {i}: пустая колонка {first}"


def test_windows_1251_file_is_not_utf8():
    raw = (EXPORT / "RSDIOBJT.csv").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert "Заказчик" in raw.decode("cp1251")


def test_demo_flow_objects_present():
    adso = {r["ADSONM"]: r for r in read("RSOADSO")}
    assert {"ZSD_A01", "ZSD_A02", "ZSD_S01", "ZSD_D01", "ZSD_A03", "ZSD_A04", "ZSD_A99"} <= adso.keys()
    assert adso["ZSD_A99"]["OBJVERS"] == "M"
    tables = {r["TABNAME"] for r in read("DD03L")}
    # standard: 1, 2, 3; cumulative: 1, 2; staging: 1; direct update: 2
    assert {"/BIC/AZSD_A011", "/BIC/AZSD_A012", "/BIC/AZSD_A013"} <= tables
    assert {"/BIC/AZSD_A021", "/BIC/AZSD_A022"} <= tables and "/BIC/AZSD_A023" not in tables
    assert "/BIC/AZSD_S011" in tables and "/BIC/AZSD_S012" not in tables
    assert "/BIC/AZSD_D012" in tables and "/BIC/AZSD_D011" not in tables


def test_active_table_keys():
    keys = [r["FIELDNAME"] for r in read("DD03L") if r["TABNAME"] == "/BIC/AZSD_A022" and r["KEYFLAG"] == "X"]
    assert keys == ["SOLD_TO", "CALMONTH", "DOC_CURRCY", "SALES_UNIT"]


def test_rule_types_cover_appendix_b():
    types = {r["RULETYPE"] for r in read("RSTRANRULE")}
    assert types == {"DIRECT", "CONSTANT", "FORMULA", "TIME", "READ_MD"}
    ends = [r["ENDROUTINE"] for r in read("RSTRAN") if r["ENDROUTINE"]]
    assert len(ends) == 1
    assert {r["CODEID"] for r in read("RSAABAP")} == set(ends)


def test_demo_flow_dtps_are_delta():
    modes = {(r["SRC"].split()[0], r["TGT"]): r["UPDMODE"] for r in read("RSBKDTP")}
    assert modes[("ZSALES_FF", "ZSD_A01")] == "DELTA"
    assert modes[("ZSD_A01", "ZSD_A02")] == "DELTA"
    assert "FULL" in modes.values()


def test_read_md_rule_targets_zsd_a04():
    tran = {r["TRANID"]: r for r in read("RSTRAN")}
    rule = next(r for r in read("RSTRANRULE") if r["RULETYPE"] == "READ_MD")
    assert tran[rule["TRANID"]]["TARGETNAME"] == "ZSD_A04"
    assert rule["PARAM"] == "0MATERIAL-0MATL_GROUP"
