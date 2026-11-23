"""Диалекты целевых СУБД: что генератор может использовать в каждой версии.

Основная цель — Arenadata DB 7.5 / Greengage 7 (ядро PostgreSQL 12), диалект ``greenplum7``.
Диалект ``greenplum`` — режим совместимости с ADB 6.x, Greengage 6, Greenplum 6 и Yandex MPP
(ядро PostgreSQL 9.4); его пакет не меняется. ``postgres`` — обычный PostgreSQL 12+ без MPP.

Различия собраны в одном месте флагами возможностей, чтобы шаблоны и генератор спрашивали
«можно ли X», а не сравнивали имя диалекта.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dialect:
    name: str
    title: str
    mpp: bool  # DISTRIBUTED BY и параметры хранения AO
    # AO-таблицы: в 7 — метод доступа USING ao_column / ao_row, в 6 — WITH (appendonly=true, orientation=…)
    access_method_syntax: bool = False
    # PRIMARY KEY и уникальные индексы на AO-таблицах: есть в 7, в 6 только на heap
    ao_primary_key: bool = False
    # CREATE SEQUENCE IF NOT EXISTS (PostgreSQL 9.5+): в 6 нет, нужен блок DO
    sequence_if_not_exists: bool = False
    # CREATE PROCEDURE с COMMIT внутри (PostgreSQL 11+): в 6 только FUNCTION, транзакцию дробит оркестратор
    procedures: bool = False
    # Декларативные партиции PARTITION BY … + PARTITION OF (PostgreSQL 10+); классический синтаксис
    # Greenplum START … END … EVERY есть в обеих версиях
    declarative_partitions: bool = False
    # Триггеры FOR EACH STATEMENT: в Greenplum 7 их нет, строковые на MPP ограничены
    statement_triggers: bool = False
    # INSERT … ON CONFLICT (PostgreSQL 9.5+); MERGE нет ни в 6, ни в 7 (появился в PostgreSQL 15)
    on_conflict: bool = False
    # GENERATED ALWAYS AS (…) STORED (PostgreSQL 12+)
    generated_columns: bool = False

    def storage_clause(self, options: str) -> str:
        """Предложение хранения после списка колонок по параметрам из type_rules.yaml.

        Параметры записаны в форме Greenplum 6 (appendonly=true, orientation=column, …). Для 7 они
        переводятся в USING ao_column WITH (…): старая форма в 7 тоже принимается, но устарела.
        """
        if not options:
            return ""
        if not self.access_method_syntax:
            return f"WITH ({options})"
        params = [p.strip() for p in options.split(",") if p.strip()]
        values = {p.split("=", 1)[0].strip().lower(): p.split("=", 1)[1].strip().lower() if "=" in p else "true"
                  for p in params}
        ao = values.get("appendonly", values.get("appendoptimized", "false")) == "true"
        rest = [p for p in params if p.split("=", 1)[0].strip().lower()
                not in ("appendonly", "appendoptimized", "orientation")]
        method = ""
        if ao:
            method = "USING ao_column" if values.get("orientation") == "column" else "USING ao_row"
        with_part = f"WITH ({', '.join(rest)})" if rest else ""
        return "\n".join(x for x in (method, with_part) if x)


DIALECT_SPECS: dict[str, Dialect] = {
    "greenplum7": Dialect(
        "greenplum7", "Arenadata DB 7.5 / Greengage 7", mpp=True, access_method_syntax=True,
        ao_primary_key=True, sequence_if_not_exists=True, procedures=True, declarative_partitions=True,
        statement_triggers=False, on_conflict=True, generated_columns=True),
    "greenplum": Dialect(
        "greenplum", "ADB 6.x / Greengage 6 / Yandex MPP (совместимость)", mpp=True),
    "postgres": Dialect(
        "postgres", "PostgreSQL 12+", mpp=False, sequence_if_not_exists=True, procedures=True,
        declarative_partitions=True, statement_triggers=True, on_conflict=True, generated_columns=True),
    # Свой генератор и шаблоны (generator/clickhouse.py, templates/clickhouse/); флаги PostgreSQL к нему не относятся
    "clickhouse": Dialect("clickhouse", "ClickHouse (Yandex Managed Service for ClickHouse)", mpp=False),
}
DIALECTS = tuple(DIALECT_SPECS)
DEFAULT_DIALECT = "greenplum7"


def get_dialect(name: str) -> Dialect:
    try:
        return DIALECT_SPECS[name]
    except KeyError:
        raise ValueError(f"Диалект {name} неизвестен, допустимо: {', '.join(DIALECTS)}") from None


def dialect_for_server(version: str, server_version_num: int) -> str:
    """Диалект по строке version() и номеру версии ядра (120022 для PostgreSQL 12.22)."""
    if any(name in version for name in ("Greenplum", "Greengage", "Arenadata", "Cloudberry")):
        return "greenplum7" if server_version_num >= 120000 else "greenplum"
    return "postgres"


__all__ = ["DEFAULT_DIALECT", "DIALECTS", "DIALECT_SPECS", "Dialect", "dialect_for_server", "get_dialect"]
