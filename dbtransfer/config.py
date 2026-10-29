"""Настройки приложения: правила из dbtransfer/rules и подключение к базе из .env."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

RULES_DIR = Path(__file__).parent / "rules"


class BwColumn(BaseModel):
    required: bool = False


class BwTable(BaseModel):
    name: str
    group: str
    purpose: str
    level: str
    required: bool
    hypothesis: bool
    version_column: str = "OBJVERS"
    columns: dict[str, BwColumn]

    @property
    def required_columns(self) -> list[str]:
        return [name for name, col in self.columns.items() if col.required]


@cache
def load_bw_tables(path: Path = RULES_DIR / "bw_tables.yaml") -> dict[str, BwTable]:
    """Описание таблиц выгрузки BW: имя таблицы -> состав колонок."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        name: BwTable(name=name, **{**spec, "columns": {c: v or {} for c, v in spec["columns"].items()}})
        for name, spec in raw["tables"].items()
    }


class DbSettings(BaseModel):
    host: str = "localhost"
    port: int = 15432
    dbname: str = "demo"
    user: str = "gpadmin"
    password: str = Field(default="", repr=False)

    def conninfo(self) -> dict[str, str | int]:
        return {
            "host": self.host, "port": self.port, "dbname": self.dbname,
            "user": self.user, "password": self.password,
        }

    def describe(self) -> str:
        """Строка подключения без пароля, для сообщений."""
        return f"{self.user}@{self.host}:{self.port}/{self.dbname}"


def read_env_file(path: Path) -> dict[str, str]:
    """Простой разбор .env: строки КЛЮЧ=значение, # комментарии, кавычки по краям убираются."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_db_settings(env_file: Path = Path(".env")) -> DbSettings:
    """Параметры подключения: переменные окружения DBT_DB_* важнее значений из .env."""
    file_values = read_env_file(env_file)

    def get(name: str, default: str) -> str:
        return os.environ.get(name) or file_values.get(name) or default

    return DbSettings(
        host=get("DBT_DB_HOST", "localhost"),
        port=int(get("DBT_DB_PORT", "15432")),
        dbname=get("DBT_DB_NAME", "demo"),
        user=get("DBT_DB_USER", "gpadmin"),
        password=get("DBT_DB_PASSWORD", ""),
    )
