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
