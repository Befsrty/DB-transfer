"""Командная строка dbtransfer (ТЗ, раздел 4.4.2). Команды добавляются по шагам плана."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dbtransfer import __version__
from dbtransfer.config import load_db_settings
from dbtransfer.db import DbError, check_connection

# Коды возврата по ТЗ: 0 успех, 1 ошибка данных, 2 ошибка подключения или окружения
EXIT_DATA_ERROR = 1
EXIT_ENV_ERROR = 2

app = typer.Typer(
    help="DB-transfer: перенос структуры хранилища SAP BW в Greenplum/Arenadata DB.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="Работа с целевой базой.", no_args_is_help=True)
app.add_typer(db_app, name="db")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"dbtransfer {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Показать версию.")
    ] = False,
) -> None:
    pass


@db_app.command("check")
def db_check(
    env_file: Annotated[Path, typer.Option("--env-file", help="Файл с параметрами подключения.")] = Path(".env"),
) -> None:
    """Проверить подключение к базе: версия СУБД и право создавать схемы."""
    settings = load_db_settings(env_file)
    typer.echo(f"Подключение: {settings.describe()}")
    try:
        info = check_connection(settings)
    except DbError as e:
        typer.secho(f"Ошибка: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ENV_ERROR) from None
    typer.echo(f"СУБД: {info.version}")
    if info.can_create_schema:
        typer.secho("Права на создание схем есть. Всё готово.", fg=typer.colors.GREEN)
    else:
        typer.secho(f"У пользователя {settings.user} нет права CREATE в базе {settings.dbname}.", fg=typer.colors.RED)
        raise typer.Exit(EXIT_ENV_ERROR)
