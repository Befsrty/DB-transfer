"""Импорт словаря Oracle и MS SQL Server: модель, типы, ключи, прочие объекты, SQL, отчёт, командная строка."""

import csv
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbtransfer.analysis.assessment import assess
from dbtransfer.cli import app
from dbtransfer.generator import TypeRules, generate
from dbtransfer.generator.tables import TableBuilder
from dbtransfer.importers import mssql_dict, oracle_dict
from dbtransfer.importers.bw_csv import ImportFailed
from dbtransfer.importers.sources import export_help, import_model
from dbtransfer.model import AdsoKind, Model, ObjectType, Severity, SourceKind
from dbtransfer.runtime import check_sql, convert_sql, resolve_target

DATA = Path(__file__).parents[1] / "data"
RULES = TypeRules.load()
runner = CliRunner()


@pytest.fixture(scope="module")
def ora():
    return oracle_dict.import_oracle_dict(DATA / "oracle_dict")


@pytest.fixture(scope="module")
def ms():
    return mssql_dict.import_mssql_dict(DATA / "mssql_dict")


def messages(model: Model, severity: Severity) -> list[str]:
    return [i.message for i in model.issues if i.severity == severity]


# --- типы ---

@pytest.mark.parametrize("args, expected", [
    (("VARCHAR2", 400, 100, "", ""), ("VARCHAR2(100)", "CHAR", 100, 0)),
    (("NUMBER", 22, 0, "10", "0"), ("NUMBER(10)", "INT8", 10, 0)),
    (("NUMBER", 22, 0, "9", "0"), ("NUMBER(9)", "INT4", 9, 0)),
    (("NUMBER", 22, 0, "15", "2"), ("NUMBER(15,2)", "DEC", 15, 2)),
    (("NUMBER", 22, 0, "25", "0"), ("NUMBER(25)", "DEC", 25, 0)),
    (("NUMBER", 22, 0, "", ""), ("NUMBER", "NUMERIC", 0, 0)),
    (("NUMBER", 22, 0, "", "0"), ("NUMBER(*,0)", "DEC", 38, 0)),
    (("DATE", 7, 0, "", ""), ("DATE", "TIMESTAMP", 0, 0)),
    (("TIMESTAMP(6) WITH LOCAL TIME ZONE", 11, 0, "", "6"), ("TIMESTAMP(6) WITH LOCAL TIME ZONE", "TIMESTAMPTZ", 0, 0)),
    (("CLOB", 4000, 0, "", ""), ("CLOB", "STRING", 0, 0)),
    (("BLOB", 4000, 0, "", ""), ("BLOB", "BINARY", 0, 0)),
])
def test_oracle_types(args, expected):
    *result, known = oracle_dict.map_type(*args)
    assert tuple(result) == expected and known


@pytest.mark.parametrize("args, expected", [
    (("nvarchar", "50", "", ""), ("nvarchar(50)", "CHAR", 50, 0)),
    (("varchar", "-1", "", ""), ("varchar(max)", "STRING", 0, 0)),
    (("decimal", "", "18", "4"), ("decimal(18,4)", "DEC", 18, 4)),
    (("money", "", "19", "4"), ("money", "DEC", 19, 4)),
    (("datetime2", "", "", ""), ("datetime2", "TIMESTAMP", 0, 0)),
    (("date", "", "", ""), ("date", "DATS", 8, 0)),
    (("bit", "", "1", "0"), ("bit", "INT1", 1, 0)),
    (("uniqueidentifier", "", "", ""), ("uniqueidentifier", "CHAR", 36, 0)),
])
def test_mssql_types(args, expected):
    *result, known = mssql_dict.map_type(*args)
    assert tuple(result) == expected and known


def test_unknown_types_become_text(ora, ms):
    assert not oracle_dict.map_type("SDO_GEOMETRY", 1, 0, "", "")[-1]
    assert any("SDO_GEOMETRY" in m and "text" in m for m in messages(ora, Severity.WARNING))
    assert any("sql_variant" in m for m in messages(ms, Severity.WARNING))
    assert TableBuilder(ora, RULES).datasource_table(ora.datasources["SALES_LOG_EVENTS/ORACLE"]) \
        .column("location").type == "text"


# --- модель ---

def test_oracle_model(ora):
    assert ora.source_kind == SourceKind.ORACLE and not ora.is_bw
    # SYS отброшена как служебная, MV_SALES — материализованное представление, а не таблица
    assert sorted(ora.adsos) == ["SALES_CUSTOMERS", "SALES_LOG_EVENTS", "SALES_ORDERS", "SALES_ORDER_LINES"]
    orders = ora.adsos["SALES_ORDERS"]
    assert orders.kind == AdsoKind.STANDARD and orders.key == ["ORDER_ID"] and orders.info_area == "SALES"
    assert orders.description == "Заказы клиентов"
    assert orders.field("AMOUNT").comment == "NUMBER(15,2). Сумма заказа, руб."
    # Ключ из уникального ограничения, порядок колонок по POSITION
    assert ora.adsos["SALES_ORDER_LINES"].key == ["ORDER_ID", "LINE_NO"]
    assert ora.adsos["SALES_LOG_EVENTS"].kind == AdsoKind.STAGING
    tr = ora.transformations["TR_SALES_ORDERS"]
    assert tr.source.key == "SALES_ORDERS/ORACLE" and tr.target.name == "SALES_ORDERS"
    assert [(r.source_fields, r.target_fields) for r in tr.rules][:2] == [(["ORDER_ID"], ["ORDER_ID"]),
                                                                        (["CUSTOMER_ID"], ["CUSTOMER_ID"])]
    assert ora.dtps["DTP_SALES_ORDERS"].mode == "DELTA"


def test_oracle_issues_and_others(ora):
    info = messages(ora, Severity.INFO)
    assert any("FK_ORDERS_CUSTOMER" in m and "SALES.CUSTOMERS" in m for m in info)
    assert any("секционирована" in m for m in info)
    assert any("уникальный UQ_ORDER_LINES" in m for m in info)
    assert any("нет первичного и уникального ключа" in m for m in messages(ora, Severity.WARNING))
    others = {o.name: o for o in ora.others}
    assert others["SALES.PKG_LOAD"].type == ObjectType.DB_ROUTINE and others["SALES.PKG_LOAD"].lines == 200
    assert "INVALID" in others["SALES.P_RECALC"].info
    view = others["SALES.V_SALES_BY_REGION"]
    assert view.type == ObjectType.VIEW and view.lines == 4
    assert {r.name for r in view.related} == {"SALES_ORDERS", "SALES_CUSTOMERS"}
    assert others["SALES.MV_SALES"].type == ObjectType.VIEW


def test_schema_filter():
    model = oracle_dict.import_oracle_dict(DATA / "oracle_dict", schemas=["sys"])
    assert list(model.adsos) == ["SYS_AUD$"]


def test_mssql_model(ms):
    assert sorted(ms.adsos) == ["dbo_AuditLog", "dbo_Customer", "dbo_Order"]
    order = ms.adsos["dbo_Order"]
    assert order.key == ["OrderId"] and order.description == "Заказы интернет-магазина"
    assert {f.name: f.datatype for f in order.fields} == {
        "OrderId": "INT8", "CustomerId": "INT4", "Created": "TIMESTAMP", "Total": "DEC", "Comment": "STRING",
        "RowVer": "BINARY", "ShipTime": "TIMS"}
    assert next(iter(ms.datasources.values())).source_system == "SHOP"  # имя базы из TABLE_CATALOG
    others = {o.name: o for o in ms.others}
    assert others["dbo.usp_LoadOrders"].lines == 140 and "процедура T-SQL" in others["dbo.usp_LoadOrders"].info
    assert [r.name for r in others["dbo.vOrderTotals"].related] == ["dbo_Order"]
    assert any("FK_Order_Customer" in m for m in messages(ms, Severity.INFO))


def test_content_hash_ignores_source_kind(ora):
    copy = ora.model_copy(deep=True)
    copy.source_kind = SourceKind.MSSQL
    assert copy.content_hash() == ora.content_hash()


def test_missing_required_file(tmp_path):
    shutil.copy(DATA / "oracle_dict" / "ALL_TABLES.csv", tmp_path)
    model = oracle_dict.import_oracle_dict(tmp_path)
    errors = messages(model, Severity.ERROR)
    assert any("ALL_TAB_COLUMNS.csv" in m and "dbtransfer tables --source oracle" in m for m in errors)
    (tmp_path / "empty").mkdir()
    with pytest.raises(ImportFailed, match="ALL_TABLES.csv"):
        oracle_dict.import_oracle_dict(tmp_path / "empty")


def test_reserved_and_long_names(tmp_path):
    shutil.copytree(DATA / "oracle_dict", tmp_path, dirs_exist_ok=True)
    with (tmp_path / "ALL_TAB_COLUMNS.csv").open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(["SALES", "CUSTOMERS", "LOAD_ID", 9, "NUMBER", 22, 0, 10, 0, "Y"])
        csv.writer(f).writerow(["SALES", "T" * 60, "ID", 1, "NUMBER", 22, 0, 10, 0, "Y"])
    with (tmp_path / "ALL_TABLES.csv").open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(["SALES", "T" * 60, "NO", "", "N"])
    model = oracle_dict.import_oracle_dict(tmp_path)
    errors = messages(model, Severity.ERROR)
    assert any("LOAD_ID" in m and "служебной" in m for m in errors)
    assert any("длиннее 63" in m for m in messages(model, Severity.WARNING))


# --- подключение к базе (DB-API) ---

class FakeCursor:
    """Отвечает на запросы словаря строками из CSV-выгрузки: имя представления — после FROM."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.description = None
        self.rows = []
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)
        name = next(n for n in oracle_dict.ORACLE_QUERIES if f"FROM {n} " in sql)
        with (self.folder / f"{name}.csv").open(encoding="utf-8") as f:
            data = list(csv.reader(f))
        self.description = [(h,) for h in data[0]]
        self.rows = [tuple(None if v == "" else v for v in r) for r in data[1:]]

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, folder):
        self.cursor_obj = FakeCursor(folder)

    def cursor(self):
        return self.cursor_obj


def test_live_connection_matches_folder(ora):
    conn = FakeConn(DATA / "oracle_dict")
    live = oracle_dict.import_oracle_dict(conn=conn, schemas=["SALES"])
    assert sorted(live.adsos) == sorted(ora.adsos)
    assert live.adsos["SALES_ORDERS"] == ora.adsos["SALES_ORDERS"]
    assert all("OWNER IN ('SALES')" in sql for sql in conn.cursor_obj.sql)
    assert live.files["ALL_TABLES"]["file"] == "из базы"
    with pytest.raises(ImportFailed, match="укажите схемы"):
        oracle_dict.import_oracle_dict(conn=conn)


def test_help_has_queries():
    text = export_help("oracle")
    assert "FROM ALL_TAB_COLUMNS WHERE OWNER IN ('HR', 'SALES')" in text
    assert "INFORMATION_SCHEMA.COLUMNS" in export_help("mssql")
    with pytest.raises(ImportFailed, match="неизвестен"):
        import_model("db2", DATA)


# --- SQL, отчёт, загрузка ---

def test_generate_sql(ora):
    package = generate(ora, RULES)
    sql = package.files()["050_adso.sql"]
    assert re.search(r"CREATE TABLE IF NOT EXISTS dbt_dwh\.sales_orders_active \(\n    order_id\s+bigint NOT NULL,",
                     sql)
    assert "amount       numeric(15,2)" in sql and "note         text" in sql
    assert "total_weight numeric," in sql
    assert "PRIMARY KEY (order_id, line_no)" in sql
    assert "updated_at timestamptz" in sql and "checksum   bytea" in sql
    assert "'Oracle: таблица SALES.ORDERS, активные данные. Заказы клиентов'" in sql
    assert "dbt_stg.sales_log_events_inbound" in sql and "sales_log_events_active" not in sql
    assert "dbt_dwh.tr_sales_orders__sales_orders" in package.files()["070_transformations.sql"]
    assert not package.stubs


def test_generate_mssql_quotes_reserved(ms):
    sql = generate(ms, RULES).files()["040_datasources.sql"]
    assert "dbt_stg.dbo_order" in sql and '"comment"' not in sql


def test_assessment_labels(ora):
    report = assess(ora)
    by_name = {a.name: a for a in report.objects}
    assert by_name["SALES_ORDERS"].type == "Таблица" and by_name["SALES_LOG_EVENTS"].type == "Таблица без ключа"
    assert by_name["SALES_ORDERS/ORACLE"].type == "Входная таблица" and by_name["SALES_ORDERS/ORACLE"].level == 1
    assert by_name["SALES.PKG_LOAD"].level == 3 and by_name["SALES.PKG_LOAD"].hours == 4 + 2 * 4
    assert by_name["SALES.V_SALES_BY_REGION"].level == 2 and by_name["SALES.V_SALES_BY_REGION"].hours == 3
    assert "SALES_ORDERS/ORACLE" in report.flat_files


def test_load_target_and_new_type_checks(ora):
    target = resolve_target(ora, RULES, "SALES_ORDER_LINES", [])
    assert target.kind == "datasource"  # не ADSO с тем же именем
    cols = {c.name: c for c in target.table.columns}
    assert [p for _, p in check_sql(cols["updated_at"], "v")] == ["не дата и время ГГГГ-ММ-ДД ЧЧ:ММ:СС"]
    assert [p for _, p in check_sql(cols["checksum"], "v")] == ["не шестнадцатеричная строка"]
    assert convert_sql(cols["updated_at"], "v", RULES) == "nullif(btrim(v), '')::timestamptz"
    assert "decode(" in convert_sql(cols["checksum"], "v", RULES)


def test_cli_import_oracle(tmp_path):
    shutil.copytree(DATA / "oracle_dict", tmp_path / "dict")
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["import", str(tmp_path / "dict"), "--source", "oracle", "-p", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Oracle: таблиц 4 (без ключа 1)" in result.output
    assert Model.load(tmp_path / "model.json").source_kind == SourceKind.ORACLE
    result = runner.invoke(app, ["report", "-p", str(tmp_path)])
    assert result.exit_code == 0 and (tmp_path / "out" / "complexity.xlsx").is_file()
    result = runner.invoke(app, ["tables", "--source", "mssql"])
    assert result.exit_code == 0 and "KEY_COLUMN_USAGE" in result.output and "sys.sql_modules" in result.output
