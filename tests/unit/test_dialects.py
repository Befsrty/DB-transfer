"""Диалекты greenplum7 (ADB 7.5 / Greengage 7), greenplum (6.x) и postgres: различия DDL и выбор по серверу."""

import pytest
from test_generator import model, tables  # noqa: F401  (фикстура модели демо-примера)

from dbtransfer.generator import DIALECT_SPECS, TypeRules, dialect_for_server, generate


def test_greenplum7_storage_uses_access_method(model):  # noqa: F811
    ddl = tables(generate(model, dialect="greenplum7").files()["050_adso.sql"])
    inbound = ddl["dbt_stg.zsd_a02_inbound"]
    assert "USING ao_column\nWITH (compresstype=zstd, compresslevel=1)\nDISTRIBUTED" in inbound
    assert "appendonly" not in inbound
    assert "USING" not in ddl["dbt_dwh.zsd_a01_active"]  # heap: без метода доступа
    assert "DISTRIBUTED BY (doc_number)" in ddl["dbt_dwh.zsd_a01_active"]


def test_greenplum6_package_unchanged_form(model):  # noqa: F811
    text = generate(model, dialect="greenplum").files()["deploy_all.sql"]
    assert "WITH (appendonly=true, orientation=column" in text
    assert "USING ao_column" not in text and "CREATE SEQUENCE IF NOT EXISTS dbt_" not in text


def test_greenplum7_sequences(model):  # noqa: F811
    meta = generate(model, dialect="greenplum7").files()["020_meta.sql"]
    assert "CREATE SEQUENCE IF NOT EXISTS dbt_meta.load_id_seq;" in meta
    assert "DO $$" not in meta


def test_primary_key_on_ao_allowed_only_in_7(model, tmp_path):  # noqa: F811
    (tmp_path / "type_rules.yaml").write_text("overrides: {ZSD_A01: {storage: {active: ao_column}}}", encoding="utf-8")
    rules = TypeRules.load(tmp_path)
    active = tables(generate(model, rules, "greenplum7").files()["050_adso.sql"])["dbt_dwh.zsd_a01_active"]
    assert "PRIMARY KEY" in active and "USING ao_column" in active


@pytest.mark.parametrize(("options", "clause"), [
    ("appendonly=true, orientation=column, compresstype=zstd", "USING ao_column\nWITH (compresstype=zstd)"),
    ("appendoptimized=true, compresstype=zlib", "USING ao_row\nWITH (compresstype=zlib)"),
    ("appendonly=true, orientation=column", "USING ao_column"),
    ("fillfactor=70", "WITH (fillfactor=70)"),
    ("", ""),
])
def test_storage_clause(options, clause):
    assert DIALECT_SPECS["greenplum7"].storage_clause(options) == clause
    assert DIALECT_SPECS["greenplum"].storage_clause(options) == (f"WITH ({options})" if options else "")


@pytest.mark.parametrize(("version", "num", "dialect"), [
    ("PostgreSQL 12.22 (Greengage Database 7.5.1 build ...) on x86_64-pc-linux-gnu", 120022, "greenplum7"),
    ("PostgreSQL 9.4.26 (Greengage Database 6.32.0 build ...) on x86_64-pc-linux-gnu", 90426, "greenplum"),
    ("PostgreSQL 9.4.26 (Greenplum Database 6.29.0 build ...)", 90426, "greenplum"),
    ("PostgreSQL 12.12 (Greenplum Database 7.1.0 build ...)", 120012, "greenplum7"),
    ("PostgreSQL 16.4 on x86_64-pc-linux-gnu", 160004, "postgres"),
])
def test_dialect_for_server(version, num, dialect):
    assert dialect_for_server(version, num) == dialect


def test_capabilities_differ_where_versions_diverge():
    gp7, gp6 = DIALECT_SPECS["greenplum7"], DIALECT_SPECS["greenplum"]
    assert gp7.procedures and not gp6.procedures
    assert gp7.declarative_partitions and not gp6.declarative_partitions
    assert not gp7.statement_triggers and not gp6.statement_triggers
    assert gp7.ao_primary_key and not gp6.ao_primary_key
