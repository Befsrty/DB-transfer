"""Переводчик SQL (dbtransfer.translate) без базы: конструкции, набор представлений и процедур, пакет, отчёт."""

import pytest
from typer.testing import CliRunner

from dbtransfer.analysis.assessment import assess
from dbtransfer.cli import app
from dbtransfer.generator import TypeRules, generate
from dbtransfer.importers.oracle_dict import import_oracle_dict
from dbtransfer.model import Model, ObjectType, OtherObject, SourceKind
from dbtransfer.translate import NameMap, Relation, translate_model, translate_query
from dbtransfer.translate.routines import translate_routines
from tests import sql_corpus as corpus


@pytest.fixture
def names():
    n = NameMap()
    n.add("SALES", "ORDERS", Relation("SALES.ORDERS", "dbt_sales.orders",
                                      columns={"order_id": "integer", "order_date": "date", "amount": "number",
                                               "qty": "integer", "status": "string"}))
    n.add("SALES", "EMP", Relation("SALES.EMP", "dbt_sales.emp",
                                   columns={"emp_id": "integer", "manager_id": "integer", "name": "string"}))
    return n


def tr(sql, source, names, dialect="greenplum7"):
    return translate_query(sql, source, dialect, names, "SALES")


@pytest.mark.parametrize(("source", "sql", "expect"), [
    ("oracle", "SELECT NVL(STATUS, 'x') FROM ORDERS", "COALESCE"),
    ("oracle", "SELECT ORDER_DATE + 1 FROM ORDERS", "order_date + 1"),  # date + 1 в PostgreSQL — тоже следующий день
    ("oracle", "SELECT * FROM ORDERS WHERE ROWNUM <= 5", "LIMIT 5"),
    ("oracle", "SELECT SYSDATE FROM DUAL", "LOCALTIMESTAMP"),
    ("oracle", "SELECT QTY / 2 FROM ORDERS", "CAST(qty AS DECIMAL)"),
    ("oracle", "SELECT EMP_ID, LEVEL FROM EMP START WITH MANAGER_ID IS NULL CONNECT BY PRIOR EMP_ID = MANAGER_ID",
     "WITH RECURSIVE"),
    ("tsql", "SELECT TOP 3 OrderId FROM sales.Orders ORDER BY Amount DESC", "LIMIT 3"),
    ("tsql", "SELECT Status + 'x' FROM sales.Orders", "||"),
    ("tsql", "SELECT ISNULL(Status, 'x'), GETDATE() FROM sales.Orders", "COALESCE"),
    ("teradata", "SEL ORDER_ID FROM SALES.ORDERS QUALIFY ROW_NUMBER() OVER (ORDER BY AMOUNT) = 1", "ROW_NUMBER"),
    ("teradata", "SEL ZEROIFNULL(AMOUNT) FROM SALES.ORDERS", "COALESCE"),
])
def test_constructs(names, source, sql, expect):
    r = tr(sql, source, names)
    assert r.status == "auto", (r.todos, r.error, r.sql)
    assert expect in r.sql
    assert "dbt_sales.orders" in r.sql or "dbt_sales.emp" in r.sql or "FROM" not in r.sql.upper()


def test_qualify_has_no_qualify_in_output(names):
    r = tr("SEL ORDER_ID FROM SALES.ORDERS QUALIFY ROW_NUMBER() OVER (ORDER BY AMOUNT) = 1", "teradata", names)
    assert "QUALIFY" not in r.sql.upper()


def test_untranslated_is_marked_not_lost(names):
    r = tr("SELECT REGEXP_LIKE_X(STATUS) FROM ORDERS", "oracle", names)
    assert r.status != "auto" and any("REGEXP_LIKE_X" in t for t in r.todos)
    bad = tr("SELECT FROM WHERE (", "oracle", names)
    assert bad.status == "failed" and bad.error


def test_clickhouse_output(names):
    r = tr("SELECT NVL(STATUS, 'x'), ORDER_DATE + 1 FROM ORDERS", "oracle", names, "clickhouse")
    assert r.status == "auto", r.todos
    assert "dbt_sales.orders" in r.sql


def test_merge_becomes_update_and_insert(names):
    r = tr("MERGE INTO ORDERS t USING (SELECT 1 AS ORDER_ID FROM DUAL) s ON (t.ORDER_ID = s.ORDER_ID) "
           "WHEN MATCHED THEN UPDATE SET QTY = 0 WHEN NOT MATCHED THEN INSERT (ORDER_ID, QTY) VALUES (s.ORDER_ID, 1)",
           "oracle", names)
    assert "MERGE" not in r.sql.upper()
    assert "UPDATE" in r.sql and "NOT EXISTS" in r.sql


@pytest.fixture
def oracle_model(tmp_path):
    corpus.write_oracle_dict(tmp_path)
    model = import_oracle_dict(tmp_path)
    model.schema_prefix = "dbtx"
    return model


def test_oracle_corpus_statuses(oracle_model):
    by_name = {t.name.split(".", 1)[1]: t for t in translate_model(oracle_model, "greenplum7")}
    for name, _, want in [*corpus.ORACLE_VIEWS, *corpus.ORACLE_ROUTINES]:
        assert by_name[name].auto == want, (name, by_name[name].status, by_name[name].todos)
    auto = sum(by_name[n].auto for n, _, _ in corpus.ORACLE_VIEWS)
    assert auto / len(corpus.ORACLE_VIEWS) >= 0.7


def test_routine_drafts(oracle_model):
    by_name = {t.name.split(".", 1)[1]: t for t in translate_model(oracle_model, "greenplum7")}
    proc = by_name["P_REFRESH_SUMMARY"].sql
    assert "CREATE OR REPLACE PROCEDURE dbtx_sales.p_refresh_summary" in proc
    assert "INOUT" in proc and "GET DIAGNOSTICS" in proc and "LANGUAGE plpgsql" in proc
    assert "dbtx_sales.f_order_level(" in proc  # вызов переведённой функции по её новому имени
    bulk = by_name["P_BULK"]
    assert bulk.status != "auto" and "TODO" in bulk.sql
    # ADB 6: процедур нет, вместо них функции RETURNS void
    gp6 = {t.name.split(".", 1)[1]: t for t in translate_model(oracle_model, "greenplum")}
    assert "CREATE OR REPLACE FUNCTION dbtx_sales.p_refresh_summary" in gp6["P_REFRESH_SUMMARY"].sql


def test_package_files(oracle_model):
    package = generate(oracle_model, TypeRules.load(), "greenplum7")
    files = package.files()
    assert "080_views.sql" in files and "drafts_views.sql" in files and "drafts_routines.sql" in files
    deploy_all = files["deploy_all.sql"]
    assert "080_views.sql" in deploy_all and "drafts_" not in deploy_all
    assert "TODO" in files["drafts_routines.sql"]


def test_assessment_translation(oracle_model):
    report = assess(oracle_model, "greenplum7")
    s = report.translation
    assert s is not None and s.views == len(corpus.ORACLE_VIEWS)
    assert s.views_auto == sum(1 for _, _, a in corpus.ORACLE_VIEWS if a)
    assert s.routines == len(corpus.ORACLE_ROUTINES) and 0 < s.statements_share <= 1
    assert 0 < s.hours_left < s.hours_manual


def test_teradata_macro_and_mssql_routines():
    model = Model(source_kind=SourceKind.TERADATA)
    model.others = [OtherObject(type=ObjectType.DB_ROUTINE, name=f"SALES.{n}", text=t, schema_name="SALES")
                    for n, t, _, _ in corpus.TERADATA_ROUTINES]
    for r in translate_routines(model, "greenplum7"):  # таблиц нет в модели: имена остаются, но помечены
        assert r.status != "failed" and "LANGUAGE plpgsql" in r.sql, (r.name, r.todos, r.sql)
    model = Model(source_kind=SourceKind.MSSQL)
    model.others = [OtherObject(type=ObjectType.DB_ROUTINE, name=f"sales.{n}", text=t, schema_name="sales")
                    for n, t, _ in corpus.MSSQL_ROUTINES]
    got = {r.name.split(".", 1)[1]: r for r in translate_routines(model, "greenplum7")}
    # Без таблиц в модели остаются TODO об именах; полный прогон с базой — tests/integration/test_translate_db.py
    assert got["fnOrderLevel"].auto and got["RaiseNew"].status != "failed"
    assert not got["DynSql"].auto and any("динамическ" in t.lower() or "exec" in t.lower() for t in got["DynSql"].todos)
    assert "WHEN OTHERS THEN" in got["RefreshSummary"].sql


def test_cli_translate(tmp_path):
    f = tmp_path / "v.sql"
    f.write_text("SELECT NVL(A, 0) FROM T WHERE ROWNUM <= 3", encoding="utf-8")
    result = CliRunner().invoke(app, ["translate", str(f), "--from", "oracle"])
    assert result.exit_code == 0, result.output
    assert "COALESCE" in result.output and "LIMIT 3" in result.output
    p = tmp_path / "p.sql"
    p.write_text(corpus.MSSQL_ROUTINES[2][1], encoding="utf-8")
    result = CliRunner().invoke(app, ["translate", str(p), "--from", "tsql"])
    assert result.exit_code == 0, result.output
    assert "LANGUAGE plpgsql" in result.output
