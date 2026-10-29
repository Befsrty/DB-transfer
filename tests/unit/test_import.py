"""Импорт демо-выгрузки BW (ПИ-2, ПИ-3, ФП-2)."""

import shutil

import pytest
from conftest import DEMO

from dbtransfer.importers.bw_csv import ImportFailed, import_bw_export, parse_ref
from dbtransfer.importers.csv_reader import decode, detect_delimiter
from dbtransfer.model import AdsoKind, InfoObjectKind, ObjectType, RuleKind, Severity

EXPORT = DEMO / "bw_export"


@pytest.fixture(scope="module")
def model():
    return import_bw_export(EXPORT)


def test_counts_match_expected_md(model):
    # Контрольный список: demo/bw_export/EXPECTED.md
    kinds = [i.kind for i in model.info_objects.values()]
    assert kinds.count(InfoObjectKind.CHARACTERISTIC) == 7
    assert kinds.count(InfoObjectKind.TIME) == 2
    assert kinds.count(InfoObjectKind.UNIT) == 2
    assert kinds.count(InfoObjectKind.KEY_FIGURE) == 2
    adso = {a.name: a.kind for a in model.adsos.values()}
    assert adso == {
        "ZSD_A01": AdsoKind.STANDARD, "ZSD_A03": AdsoKind.STANDARD,
        "ZSD_A02": AdsoKind.CUMULATIVE, "ZSD_A04": AdsoKind.CUMULATIVE,
        "ZSD_S01": AdsoKind.STAGING, "ZSD_D01": AdsoKind.DIRECT_UPDATE,
    }
    assert list(model.datasources) == ["ZSALES_FF/FLATFILE"]
    assert len(model.transformations) == 4
    assert sorted(d.mode for d in model.dtps.values()) == ["DELTA", "DELTA", "DELTA", "FULL"]
    others = sorted((o.type, o.name) for o in model.others)
    assert others == sorted([
        (ObjectType.COMPOSITE_PROVIDER, "ZSD_CP01"), (ObjectType.QUERY, "ZSD_Q001"),
        (ObjectType.HIERARCHY, "ZMAT_PRODUCT_TREE"), (ObjectType.PROCESS_CHAIN, "ZSD_PC01"),
        (ObjectType.CLASSIC_DSO, "ZOLD_DSO"),
    ])


def test_clean_import_has_no_problems(model):
    assert [i for i in model.issues if i.severity != Severity.INFO] == []


def test_inactive_object_listed(model):
    assert [(o.name, o.versions, o.file) for o in model.inactive] == [("ZSD_A99", ["M"], "RSOADSO.csv")]
    assert "ZSD_A99" not in model.adsos
    info = next(i for i in model.issues if i.object == "ZSD_A99")
    assert info.file == "RSOADSO.csv" and info.line


def test_encoding_recorded(model):
    assert model.files["RSDIOBJT"]["encoding"] == "Windows-1251"
    assert model.files["RSDIOBJ"]["encoding"] == "UTF-8"
    assert model.info_objects["0SOLD_TO"].description == "Заказчик"


def test_adso_fields_and_key(model):
    a02 = model.adsos["ZSD_A02"]
    assert a02.key == ["0SOLD_TO", "0CALMONTH", "0DOC_CURRCY", "0SALES_UNIT"]
    assert a02.bw_tables == {"inbound": "/BIC/AZSD_A021", "active": "/BIC/AZSD_A022"}
    assert a02.field("0NETVAL_INV").bw_column == "NETVAL_INV"
    assert model.adsos["ZSD_S01"].key == []  # staging без ключа


def test_transformations(model):
    by_target = {t.target.name: t for t in model.transformations.values()}
    tr00 = by_target["ZSD_A01"]
    assert tr00.source.key == "ZSALES_FF/FLATFILE"
    formula = next(r for r in tr00.rules if r.kind == RuleKind.FORMULA)
    assert formula.source_fields == ["NETVAL", "DISCOUNT"] and formula.param == "NETVAL - DISCOUNT"
    assert by_target["ZSD_A03"].routines[0].lines == 10
    read_md = next(r for r in by_target["ZSD_A04"].rules if r.kind == RuleKind.READ_MD)
    assert read_md.param == "0MATERIAL-0MATL_GROUP"


def test_tab_delimiter_and_bom_give_same_model(tmp_path, model):
    shutil.copytree(EXPORT, tmp_path / "x")
    path = tmp_path / "x" / "RSTRAN.csv"
    text = path.read_text(encoding="utf-8").replace(";", "\t")
    path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    other = import_bw_export(tmp_path / "x")
    assert other.files["RSTRAN"]["encoding"] == "UTF-8 с BOM"
    assert other.files["RSTRAN"]["delimiter"] == "табуляция"
    assert other.content_hash() == model.content_hash()


def test_broken_export_does_not_stop_import(tmp_path):
    # ПИ-3: удалённая колонка в RSDIOBJ.csv и испорченная строка в RSTRAN.csv
    folder = tmp_path / "broken"
    shutil.copytree(EXPORT, folder)
    rsdiobj = folder / "RSDIOBJ.csv"
    lines = rsdiobj.read_text(encoding="utf-8").splitlines()
    rsdiobj.write_text("\n".join(";".join(line.split(";")[:3]) for line in lines), encoding="utf-8")
    rstran = folder / "RSTRAN.csv"
    lines = rstran.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2] + ";лишнее"
    rstran.write_text("\n".join(lines), encoding="utf-8")

    model = import_bw_export(folder)
    col = next(i for i in model.issues if i.file == "RSDIOBJ.csv" and i.severity == Severity.ERROR)
    assert "IOBJTP" in col.message and "bw_tables.yaml" in col.message
    row = next(i for i in model.issues if i.file == "RSTRAN.csv" and i.line == 3)
    assert "строка пропущена" in row.message
    assert len(model.transformations) == 3
    assert model.info_objects == {}


def test_missing_folder():
    with pytest.raises(ImportFailed):
        import_bw_export(DEMO / "нет такой папки")


def test_decode_and_delimiter():
    assert decode("а;б".encode("cp1251"))[1] == "Windows-1251"
    assert decode("а;б".encode())[1] == "UTF-8"
    assert detect_delimiter("A\tB\tC") == "\t"
    assert detect_delimiter("A,B;C;D") == ";"


def test_parse_ref_padded_datasource():
    ref = parse_ref("RSDS", f"{'ZSALES_FF':<30}FLATFILE")
    assert (ref.name, ref.source_system) == ("ZSALES_FF", "FLATFILE")
    assert parse_ref("RSDS", "ZSALES_FF FLATFILE").source_system == "FLATFILE"
