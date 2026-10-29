from dbtransfer.config import load_bw_tables, load_db_settings, read_env_file


def test_env_file_parsing(tmp_path, monkeypatch):
    for var in ("DBT_DB_HOST", "DBT_DB_PORT", "DBT_DB_NAME", "DBT_DB_USER", "DBT_DB_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text('# комментарий\nDBT_DB_PORT=5433\nDBT_DB_PASSWORD="секрет"\n\nмусор\n', encoding="utf-8")
    assert read_env_file(env) == {"DBT_DB_PORT": "5433", "DBT_DB_PASSWORD": "секрет"}
    settings = load_db_settings(env)
    assert settings.port == 5433 and settings.dbname == "demo" and settings.password == "секрет"
    assert "секрет" not in repr(settings) and "секрет" not in settings.describe()


def test_environment_overrides_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DBT_DB_HOST=from-file\n", encoding="utf-8")
    monkeypatch.setenv("DBT_DB_HOST", "from-env")
    assert load_db_settings(env).host == "from-env"


def test_bw_tables_required_set():
    tables = load_bw_tables()
    assert {"DD03L", "RSDIOBJ", "RSOADSO", "RSTRAN", "RSBKDTP"} <= {n for n, t in tables.items() if t.required}
    assert tables["DD03L"].version_column == "AS4LOCAL"
    assert "IOBJTP" in tables["RSDIOBJ"].required_columns
