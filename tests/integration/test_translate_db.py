"""Переводчик SQL на живых базах: представления и процедуры Oracle и MS SQL Server переводятся, разворачиваются
в Greengage (ADB 7.5 или 6.x) и дают те же строки, что и в источнике. Teradata — синтетика: результат
сравнивается с эталонным запросом PostgreSQL на той же целевой базе.

Нужны цель (DBT_DB_*, как у остальных тестов -m db) и источники:
  DBT_ORACLE_URL=oracle://sales:student@localhost:1521/FREEPDB1
  DBT_MSSQL_URL=mssql://sa:Student_123@localhost:1433/master
Без адреса источника его тест пропускается. Запуск: uv run pytest -m db tests/integration/test_translate_db.py
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from dbtransfer import naming
from dbtransfer.config import load_db_settings
from dbtransfer.db import DbError, connect, server_dialect
from dbtransfer.deploy import deploy
from dbtransfer.generator import TypeRules, generate
from dbtransfer.generator.dialects import get_dialect
from dbtransfer.model import Model, ObjectType, OtherObject, SourceKind
from dbtransfer.translate import NameMap, Relation, translate_query
from dbtransfer.translate.routines import translate_routines
from dbtransfer.translate.views import split_view
from tests import sql_corpus as corpus

pytestmark = pytest.mark.db

ROOT = Path(__file__).parents[2]
MIN_AUTO_SHARE = 0.7


def norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float, Decimal)):
        d = round(Decimal(str(v)), 6)
        return d.normalize() if d != 0 else Decimal(0)
    if isinstance(v, datetime):
        return v.date() if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) else v.replace(tzinfo=None)
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        return v.rstrip()
    return v


def rows_of(cur, sql: str) -> list[tuple]:
    cur.execute(sql)
    rows = [tuple(norm(v) for v in r) for r in cur.fetchall()]
    return sorted(rows, key=lambda r: tuple((v is None, str(v)) for v in r))


@pytest.fixture
def target():
    try:
        conn = connect(load_db_settings(ROOT / ".env"), timeout=5)
    except DbError as e:
        pytest.skip(str(e))
    yield conn
    conn.rollback()
    with conn.cursor() as cur:
        for s in ("dbtx_sales", "dbtx_meta", "dbtx_stg", "dbtx_md", "dbtx_dwh", "dbtx_mart", "dbtt_sales"):
            cur.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
    conn.commit()
    conn.close()


def load_target(conn, model: Model, columns: dict[str, list[str]]) -> None:
    with conn.cursor() as cur:
        for t in model.tables.values():
            key = t.name.upper()
            if key not in corpus.DATA:
                continue
            cols = ", ".join(naming.db_name(c) for c in columns[key])
            marks = ", ".join(["%s"] * len(columns[key]))
            cur.executemany(f"INSERT INTO {naming.data_table(model, t)} ({cols}) VALUES ({marks})",
                            corpus.DATA[key])
    conn.commit()


def target_call(conn, sql: str, target: str, args: tuple) -> None:
    """Вызов переведённой процедуры: CALL в ADB 7 (INOUT-параметры — NULL), функция в ADB 6."""
    head = sql.split("$body$", 1)[0]
    inout = head.count("INOUT ")
    values = ", ".join([*(str(a) for a in args), *(["NULL"] * inout)])
    verb = "CALL" if "CREATE OR REPLACE PROCEDURE" in head else "SELECT"
    with conn.cursor() as cur:
        cur.execute(f"{verb} {target}({values if verb == 'CALL' else ', '.join(str(a) for a in args)})")
    conn.commit()


def check_objects(model: Model, target, source_cur, source_name, views: dict[str, bool],
                  routines: dict[str, bool], columns: dict[str, list[str]], run_source_call, calls, functions):
    """Пакет -> цель, данные, сравнение представлений; затем процедуры: создать, вызвать в обеих базах,
    сравнить таблицы. Общий для Oracle и MS SQL."""
    model.schema_prefix = "dbtx"
    package = generate(model, TypeRules.load(), server_dialect(target))
    result = deploy(target, package, recreate=True)
    assert result.ok, result.error
    load_target(target, model, columns)
    view_errors = {i.obj.name: i.message for i in result.view_errors}
    by_name = {t.name.split(".", 1)[1].upper(): t for t in package.translations}
    report, problems = [], []
    with target.cursor() as cur:
        for name, want_auto in views.items():
            t = by_name.get(name.upper())
            if t is None:
                problems.append(f"{name}: представления нет в модели")
                continue
            if t.auto != want_auto:
                problems.append(f"{name}: ожидалось {'auto' if want_auto else 'не auto'}, а {t.status}: {t.todos}")
            if not t.auto:
                report.append(f"{name}: {t.status}, TODO: {'; '.join(t.todos)}")
                continue
            if t.target in view_errors:
                problems.append(f"{name}: база не приняла перевод: {view_errors[t.target]}\n{t.sql}")
                continue
            got = rows_of(cur, f"SELECT * FROM {t.target}")
            want = rows_of(source_cur, f"SELECT * FROM {source_name(name)}")
            if got != want:
                problems.append(f"{name}: строки расходятся\n{t.sql}\nисточник: {want[:5]}\nцель:     {got[:5]}")
            else:
                report.append(f"{name}: совпало, строк {len(got)}")
    target.commit()
    # Процедуры: переведённые без TODO создаются в цели
    created = {}
    for name, want_auto in routines.items():
        t = by_name.get(name.upper())
        if t is None:
            problems.append(f"{name}: процедуры нет в модели")
            continue
        if t.auto != want_auto:
            problems.append(f"{name}: ожидалось {'без TODO' if want_auto else 'с TODO'}, а {t.status}: {t.todos}"
                            f"\n{t.sql}")
            continue
        report.append(f"{name}: {t.status}, операторов {t.statements}, без TODO {t.auto_statements}")
        if not t.auto:
            continue
        try:
            with target.cursor() as cur:
                cur.execute(t.sql)
            target.commit()
            created[name.upper()] = t
        except Exception as e:  # noqa: BLE001
            target.rollback()
            problems.append(f"{name}: база не приняла черновик: {e}\n{t.sql}")
    with target.cursor() as cur:
        for name, source_sql, target_sql in functions:
            t = created.get(name.upper())
            if t is None:
                continue
            got = rows_of(cur, target_sql.format(f=t.target, s="dbtx_sales"))
            want = rows_of(source_cur, source_sql)
            if got != want:
                problems.append(f"{name}: результаты функции расходятся\nисточник: {want[:5]}\nцель:     {got[:5]}")
    target.commit()
    for name, source_sql, args, table in calls:
        t = created.get(name.upper())
        if t is None:
            continue
        run_source_call(source_sql)
        try:
            target_call(target, t.sql, t.target, args)
        except Exception as e:  # noqa: BLE001
            target.rollback()
            problems.append(f"{name}: вызов в цели упал: {e}\n{t.sql}")
            continue
        rel = model.find_table(table)
        with target.cursor() as cur:
            got = rows_of(cur, f"SELECT * FROM {naming.data_table(model, rel)}")
        want = rows_of(source_cur, f"SELECT * FROM {source_name(rel.name)}")
        if got != want:
            problems.append(f"{name}: после вызова таблица {table} расходится\n{t.sql}\nисточник: {want[:6]}\n"
                            f"цель:     {got[:6]}")
        else:
            report.append(f"{name}: после вызова {table} совпала, строк {len(got)}")
        target.commit()
    total = len(views)
    auto = sum(1 for n in views if by_name.get(n.upper()) is not None and by_name[n.upper()].auto)
    print(f"\nпредставлений автоматически: {auto} из {total} ({auto / total:.0%})\n" + "\n".join(report))
    assert not problems, "\n\n".join(problems)
    assert auto / total >= MIN_AUTO_SHARE


def test_mssql(target):
    url = os.environ.get("DBT_MSSQL_URL")
    if not url:
        pytest.skip("DBT_MSSQL_URL не задан")
    from dbtransfer.importers.mssql_dict import connect as ms_connect
    from dbtransfer.importers.mssql_dict import import_mssql_dict

    admin = ms_connect(url)
    admin.autocommit(True)
    with admin.cursor() as cur:
        cur.execute("IF DB_ID('dbtx') IS NULL CREATE DATABASE dbtx")
    admin.close()
    src = ms_connect(url.rsplit("/", 1)[0] + "/dbtx")
    src.autocommit(True)
    cur = src.cursor()
    for name, _, _ in corpus.MSSQL_VIEWS:
        cur.execute(f"IF OBJECT_ID('sales.{name}', 'V') IS NOT NULL DROP VIEW sales.{name}")
    for name, text, _ in corpus.MSSQL_ROUTINES:
        kind = "FUNCTION" if "CREATE FUNCTION" in text else "PROCEDURE"
        cur.execute(f"IF OBJECT_ID('sales.{name}') IS NOT NULL DROP {kind} sales.{name}")
    for t in ("Orders", "Customers", "Employees", "CustSummary"):
        cur.execute(f"IF OBJECT_ID('sales.{t}', 'U') IS NOT NULL DROP TABLE sales.{t}")
    cur.execute("IF SCHEMA_ID('sales') IS NULL EXEC('CREATE SCHEMA sales')")
    for ddl in [*corpus.MSSQL_TABLES, corpus.MSSQL_SUMMARY]:
        cur.execute(ddl)
    for key, rows in corpus.DATA.items():
        cols = corpus.MSSQL_COLUMNS[key]
        cur.executemany(f"INSERT INTO sales.{key.capitalize()} ({', '.join(cols)}) "
                        f"VALUES ({', '.join(['%s'] * len(cols))})", rows)
    for _, text, _ in [*corpus.MSSQL_VIEWS, *corpus.MSSQL_ROUTINES]:
        cur.execute(text)
    model = import_mssql_dict(conn=src, schemas=["sales"])
    check_objects(model, target, cur, lambda name: f"sales.{name}", {n: a for n, _, a in corpus.MSSQL_VIEWS},
                  {n: a for n, _, a in corpus.MSSQL_ROUTINES}, corpus.MSSQL_COLUMNS, cur.execute,
                  corpus.MSSQL_CALLS, corpus.MSSQL_FUNCTION_CHECKS)
    src.close()


def test_oracle(target):
    url = os.environ.get("DBT_ORACLE_URL")
    if not url:
        pytest.skip("DBT_ORACLE_URL не задан")
    from dbtransfer.importers.oracle_dict import connect as ora_connect
    from dbtransfer.importers.oracle_dict import import_oracle_dict

    src = ora_connect(url)
    cur = src.cursor()
    cur.execute("SELECT USER FROM DUAL")
    owner = cur.fetchone()[0]
    for name, _, _ in corpus.ORACLE_VIEWS:
        cur.execute(f"BEGIN EXECUTE IMMEDIATE 'DROP VIEW {name}'; EXCEPTION WHEN OTHERS THEN NULL; END;")
    for t in ("ORDERS", "CUSTOMERS", "EMPLOYEES", "CUST_SUMMARY"):
        cur.execute(f"BEGIN EXECUTE IMMEDIATE 'DROP TABLE {t} PURGE'; EXCEPTION WHEN OTHERS THEN NULL; END;")
    for ddl in [*corpus.ORACLE_TABLES, corpus.ORACLE_SUMMARY]:
        cur.execute(ddl)
    for key, rows in corpus.DATA.items():
        cols = corpus.COLUMNS[key]
        marks = ", ".join(f":{i + 1}" for i in range(len(cols)))
        cur.executemany(f"INSERT INTO {key} ({', '.join(cols)}) VALUES ({marks})", rows)
    src.commit()
    for name, text, _ in corpus.ORACLE_VIEWS:
        cur.execute(f"CREATE OR REPLACE VIEW {name} AS {text}")
    for _, text, _ in corpus.ORACLE_ROUTINES:
        cur.execute(text)
    model = import_oracle_dict(conn=src, schemas=[owner])

    def call(sql: str) -> None:
        if ":out" in sql:
            cur.execute(sql, out=cur.var(int))
        else:
            cur.execute(sql)
        src.commit()

    check_objects(model, target, cur, lambda name: name, {n: a for n, _, a in corpus.ORACLE_VIEWS},
                  {n: a for n, _, a in corpus.ORACLE_ROUTINES}, corpus.COLUMNS, call, corpus.ORACLE_CALLS,
                  corpus.ORACLE_FUNCTION_CHECKS)
    src.close()


def _teradata_names(schema: str) -> NameMap:
    names = NameMap()
    cats = {"integer": "integer", "varchar(50)": "string", "varchar(20)": "string", "varchar(10)": "string",
            "varchar(1)": "string", "date": "date", "timestamp(0)": "timestamp", "numeric(12,2)": "number",
            "numeric(10,2)": "number", "numeric(14,2)": "number"}
    for key, cols in {**corpus.TERADATA_COLUMNS, "CUST_SUMMARY": corpus.TERADATA_SUMMARY}.items():
        columns = {c.split()[0]: cats[c.split(None, 1)[1]] for c in cols.split(", ")}
        names.add("SALES", key, Relation(f"SALES.{key}", f"{schema}.{key.lower()}", columns=columns))
    return names


def _teradata_tables(target, schema: str) -> None:
    with target.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        cur.execute(f"CREATE SCHEMA {schema}")
        for key, cols in corpus.TERADATA_COLUMNS.items():
            cur.execute(f"CREATE TABLE {schema}.{key.lower()} ({cols})")
            marks = ", ".join(["%s"] * len(cols.split(", ")))
            cur.executemany(f"INSERT INTO {schema}.{key.lower()} VALUES ({marks})", corpus.DATA[key])
        cur.execute(f"CREATE TABLE {schema}.cust_summary ({corpus.TERADATA_SUMMARY})")
    target.commit()


def test_teradata_views_against_reference(target):
    """Teradata: перевод на целевой базе против эталонного запроса PostgreSQL на тех же данных."""
    schema = "dbtt_sales"
    _teradata_tables(target, schema)
    names = _teradata_names(schema)
    dialect = server_dialect(target)
    problems, auto = [], 0
    with target.cursor() as cur:
        for name, text, reference in corpus.TERADATA_VIEWS:
            query, _ = split_view(text)
            r = translate_query(query, "teradata", dialect, names, "SALES")
            if reference is None:
                if r.status == "auto":
                    problems.append(f"{name}: ожидался черновик, а перевод auto:\n{r.sql}")
                continue
            if r.status != "auto":
                problems.append(f"{name}: {r.status}: {r.todos or r.error}")
                continue
            auto += 1
            try:
                got = rows_of(cur, r.sql)
            except Exception as e:  # noqa: BLE001
                target.rollback()
                problems.append(f"{name}: база не приняла перевод: {e}\n{r.sql}")
                continue
            want = rows_of(cur, reference.format(s=schema))
            if got != want:
                problems.append(f"{name}: строки расходятся\n{r.sql}\nэталон: {want[:5]}\nцель:   {got[:5]}")
    assert not problems, "\n\n".join(problems)
    assert auto / len(corpus.TERADATA_VIEWS) >= MIN_AUTO_SHARE


def test_teradata_routines(target):
    """Макрос и процедура SPL Teradata: черновик создаётся в цели и выполняется; итоги — как у эталона."""
    schema = "dbtt_sales"
    _teradata_tables(target, schema)
    names = _teradata_names(schema)
    dialect = server_dialect(target)
    model = Model(source_kind=SourceKind.TERADATA, schema_prefix="dbtt")
    model.others = [OtherObject(type=ObjectType.DB_ROUTINE, name=f"SALES.{n}", text=text, schema_name="SALES")
                    for n, text, _, _ in corpus.TERADATA_ROUTINES]
    by_name = {t.name.split(".", 1)[1]: t for t in translate_routines(model, dialect, names)}
    procedures = get_dialect(dialect).procedures
    with target.cursor() as cur:
        for name, _, want_auto, reference in corpus.TERADATA_ROUTINES:
            t = by_name[name]
            assert t.auto == want_auto, f"{name}: {t.status} {t.todos}\n{t.sql}"
            cur.execute(t.sql)
            if name == "mRefresh":
                cur.execute(f"SELECT {t.target}(50, 'UNK')")
                got = rows_of(cur, f"SELECT customer_id, total, cnt FROM {schema}.cust_summary")
                assert got == rows_of(cur, reference.format(s=schema))
                assert rows_of(cur, f"SELECT count(*) FROM {schema}.orders WHERE status IS NULL") == [(Decimal(0),)]
            else:
                cur.execute(f"CALL {t.target}(1000, NULL)" if procedures else f"SELECT {t.target}(1000)")
                got = cur.fetchone()[0]
                want = rows_of(cur, f"SELECT count(*) FROM (SELECT customer_id FROM {schema}.orders GROUP BY 1 "
                                    "HAVING sum(amount) >= 1000) x")[0][0]
                assert norm(got) == want
    target.commit()
