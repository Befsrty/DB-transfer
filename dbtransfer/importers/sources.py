"""Выбор источника модели: выгрузки SAP BW или словарь Oracle / MS SQL Server.

Командная строка и интерфейс вызывают import_model и export_help, не зная устройства импортёров.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from dbtransfer.importers import mssql_dict, oracle_dict
from dbtransfer.importers.bw_csv import SE16_HELP, ImportFailed, import_bw_export
from dbtransfer.model import Model, SourceKind

# Имя источника в командной строке -> вид модели и подпись
SOURCES = {
    "bw": (SourceKind.SAP_BW, "SAP BW: CSV-выгрузки SE16"),
    "oracle": (SourceKind.ORACLE, "Oracle: словарь ALL_*"),
    "mssql": (SourceKind.MSSQL, "MS SQL Server: INFORMATION_SCHEMA и sys.*"),
}
CONNECTORS = {"oracle": oracle_dict.connect, "mssql": mssql_dict.connect}


def source_kind(source: str) -> SourceKind:
    if source not in SOURCES:
        raise ImportFailed(f"Источник «{source}» неизвестен, допустимо: {', '.join(SOURCES)}")
    return SOURCES[source][0]


def export_help(source: str) -> str:
    """Как получить выгрузку для источника: инструкция SE16 или запросы к словарю."""
    kind = source_kind(source)
    if kind == SourceKind.ORACLE:
        return oracle_dict.help_text()
    if kind == SourceKind.MSSQL:
        return mssql_dict.help_text()
    return SE16_HELP


def import_model(source: str, folder: Path | str | None = None, *, url: str | None = None,
                 password: str | None = None, schemas: Iterable[str] | None = None, system: str | None = None,
                 encoding: str | None = None, delimiter: str | None = None, language: str = "R",
                 target: str = "table") -> Model:
    """Построить модель: из папки выгрузок или, для Oracle и MS SQL Server, по подключению url."""
    kind = source_kind(source)
    if kind == SourceKind.SAP_BW:
        if url:
            raise ImportFailed("Подключение напрямую к SAP BW не поддерживается: выгрузите таблицы через SE16")
        return import_bw_export(folder, language=language, encoding=encoding, delimiter=delimiter)
    importer = oracle_dict.import_oracle_dict if kind == SourceKind.ORACLE else mssql_dict.import_mssql_dict
    extra = {"system": system.upper(), "target": target} if system else {"target": target}
    if url:
        try:
            conn = CONNECTORS[source](url, password)
        except ImportFailed:
            raise
        except Exception as e:  # noqa: BLE001 — у драйверов свои классы ошибок
            raise ImportFailed(f"Не удалось подключиться к {SOURCES[source][1].split(':')[0]}: {e}") from e
        try:
            return importer(conn=conn, schemas=schemas, **extra)
        finally:
            conn.close()
    return importer(folder, schemas=schemas, encoding=encoding, delimiter=delimiter, **extra)
