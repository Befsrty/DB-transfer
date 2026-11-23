"""Строки ⚠ справочника типов Oracle и MS SQL: во что переносится тип и какое замечание видит пользователь."""

from pathlib import Path

import pytest

from dbtransfer.generator import TypeRules
from dbtransfer.generator.tables import TableBuilder
from dbtransfer.importers import mssql_dict, oracle_dict
from dbtransfer.model import Severity
from dbtransfer.runtime import check_sql, convert_sql, resolve_target

DATA = Path(__file__).parents[1] / "data"
RULES = TypeRules.load()


def model_type(datatype: str, length: int = 0, decimals: int = 0) -> str:
    from dbtransfer.model import FieldDef
    return RULES.column_type(FieldDef(name="X", position=1, datatype=datatype, length=length, decimals=decimals))


# --- Oracle ---

@pytest.mark.parametrize("args, datatype, target, lossy, words", [
    (("NUMBER", "5", "-2"), "INT4", "integer", False, "масштаб вне"),  # до 9 999 900
    (("NUMBER", "9", "-2"), "INT8", "bigint", False, "масштаб вне"),  # до 99 999 999 900 — уже не integer
    (("NUMBER", "3", "5"), "NUMERIC", "numeric", False, "масштаб вне"),
    (("NUMBER", "", ""), "NUMERIC", "numeric", False, "без точности"),
    (("TIMESTAMP(9)", "", "9"), "TIMESTAMP", "timestamp", True, "округляются до 6"),
    (("TIMESTAMP(6) WITH TIME ZONE", "", "6"), "TIMESTAMPTZ", "timestamptz", True, "часовой пояс"),
    (("ROWID", "", ""), "STRING", "text", False, "адрес строки"),
    (("CHAR", "", ""), "FIXCHAR", "char(2)", False, "пробелами"),
])
def test_oracle_caveats(args, datatype, target, lossy, words):
    data_type, precision, scale = args
    _, got, length, decimals, known = oracle_dict.map_type(data_type, 2, 2, precision, scale)
    assert known and got == datatype and model_type(got, length, decimals) == target
    text, is_lossy = oracle_dict.type_caveat(data_type, precision, scale)
    assert words in text and is_lossy == lossy


def test_oracle_plain_types_have_no_caveat():
    for args in (("VARCHAR2", "", ""), ("NUMBER", "10", "0"), ("DATE", "", ""), ("TIMESTAMP(6)", "", "6"),
                 ("TIMESTAMP(6) WITH LOCAL TIME ZONE", "", "6")):
        assert oracle_dict.type_caveat(*args) == ("", False)


def test_oracle_varchar2_byte_length():
    # VARCHAR2(100 BYTE): CHAR_LENGTH = 100; varchar(n) в UTF-8 считает символы, значения точно помещаются
    assert oracle_dict.map_type("VARCHAR2", 100, 100, "", "")[:3] == ("VARCHAR2(100)", "CHAR", 100)
    # VARCHAR2(50 CHAR) в AL32UTF8: DATA_LENGTH до 200 байт, длина берётся в символах
    assert oracle_dict.map_type("VARCHAR2", 200, 50, "", "")[:3] == ("VARCHAR2(50)", "CHAR", 50)


def test_oracle_issues_in_model():
    model = oracle_dict.import_oracle_dict(DATA / "oracle_dict")
    warn = [i.message for i in model.issues if i.severity == Severity.WARNING]
    info = [i.message for i in model.issues if i.severity == Severity.INFO]
    assert any("SALES.ORDER_LINES, колонка UPDATED_AT" in m and "часовой пояс" in m for m in warn)
    assert any("SALES.ORDERS, колонка TOTAL_WEIGHT" in m for m in info)
    assert TableBuilder(model, RULES).db_table(model.tables["SALES.CUSTOMERS"]).column("region").type == "char(2)"


# --- MS SQL Server ---

@pytest.mark.parametrize("args, lossy, words", [
    (("datetime2", "7"), True, "100 нс"),
    (("time", "7"), True, "100 нс"),
    (("datetimeoffset", "7"), True, "смещение не сохраняется"),
    (("datetimeoffset", "3"), True, "смещение не сохраняется"),
    (("datetime", "3"), False, "1/300"),
    (("uniqueidentifier", ""), False, "порядок сортировки"),
    (("rowversion", ""), False, "счётчик версий"),
    (("nchar", ""), False, "пробелами"),
])
def test_mssql_caveats(args, lossy, words):
    text, is_lossy = mssql_dict.type_caveat(*args)
    assert words in text and is_lossy == lossy


def test_mssql_datetimeoffset7_has_both():
    text, _ = mssql_dict.type_caveat("datetimeoffset", "7")
    assert "100 нс" in text and "смещение" in text


def test_mssql_plain_types_have_no_caveat():
    for args in (("datetime2", "6"), ("time", "0"), ("nvarchar", ""), ("int", ""), ("date", "0")):
        assert mssql_dict.type_caveat(*args) == ("", False)


@pytest.mark.parametrize("data_type", ["sql_variant", "hierarchyid", "geography", "geometry"])
def test_mssql_exotic_types_become_text(data_type):
    _, datatype, _, _, known = mssql_dict.map_type(data_type, "", "", "")
    assert not known and model_type(datatype) == "text"


def test_mssql_issues_in_model():
    model = mssql_dict.import_mssql_dict(DATA / "mssql_dict")
    warn = [i.message for i in model.issues if i.severity == Severity.WARNING]
    info = [i.message for i in model.issues if i.severity == Severity.INFO]
    assert any("dbo.Order, колонка Created" in m and "100 нс" in m for m in warn)
    assert any("dbo.AuditLog, колонка At" in m and "смещение" in m for m in warn)
    assert any("dbo.Customer, колонка Guid" in m for m in info)
    # cp1251: varchar в Cyrillic_General_CI_AS — одно замечание на базу, nvarchar (Юникод) не считается
    code_page = [m for m in info if "кодовой странице" in m]
    assert len(code_page) == 1 and "CYRILLIC_GENERAL_CI_AS" in code_page[0]
    # CI-collation: ключ сравнивается через lower() и при загрузке, и при сверке
    assert resolve_target(model, RULES, "dbo.Country", []).ci_key == ["code"]


def test_fractional_seconds_accepted():
    model = mssql_dict.import_mssql_dict(DATA / "mssql_dict")
    target = resolve_target(model, RULES, "dbo.Order", [])
    created = target.table.column("created")
    assert check_sql(created, "v")[0][1] == "не дата и время ГГГГ-ММ-ДД ЧЧ:ММ:СС"
    assert "::timestamp" in convert_sql(created, "v", RULES)  # 7 знаков: PostgreSQL сам округлит до 6
