import json

import pytest
from conftest import DEMO

from dbtransfer.importers.bw_csv import import_bw_export
from dbtransfer.model import Model
from dbtransfer.project import ProjectError, find_project, init_project, open_project, schema_prefix_for


def test_init_and_scope_keeps_comments(tmp_path):
    project = init_project(tmp_path / "p", name="Тест")
    assert project.config.name == "Тест" and (tmp_path / "p" / "bw_export").is_dir()
    assert (tmp_path / "p" / "data").is_dir() and project.config.schema_prefix == "p"
    project.set_scope("ZSD_A02")
    text = project.config_path.read_text(encoding="utf-8")
    assert "scope_from: ZSD_A02" in text and "# Проект DB-transfer" in text
    assert open_project(tmp_path / "p").config.scope_from == "ZSD_A02"
    with pytest.raises(ProjectError):
        init_project(tmp_path / "p")


@pytest.mark.parametrize(("folder", "prefix"), [("my-bw", "my_bw"), ("Проект", "proj"), ("dbt", "proj"),
                                                ("2026 SAP", "p_2026_sap"), ("a" * 30, "a" * 20)])
def test_schema_prefix_for(folder, prefix):
    assert schema_prefix_for(folder) == prefix


def test_find_project_option_and_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("DBT_PROJECT", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ProjectError):
        find_project()
    assert find_project(DEMO).config.scope_from == "ZSD_A02"


def test_model_roundtrip_and_version(tmp_path):
    project = init_project(tmp_path / "p")
    model = import_bw_export(DEMO / "bw_export")
    project.save_model(model)
    loaded = project.load_model()
    assert loaded.content_hash() == model.content_hash()
    raw = json.loads(project.model_path.read_text(encoding="utf-8"))
    raw["schema_version"] = 999
    project.model_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ProjectError, match="версии 999"):
        project.load_model()


def test_hash_ignores_import_time():
    a, b = import_bw_export(DEMO / "bw_export"), Model.model_validate(import_bw_export(DEMO / "bw_export"))
    b.imported_at = None
    assert a.content_hash() == b.content_hash()
