"""Генерация SQL (ФП-5): эталонные файлы tests/golden/sql, детерминизм, правила ПИ-5, заготовки.

Эталонные файлы обновляются так: UPDATE_GOLDEN=1 uv run pytest tests/unit/test_generator.py
После обновления просмотрите разницу в git diff: каждое изменение SQL должно быть намеренным.
"""

import os
import re
import shutil

import pytest
from conftest import DEMO, ROOT

from dbtransfer.generator import RulesError, TypeRules, generate
from dbtransfer.importers.bw_csv import import_bw_export
from dbtransfer.model import AdsoKind, ObjectRef, ObjectType, RuleKind
from dbtransfer.project import open_project

GOLDEN = ROOT / "tests" / "golden" / "sql"


@pytest.fixture
def model():
    m = import_bw_export(DEMO / "bw_export")
    config = open_project(DEMO).config
    m.name, m.bw_version, m.schema_prefix = config.name, config.bw_version, config.schema_prefix
    return m


def tables(sql: str) -> dict[str, str]:
    """CREATE TABLE по именам таблиц."""
    return {m.group(1): m.group(0) for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\S+) \(.*?;", sql, re.S)}


def tr_sql(package, source: str, target: str) -> str:
    return next(o for o in package.objects if o.group == "transformations"
                and o.bw_object == f"Трансформация {source} → {target}").sql


def test_golden_files(model):
    files = generate(model).files()
    if os.environ.get("UPDATE_GOLDEN"):
        shutil.rmtree(GOLDEN, ignore_errors=True)
        GOLDEN.mkdir(parents=True)
        for name, text in files.items():
            (GOLDEN / name).write_bytes(text.encode("utf-8"))
    assert sorted(files) == sorted(p.name for p in GOLDEN.iterdir())
    for name, text in files.items():
        assert text == (GOLDEN / name).read_bytes().decode("utf-8"), f"{name} отличается от эталона"


def test_deterministic(model, tmp_path):
    """ПИ-5: два запуска дают побайтно одинаковые файлы."""
    first = generate(model).write(tmp_path / "a")
    second = generate(model).write(tmp_path / "b")
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]
    assert [p.name for p in first][:2] == ["010_schemas.sql", "020_meta.sql"]


def test_pi5_storage_rules(model):
    """ПИ-5: нет PK на AO-таблицах, ключ распределения входит в PK, NUMC стал varchar."""
    sql = "\n".join(o.sql for o in generate(model).objects if o.kind == "table")
    ddl = tables(sql)
    assert len(ddl) == 22
    for name, text in ddl.items():
        if "appendonly=true" in text:
            assert "PRIMARY KEY" not in text, name
        pk = re.search(r"PRIMARY KEY \(([^)]*)\)", text)
        dist = re.search(r"DISTRIBUTED BY \(([^)]*)\)", text)
        if pk and dist:
            assert set(dist.group(1).split(", ")) <= set(pk.group(1).split(", ")), name
    assert "s_ord_item varchar(6) NOT NULL" in ddl["dbt_dwh.zsd_a01_active"]
    assert "calmonth   varchar(6) NOT NULL" in ddl["dbt_mart.zsd_a02_active"]


def test_all_adso_kinds(model):
    """ПИ-7 и ТЗ 4.3.4: состав таблиц по виду ADSO."""
    names = set(tables("\n".join(o.sql for o in generate(model).objects)))
    assert {"dbt_stg.zsd_s01_inbound"} == {n for n in names if "zsd_s01" in n}
    assert {"dbt_stg.zsd_a01_inbound", "dbt_dwh.zsd_a01_active", "dbt_dwh.zsd_a01_changelog"} == \
        {n for n in names if "zsd_a01" in n}
    assert {"dbt_stg.zsd_a02_inbound", "dbt_mart.zsd_a02_active"} == {n for n in names if "zsd_a02" in n}
    assert {"dbt_mart.zsd_d01_active"} == {n for n in names if "zsd_d01" in n}
    assert "DISTRIBUTED RANDOMLY" in tables(generate(model).files()["050_adso.sql"])["dbt_stg.zsd_s01_inbound"]


def test_postgres_dialect(model):
    text = generate(model, dialect="postgres").files()["deploy_all.sql"]
    assert "DISTRIBUTED" not in text and "appendonly" not in text
    assert "диалект postgres" in text
    with pytest.raises(ValueError, match="Диалект"):
        generate(model, dialect="oracle")


def test_scope_limits_package(model):
    package = generate(model, scope="ZSD_A02")
    names = [o.name for o in package.objects]
    assert "dbt_dwh.zsd_a01_active" in names and "dbt_mart.zsd_a02_active" in names
    assert not any("zsd_a03" in n or "zsd_s01" in n for n in names)
    assert "dbt_md.country_text" in names  # тексты атрибута признака потока
    assert not package.stubs


def test_stub_for_end_routine(model):
    package = generate(model)
    (stub,) = package.stubs
    assert stub.ref == "2D8B5E3C1F4A6B7C0D9E8F7A6B5C4D32" and stub.stub == ["конечная рутина ABAP"]
    assert "RAISE EXCEPTION 'Трансформация ZSD_A01 → ZSD_A03 требует ручной доработки: конечная рутина ABAP'" \
        in stub.sql
    assert "-- METHOD end_routine." in stub.sql and "-- INSERT INTO dbt_stg.zsd_a03_inbound" in stub.sql
    assert package.function_for("ZSD_A01", "activation").name == "dbt_dwh.activate_zsd_a01(bigint)"


def test_unsupported_rules_make_stubs(model):
    tr = next(t for t in model.transformations.values() if t.target.name == "ZSD_A01")
    rules = {r.target_fields[0]: r for r in tr.rules}
    rules["0NETVAL_INV"].param = "LOG(NETVAL) % 2"
    rules["0SOLD_TO"].kind = RuleKind.ROUTINE
    rules["0MATERIAL"].kind = RuleKind.UNKNOWN
    rules["0MATERIAL"].raw_kind = "FORMULA_OLD"
    package = generate(model)
    stub = next(o for o in package.stubs if o.ref == tr.id)
    assert stub.stub == ["0SOLD_TO: рутина поля ABAP", "0MATERIAL: правило FORMULA_OLD не поддерживается",
                         "0NETVAL_INV: формула «LOG(NETVAL) % 2» не переводится: непонятный символ «%» в позиции 13"]
    assert "%%" in stub.sql  # знак процента экранирован для RAISE


def test_time_constant_and_filter(model):
    tr = next(t for t in model.transformations.values() if t.target.name == "ZSD_A02")
    dtp = next(d for d in model.dtps.values() if d.target.name == "ZSD_A02")
    dtp.filter = "0DOC_CURRCY = 'RUB'"
    tr.rules.append(tr.rules[0].model_copy(update={"kind": RuleKind.CONSTANT, "param": "X",
                                                    "target_fields": ["0SALES_UNIT"]}))
    sql = tr_sql(generate(model), "ZSD_A01", "ZSD_A02")
    assert "coalesce(to_char(s.calday, 'YYYYMM'), '000000'),  -- 0CALMONTH" in sql
    assert "AND (s.doc_currcy = 'RUB')" in sql
    assert "Поле 0SALES_UNIT задано несколькими правилами" in sql


def test_unsupported_targets_and_sources(model):
    tr = next(t for t in model.transformations.values() if t.target.name == "ZSD_A02")
    tr.target = ObjectRef(type=ObjectType.ADSO, name="ZSD_D01")
    other = next(t for t in model.transformations.values() if t.target.name == "ZSD_A04")
    other.source = ObjectRef(type=ObjectType.INFOOBJECT, name="0MATERIAL")
    reasons = [r for o in generate(model).stubs for r in o.stub]
    assert "ADSO ZSD_D01 вида direct update не загружается трансформацией" in reasons
    assert "источник IOBJ 0MATERIAL не поддерживается, только ADSO и DataSource" in reasons


def test_full_load_from_datasource_and_staging(model):
    for dtp in model.dtps.values():
        dtp.mode = "FULL"
    model.adsos["ZSD_A01"].kind = AdsoKind.STAGING
    package = generate(model)
    sql = tr_sql(package, "ZSALES_FF", "ZSD_A01")
    assert "row_number() OVER (ORDER BY s.load_id, s.record_no)" in sql and "WHERE" not in sql.split("FROM")[-1]
    assert "FROM dbt_stg.zsd_a01_inbound s" in tr_sql(package, "ZSD_A01", "ZSD_A02")
    assert "dbt_stg.zsd_a01_inbound" in tr_sql(package, "ZSD_A01", "ZSD_A02")


def test_project_type_rules(model, tmp_path):
    (tmp_path / "type_rules.yaml").write_text(
        "storage_options: {ao_column: 'appendonly=true, orientation=column, compresstype=zlib, compresslevel=5'}\n"
        "overrides:\n  ZSD_A01: {distributed_by: [0DOC_NUMBER, s_ord_item], storage: {change_log: heap}}\n",
        encoding="utf-8")
    ddl = tables(generate(model, TypeRules.load(tmp_path)).files()["050_adso.sql"])
    assert "DISTRIBUTED BY (doc_number, s_ord_item)" in ddl["dbt_dwh.zsd_a01_active"]
    assert "WITH" not in ddl["dbt_dwh.zsd_a01_changelog"]
    assert "compresstype=zlib" in ddl["dbt_stg.zsd_a02_inbound"]


@pytest.mark.parametrize(("text", "message"), [
    ("overrides: {ZSD_A01: {distributed_by: [0SOLD_TO]}}", "должен входить в PRIMARY KEY"),
    ("overrides: {ZSD_A01: {distributed_by: [0PRICE]}}", "поля 0PRICE нет"),
    ("overrides: {ZSD_A01: {storage: {active: ao_column}}}", "не может быть PRIMARY KEY"),
    ("overrides: {ZSD_A01: {storage: {active: columnar}}}", "хранение «columnar» неизвестно"),
    ("storage: {active: columnar}", "хранение «columnar» для active неизвестно"),
    ("types: [", "файл не читается"),
])
def test_bad_type_rules(model, tmp_path, text, message):
    (tmp_path / "type_rules.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(RulesError, match=message):
        generate(model, TypeRules.load(tmp_path))


def test_unknown_type_warning(model):
    model.adsos["ZSD_S01"].fields[0].datatype = "RAW"
    package = generate(model)
    assert package.warnings == ["Тип RAW нет в type_rules.yaml, взят text: ZSD_S01.0DOC_NUMBER"]
