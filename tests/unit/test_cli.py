from typer.testing import CliRunner

from dbtransfer.cli import EXIT_ENV_ERROR, app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "dbtransfer 0.1.0" in result.output


def test_db_check_without_server(tmp_path, monkeypatch):
    monkeypatch.setenv("DBT_DB_PORT", "1")  # заведомо закрытый порт
    result = runner.invoke(app, ["db", "check", "--env-file", str(tmp_path / ".env")])
    assert result.exit_code == EXIT_ENV_ERROR
    assert "docker compose ps" in result.output


def _demo_project(tmp_path):
    import shutil

    from conftest import DEMO

    shutil.copytree(DEMO / "bw_export", tmp_path / "bw_export")
    shutil.copy(DEMO / "project.yaml", tmp_path / "project.yaml")
    return tmp_path


def test_import_report_scope_lineage(tmp_path):
    project = str(_demo_project(tmp_path))
    result = runner.invoke(app, ["import", "-p", project])
    assert result.exit_code == 0, result.output
    assert "ZSD_A99" in result.output
    assert (tmp_path / "model.json").is_file() and (tmp_path / "out" / "import_check.xlsx").is_file()

    result = runner.invoke(app, ["report", "-p", project])
    assert result.exit_code == 0 and (tmp_path / "out" / "complexity.xlsx").is_file()

    result = runner.invoke(app, ["scope", "--from", "ZSD_A01", "-p", project])
    assert result.exit_code == 0 and "ZSALES_FF → ZSD_A01" in result.output
    assert "scope_from: ZSD_A01" in (tmp_path / "project.yaml").read_text(encoding="utf-8")

    result = runner.invoke(app, ["lineage", "ZSD_A02.0NETVAL_INV", "-p", project])
    assert result.exit_code == 0 and "ZSALES_FF.DISCOUNT" in result.output
    result = runner.invoke(app, ["lineage", "ZSALES_FF.NETVAL", "--up", "-p", project])
    assert "ZSD_CP01" in result.output


def test_report_without_model(tmp_path):
    result = runner.invoke(app, ["report", "-p", str(_demo_project(tmp_path))])
    assert result.exit_code == 1 and "dbtransfer import" in result.output


def test_tables_lists_required_columns():
    result = runner.invoke(app, ["tables"])
    assert result.exit_code == 0
    assert "RSDIOBJ" in result.output and "IOBJTP" in result.output and "SE16" in result.output


def test_init(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path / "new")])
    assert result.exit_code == 0 and (tmp_path / "new" / "project.yaml").is_file()
