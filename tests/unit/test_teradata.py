"""Импорт словаря Teradata (DBC): типы, первичный индекс, SET-таблицы, секции RANGE_N, код и скрипты, DDL,
выгрузка данных через DB-API. Живого Teradata нет: словарь — синтетика tests/data/teradata_dict (допущение)."""

import re
import shutil
import sqlite3
import sys
import types
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbtransfer.analysis.assessment import assess
from dbtransfer.cli import app
from dbtransfer.generator import TypeRules, generate
from dbtransfer.importers import teradata
from dbtransfer.importers.csv_reader import Row, TableFile
from dbtransfer.importers.rdbms_dict import TARGET_ADSO, DictImportFailed
from dbtransfer.importers.sources import export_help, import_model
from dbtransfer.model import FieldDef, ObjectType, Severity, SourceKind
from dbtransfer.runtime import convert_sql, resolve_target
from dbtransfer.transfer import _teradata_sum, csv_value, export_dbapi

DATA = Path(__file__).parents[1] / "data" / "teradata_dict"
RULES = TypeRules.load()
runner = CliRunner()


@pytest.fixture(scope="module")
def td():
    return teradata.import_teradata(DATA)


def table_sql(model, dialect: str, name: str) -> str:
    package = generate(model, TypeRules.load(), dialect)
    return next(o.sql for o in package.objects if o.kind == "table" and o.name == name).split("COMMENT ON")[0]


def messages(model, severity=None) -> list[str]:
    return [i.message for i in model.issues if severity is None or i.severity == severity]


# --- типы ---

@pytest.mark.parametrize(("args", "expected"), [
    (("CV", "200", "", "", "2"), ("VARCHAR(100) UNICODE", "CHAR", 100, 0, True, "")),
    (("CV", "50", "", "", "1"), ("VARCHAR(50)", "CHAR", 50, 0, True, "")),
    (("CF", "40", "", "", "2"), ("CHAR(20) UNICODE", "FIXCHAR", 20, 0, True, "")),
    (("CO", "2097152"), ("CLOB", "STRING", 0, 0, True, "")),
    (("I1",), ("BYTEINT", "INT2", 0, 0, True, "")),
    (("I ",), ("INTEGER", "INT4", 0, 0, True, "")),
    (("I8",), ("BIGINT", "INT8", 0, 0, True, "")),
    (("D ", "8", "15", "2"), ("DECIMAL(15,2)", "DEC", 15, 2, True, "")),
    (("N ", "18", "12", "2"), ("NUMBER(12,2)", "DEC", 12, 2, True, "")),
    (("N ", "18", "-128", "-128"), ("NUMBER", "NUMERIC", 0, 0, True, "")),
    (("F ",), ("FLOAT", "FLTP", 0, 0, True, "")),
    (("DA",), ("DATE", "DATS", 8, 0, True, "")),
    (("AT", "15", "", "6"), ("TIME(6)", "TIMS", 6, 0, True, "")),
    (("TS", "26", "", "6"), ("TIMESTAMP(6)", "TIMESTAMP", 0, 0, True, "")),
    (("TZ", "21", "", "0"), ("TIME(0) WITH TIME ZONE", "NATIVE", 0, 0, True, "timetz(0)")),
    (("SZ", "32", "", "6"), ("TIMESTAMP(6) WITH TIME ZONE", "TIMESTAMPTZ", 0, 0, True, "")),
    (("BV", "16"), ("VARBYTE(16)", "BINARY", 0, 0, True, "")),
    (("BO",), ("BLOB", "BINARY", 0, 0, True, "")),
    (("DS",), ("INTERVAL DAY TO SECOND", "NATIVE", 0, 0, True, "interval day to second")),
    (("MO",), ("INTERVAL MONTH", "NATIVE", 0, 0, True, "interval month")),
    (("JN",), ("JSON", "NATIVE", 0, 0, True, "jsonb")),
    (("PD",), ("PERIOD(DATE)", "STRING", 0, 0, True, "")),
    (("UT",), ("UDT", "STRING", 0, 0, True, "")),
    (("ZZ",), ("ZZ", "ZZ", 0, 0, False, "")),
])
def test_map_type(args, expected):
    t = teradata.map_type(*args)
    assert (t.shown, t.datatype, t.length, t.decimals, t.known, t.target_type) == expected


def test_type_caveats():
    assert teradata.type_caveat("PD")[1] is True and "PERIOD" in teradata.type_caveat("PD")[0]
    assert teradata.type_caveat("SZ")[1] is True
    assert teradata.type_caveat("N", "12", "2") == ("", False)
    assert "без точности" in teradata.type_caveat("N", "-128", "-128")[0]
    assert teradata.type_caveat("I ") == ("", False)


def test_rules_have_model_types():
    """Каждый тип модели, который даёт импорт Teradata, есть в type_rules.yaml (или переносится как есть)."""
    for code in ("CF", "CV", "CO", "I1", "I2", "I ", "I8", "D ", "N ", "F ", "DA", "AT", "TS", "SZ", "BF", "PD",
                 "XM", "UT"):
        t = teradata.map_type(code, "10", "10", "2")
        assert t.datatype in RULES.types, code
    for code in ("TZ", "JN", *teradata._INTERVALS):
        assert teradata.map_type(code).target_type, code


# --- секционирование ---

COLS = {"D": "D", "ORDERDATE": "OrderDate", "N": "N"}


def test_range_n_monthly():
    p, notes = teradata.partitioning_from(
        "CHECK ((RANGE_N(OrderDate  BETWEEN DATE '2024-01-01' AND DATE '2024-12-31' EACH INTERVAL '1' MONTH , "
        "NO RANGE)) BETWEEN 1 AND 13)", COLS, "S.T")
    assert p.kind == "RANGE" and p.columns == ["OrderDate"] and len(p.partitions) == 13
    assert (p.partitions[0].name, p.partitions[0].start, p.partitions[0].values) == (
        "p202401", "DATE '2024-01-01'", ["DATE '2024-02-01'"])
    assert p.partitions[11].values == ["DATE '2025-01-01'"]  # верхняя граница Teradata включительно
    assert p.partitions[-1].is_default and "секций 13" in notes[0]


def test_range_n_integer_uneven_and_unknown():
    p, notes = teradata.partitioning_from("RANGE_N(N BETWEEN 1 AND 100 EACH 30, UNKNOWN)", COLS, "S.T")
    assert [(x.start, x.values) for x in p.partitions[:-1]] == [("1", ["31"]), ("31", ["61"]), ("61", ["91"]),
                                                                ("91", ["101"])]
    assert p.partitions[-1].is_default and "UNKNOWN" in notes[0]


def test_range_n_open_bounds_and_points():
    p, _ = teradata.partitioning_from("RANGE_N(N BETWEEN *, 10, 20 AND *)", COLS, "S.T")
    assert [(x.name, x.start, x.values) for x in p.partitions] == [
        ("p_min", "", ["10"]), ("p10", "10", ["20"]), ("p20", "20", [])]
    assert not any(x.is_default for x in p.partitions)


def test_range_n_daily_coarsened_and_multilevel():
    p, notes = teradata.partitioning_from(
        "PARTITION#L1 = RANGE_N(D BETWEEN DATE '2015-01-01' AND DATE '2024-12-31' EACH INTERVAL '1' DAY) AND "
        "PARTITION#L2 = CASE_N(V < 0, NO CASE)", COLS, "S.T")
    assert len(p.partitions) == 120 and p.partitions[0].name == "p201501"
    assert "многоуровневое" in notes[0] and "укрупнены до месяца" in notes[1]


def test_range_n_weekly_and_yearly():
    p, _ = teradata.partitioning_from("RANGE_N(D BETWEEN DATE '2024-01-01' AND DATE '2024-01-31' "
                                      "EACH INTERVAL '7' DAY)", COLS, "S.T")
    assert [x.values[0] for x in p.partitions] == ["DATE '2024-01-08'", "DATE '2024-01-15'", "DATE '2024-01-22'",
                                                   "DATE '2024-01-29'", "DATE '2024-02-01'"]
    p, _ = teradata.partitioning_from("RANGE_N(D BETWEEN '2020-01-31' AND '2022-12-31' EACH INTERVAL '1' YEAR)",
                                      COLS, "S.T")
    assert [x.name for x in p.partitions] == ["p2020", "p2021", "p2022"]


@pytest.mark.parametrize(("text", "part"), [
    ("CASE_N(V < 0, NO CASE)", "CASE_N не переносится"),
    ("RANGE_N(EXTRACT(YEAR FROM D) BETWEEN 2020 AND 2030 EACH 1)", "не разобрано"),
    ("RANGE_N(Other BETWEEN 1 AND 10)", "по выражению, а не по колонке"),
    ("RANGE_N(D BETWEEN TIMESTAMP '2020-01-01 00:00:00' AND TIMESTAMP '2021-01-01 00:00:00' "
     "EACH INTERVAL '1' HOUR)", "не разобрана"),
    ("RANGE_N(N BETWEEN 1 AND 100000 EACH 1)", "больше 1000"),
])
def test_range_n_not_transferred(text, part):
    p, notes = teradata.partitioning_from(text, COLS, "S.T")
    assert p is None and part in " ".join(notes)


# --- модель из словаря ---

def test_model_tables_keys_distribution(td):
    assert td.source_kind == SourceKind.TERADATA and td.source_label == "Teradata"
    assert sorted(td.tables) == ["SALES.Clicks", "SALES.Customers", "SALES.Daily", "SALES.EventLog",
                                 "SALES.OrderLines", "SALES.Orders", "SALES.TmpLoad"]
    customers = td.tables["SALES.Customers"]  # SET-таблица с UPI: ключ и распределение — первичный индекс
    assert customers.key == ["CustomerId"] and customers.distributed_by == ["CustomerId"]
    orders = td.tables["SALES.Orders"]  # NUPI CustomerId + PRIMARY KEY OrderId
    assert orders.key == ["OrderId"] and orders.distribution == "HASH" and orders.distributed_by == ["CustomerId"]
    assert td.tables["SALES.OrderLines"].key == ["OrderId", "LineNo"]  # порядок по ColumnPosition
    assert td.tables["SALES.Clicks"].distribution == "RANDOM"  # NoPI
    assert td.tables["SALES.Clicks"].storage == "ao_column"  # PARTITION BY COLUMN
    assert td.tables["SALES.EventLog"].key == [] and td.tables["SALES.EventLog"].distributed_by == ["EventType"]
    assert "DBC.TVM" not in td.tables


def test_model_field_types(td):
    fields = {f.name: f for f in td.tables["SALES.Orders"].fields}
    assert (fields["Amount"].datatype, fields["Amount"].length, fields["Amount"].decimals) == ("DEC", 15, 2)
    assert fields["Delivery"].target_type == "interval day to second"
    assert fields["OrderDate"].comment == "DATE"
    name = next(f for f in td.tables["SALES.Customers"].fields if f.name == "Name")
    assert (name.datatype, name.length, name.nullable) == ("CHAR", 100, False)


def test_model_notes(td):
    text = "\n".join(messages(td))
    for part in ("SALES.EventLog — SET-таблица без уникального ключа", "Teradata 17.20.03.09",
                 "Hash-индекс SALES.hiOrders", "NOT CASESPECIFIC: 4", "колонки IDENTITY (ClickId)",
                 "OrderDate (YY/MM/DD)", "COMPRESS", "GLOBAL TEMPORARY", "Внешний ключ FK_Lines_Orders",
                 "PERIOD(DATE)", "Тип ZZ колонки SALES.TmpLoad.Odd не распознан", "укрупнены до месяца"):
        assert part in text, part
    # Ключ Orders не объявляется (секционирование по OrderDate): про потерю размещения не предупреждаем
    assert "первичный индекс (CustomerId) не входит" not in text
    assert not any("SET-таблица" in m and "Customers" in m for m in messages(td))


def test_pi_outside_key_note():
    files = {
        "TABLESV": _file("TABLESV", [{"DATABASENAME": "S", "TABLENAME": "T", "TABLEKIND": "T", "CHECKOPT": "N"}]),
        "COLUMNSV": _file("COLUMNSV", [
            {"DATABASENAME": "S", "TABLENAME": "T", "COLUMNNAME": c, "COLUMNID": str(i), "COLUMNTYPE": "I"}
            for i, c in enumerate(["A", "B"], start=1)]),
        "INDICESV": _file("INDICESV", [
            {"DATABASENAME": "S", "TABLENAME": "T", "INDEXNUMBER": "1", "INDEXTYPE": "P", "UNIQUEFLAG": "N",
             "COLUMNNAME": "B", "COLUMNPOSITION": "1"},
            {"DATABASENAME": "S", "TABLENAME": "T", "INDEXNUMBER": "2", "INDEXTYPE": "S", "UNIQUEFLAG": "Y",
             "INDEXNAME": "USI_A", "COLUMNNAME": "A", "COLUMNPOSITION": "1"}]),
    }
    catalog = teradata.catalog_from(files, lambda s: True)
    t = catalog.tables[0]
    assert (t.key, t.key_kind, t.key_constraint) == (["A"], "уникальный", "USI_A (UNIQUE SECONDARY INDEX)")
    assert any("первичный индекс (B) не входит" in n for n in t.notes)


def _file(name: str, rows: list[dict]) -> TableFile:
    return TableFile(table=name, usable=True, rows=[Row(i, r) for i, r in enumerate(rows, start=2)])


def test_model_code_and_scripts(td):
    others = {o.name: o for o in td.others}
    view = others["SALES.vLastOrder"]
    assert view.type == ObjectType.VIEW and "QUALIFY" in view.info and "LOCKING … FOR ACCESS" in view.info
    assert {r.name for r in view.related} == {"SALES.Customers", "SALES.Orders"}
    assert others["SALES.mLoadDay"].info.startswith("макрос (конструкции Teradata: SEL/INS/UPD/DEL, FORMAT)")
    assert "MERGE" in others["SALES.spRecalc"].info and others["SALES.spRecalc"].lines == 6
    assert others["SALES.jiOrders"].type == ObjectType.VIEW and others["SALES.trgOrders"].type == ObjectType.DB_ROUTINE
    bteq = others["load_orders.bteq"]
    assert bteq.type == ObjectType.SCRIPT and bteq.lines == 8 and "команды BTEQ" in bteq.info
    assert [r.name for r in bteq.related] == ["SALES.Orders"]  # DATABASE SALES; — база по умолчанию
    assert others["customers.fld"].info.startswith("скрипт FastLoad")
    assert "SALES.hiOrders" not in others


def test_features_skip_literals_and_comments():
    assert teradata.teradata_features("SELECT 'QUALIFY' FROM t -- SEL\n/* TOP 5 */") == []
    assert teradata.teradata_features("SELECT TOP 10 * FROM t SAMPLE 100") == ["TOP n", "SAMPLE"]


def test_schema_filter_and_scripts_option(td, tmp_path):
    assert not teradata.import_teradata(DATA, schemas=["OTHER"]).tables
    assert sorted(teradata.import_teradata(DATA, schemas=["sales"]).tables) == sorted(td.tables)
    (tmp_path / "etl").mkdir()
    (tmp_path / "etl" / "x.tpt").write_text("DEFINE JOB load_x (\n  APPLY 'INS INTO SALES.Orders ...';\n);\n",
                                            encoding="utf-8")
    model = teradata.import_teradata(DATA, scripts=tmp_path)
    assert [o.name for o in model.others if o.type == ObjectType.SCRIPT] == ["etl/x.tpt"]
    assert next(o for o in model.others if o.name == "etl/x.tpt").info.startswith("скрипт TPT")
    with pytest.raises(DictImportFailed, match="Папка скриптов"):
        teradata.import_teradata(DATA, scripts=tmp_path / "none")


def test_adso_mode(td):
    model = teradata.import_teradata(DATA, target=TARGET_ADSO)
    assert "SALES_Orders" in model.adsos and not model.tables
    assert any("в режиме ADSO берутся из type_rules.yaml" in m for m in messages(model))


# --- DDL ---

def test_ddl_greenplum7(td):
    orders = table_sql(td, "greenplum7", "dbt_sales.orders")
    assert "DISTRIBUTED BY (customerid)\nPARTITION BY RANGE (orderdate)" in orders
    assert "PARTITION p202412 START (DATE '2024-12-01') INCLUSIVE END (DATE '2025-01-01') EXCLUSIVE" in orders
    assert "DEFAULT PARTITION p_other" in orders and "delivery   interval day to second" in orders
    customers = table_sql(td, "greenplum7", "dbt_sales.customers")
    assert "PRIMARY KEY (customerid)" in customers and "DISTRIBUTED BY (customerid)" in customers
    assert "name       varchar(100) NOT NULL" in customers
    clicks = table_sql(td, "greenplum7", "dbt_sales.clicks")
    assert "USING ao_column" in clicks and "DISTRIBUTED RANDOMLY" in clicks
    events = table_sql(td, "greenplum7", "dbt_sales.eventlog")
    assert "payload   jsonb" in events and "validity  text" in events and "DISTRIBUTED BY (eventtype)" in events
    lines = table_sql(td, "greenplum7", "dbt_sales.orderlines")
    assert "PRIMARY KEY (orderid, lineno)" in lines and "DISTRIBUTED BY (orderid, lineno)" in lines


def test_ddl_greenplum6(td):
    clicks = table_sql(td, "greenplum", "dbt_sales.clicks")
    assert "WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)" in clicks
    assert "PARTITION p1 START (1) INCLUSIVE END (26) EXCLUSIVE" in table_sql(td, "greenplum", "dbt_sales.orderlines")


def test_ci_key_columns():
    files = {
        "TABLESV": _file("TABLESV", [{"DATABASENAME": "S", "TABLENAME": "T", "TABLEKIND": "T", "CHECKOPT": "Y"}]),
        "COLUMNSV": _file("COLUMNSV", [{"DATABASENAME": "S", "TABLENAME": "T", "COLUMNNAME": "CODE",
                                        "COLUMNID": "1", "COLUMNTYPE": "CV", "COLUMNLENGTH": "10",
                                        "UPPERCASEFLAG": "N", "NULLABLE": "N"}]),
        "INDICESV": _file("INDICESV", [{"DATABASENAME": "S", "TABLENAME": "T", "INDEXNUMBER": "1",
                                        "INDEXTYPE": "P", "UNIQUEFLAG": "Y", "COLUMNNAME": "CODE",
                                        "COLUMNPOSITION": "1"}]),
    }
    from dbtransfer.importers.rdbms_dict import build_model

    model = build_model(teradata.catalog_from(files, lambda s: True), files, SourceKind.TERADATA, "x", "TD", "TD")
    assert model.tables["S.T"].ci_columns == ["CODE"]
    assert any("Teradata NOT CASESPECIFIC" in m for m in messages(model, Severity.WARNING))


def test_native_conversion(td):
    target = resolve_target(td, RULES, "SALES.Orders", ["OrderId", "Delivery"])
    col = target.table.column("delivery")
    assert convert_sql(col, "r.delivery", RULES) == "nullif(r.delivery, '')::interval day to second"


# --- отчёт, справка, командная строка ---

def test_assessment(td):
    rows = {a.name: a for a in assess(td).objects}
    assert rows["SALES.spRecalc"].type == "Процедура, функция, триггер" and rows["SALES.spRecalc"].level == 3
    assert "PL/pgSQL" in rows["SALES.spRecalc"].reason
    assert rows["SALES.vLastOrder"].level == 2 and "QUALIFY" in rows["SALES.vLastOrder"].reason
    assert rows["load_orders.bteq"].type == "Скрипт загрузки" and rows["load_orders.bteq"].hours == 6


def test_help_and_import_model():
    text = export_help("teradata")
    assert "-- TablesV.csv" in text and "DBC.IndicesV" in text and "DatabaseName IN ('SALES', 'DWH')" in text
    assert import_model("teradata", DATA).tables
    with pytest.raises(Exception, match="только у источника teradata"):
        import_model("oracle", DATA, scripts=DATA / "bteq")


def test_connection_import_with_fake_driver():
    """Подключение: запросы TERADATA_QUERIES с подставленными базами, ответ — как из CSV."""
    files = {name: teradata.read_folder(DATA, teradata.source_tables(SourceKind.TERADATA), teradata.HOW)[name]
             for name in ("TABLESV", "COLUMNSV")}
    seen = []

    class Cursor:
        def execute(self, sql):
            seen.append(sql)
            name = re.search(r"FROM DBC\.(\w+)", sql)[1].upper()
            f = files.get(name)
            if f is None:
                raise RuntimeError("нет прав")
            header = list(f.rows[0].values)
            self.description = [(h,) for h in header]
            self.rows = [tuple(r.values.values()) for r in f.rows]

        def fetchmany(self, n):
            chunk, self.rows = self.rows[:n], self.rows[n:]
            return chunk

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

    with pytest.raises(DictImportFailed, match="укажите базы"):
        teradata.import_teradata(conn=Conn())
    model = teradata.import_teradata(conn=Conn(), schemas=["SALES"])
    assert "SALES.Orders" in model.tables and model.source == "подключение к Teradata"
    assert all("IN ('SALES')" in s for s in seen if "DBCInfoV" not in s)
    assert any("Запрос к INDICESV не выполнен" in m for m in messages(model, Severity.WARNING))


def test_connect_errors(monkeypatch):
    with pytest.raises(DictImportFailed, match="teradata://"):
        teradata.connect("oracle://x@y/z")
    monkeypatch.setitem(sys.modules, "teradatasql", None)
    with pytest.raises(DictImportFailed, match="uv sync --extra teradata"):
        teradata.connect("teradata://u@host/")
    calls = {}
    monkeypatch.setitem(sys.modules, "teradatasql", types.SimpleNamespace(connect=lambda **kw: calls.update(kw)))
    teradata.connect("teradata://etl:p%40ss@td-host:1025/SALES?logmech=LDAP&encryptdata=true")
    assert calls == {"host": "td-host", "user": "etl", "password": "p@ss", "dbs_port": "1025", "database": "SALES",
                     "logmech": "LDAP", "encryptdata": "true"}


def test_cli_import(tmp_path):
    shutil.copytree(Path(__file__).parents[2] / "demo", tmp_path / "demo")
    result = runner.invoke(app, ["import", str(DATA), "--source", "teradata", "-p", str(tmp_path / "demo")])
    assert result.exit_code == 0, result.output  # нераспознанный тип ZZ — предупреждение, не ошибка
    assert "Teradata: таблиц 7 (без ключа 4, секционированных 3)" in result.output
    result = runner.invoke(app, ["tables", "--source", "teradata"])
    assert result.exit_code == 0 and "INDICESV" in result.output and "DBC.TablesV" in result.output


# --- выгрузка данных через DB-API ---

def test_csv_value():
    assert csv_value(None) == "\\N"
    assert csv_value(b"\x0a\x0b") == "0x0a0b"
    assert csv_value(Decimal("1E+3")) == "1000"
    assert csv_value(0.1) == "0.1"
    assert csv_value(date(2024, 1, 31)) == "2024-01-31"
    assert csv_value(datetime(2024, 1, 31, 10, 15, 0, 123000)) == "2024-01-31 10:15:00.123000"
    assert csv_value(datetime(2024, 1, 31, 10, 0, tzinfo=UTC)) == "2024-01-31 10:00:00+00:00"
    assert csv_value(time(8, 30)) == "08:30:00"
    assert csv_value(True) == "true" and csv_value(" 3 04:05:06") == " 3 04:05:06"


def test_teradata_sums():
    assert _teradata_sum(FieldDef(name="Qty", position=1, datatype="INT4", length=0)) == \
        'SUM(CAST("Qty" AS DECIMAL(38,0)))'
    assert _teradata_sum(FieldDef(name="Amt", position=2, datatype="DEC", length=15, decimals=2)) == \
        'SUM(CAST("Amt" AS DECIMAL(38,2)))'
    assert _teradata_sum(FieldDef(name="F", position=3, datatype="FLTP", length=0)) == 'SUM("F")'


def test_export_dbapi_sqlite(td, tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH ':memory:' AS SALES")
    conn.execute('CREATE TABLE SALES.OrderLines (OrderId, LineNo, Sku, Qty, Price)')
    conn.execute("INSERT INTO SALES.OrderLines VALUES (1, 1, 'A-1', 2.5, NULL), (1, 2, '', 1, 10)")
    path = export_dbapi(conn, td.tables["SALES.OrderLines"], tmp_path / "x.csv")
    assert path.read_text(encoding="utf-8").splitlines() == [
        "OrderId,LineNo,Sku,Qty,Price", "1,1,A-1,2.5,\\N", "1,2,,1,10"]
