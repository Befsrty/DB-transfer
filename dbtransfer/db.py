"""Подключение к целевой базе Greengage / Greenplum."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from dbtransfer.config import DbSettings


class DbError(Exception):
    """Не удалось подключиться или выполнить запрос; текст понятен пользователю."""


@dataclass
class ConnectionInfo:
    version: str
    can_create_schema: bool


def connect(settings: DbSettings, timeout: int = 10) -> psycopg.Connection:
    try:
        return psycopg.connect(**settings.conninfo(), connect_timeout=timeout)
    except psycopg.OperationalError as e:
        raise DbError(
            f"нет подключения к {settings.describe()}: {str(e).strip()}. "
            "Проверьте, что контейнер запущен (docker compose ps) и параметры в .env совпадают с docker-compose.yml. "
            "Сразу после первого docker compose up база готовится 1–3 минуты."
        ) from e


def check_connection(settings: DbSettings) -> ConnectionInfo:
    with connect(settings) as conn:
        version = conn.execute("SELECT version()").fetchone()[0]
        can_create = conn.execute("SELECT has_database_privilege(current_database(), 'CREATE')").fetchone()[0]
    return ConnectionInfo(version=version, can_create_schema=bool(can_create))
