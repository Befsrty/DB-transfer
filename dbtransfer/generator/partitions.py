"""Секционирование таблиц, перенесённых 1:1: PARTITION BY для Greenplum 6 и для PostgreSQL 10+.

Границы приходят из импорта уже литералами SQL целевой базы. Greenplum 6 описывает секции внутри
CREATE TABLE (START / END, VALUES, DEFAULT PARTITION), PostgreSQL — отдельными CREATE TABLE ... PARTITION OF.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dbtransfer.generator.sql import ident, qualified
from dbtransfer.model import Partitioning

MAX_PARTITION_NAME = 20  # Greenplum строит имя секции <таблица>_1_prt_<имя>, оно тоже не длиннее 63


@dataclass
class PartitionSql:
    clause: str = ""  # PARTITION BY ...
    after: str = ""  # PostgreSQL: CREATE TABLE ... PARTITION OF ...
    warnings: list[str] = field(default_factory=list)


def partition_name(name: str, index: int) -> str:
    clean = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:MAX_PARTITION_NAME].strip("_")
    return clean if clean and not clean[0].isdigit() else f"p{index}"


def partition_sql(p: Partitioning, schema: str, table: str, columns: list[str], greenplum: bool) -> PartitionSql:
    """columns — имена колонок секционирования в целевой базе (уже в нужном регистре и кавычках)."""
    result = PartitionSql()
    names: set[str] = set()
    parts = []
    for i, part in enumerate(p.partitions, start=1):
        name = partition_name(part.name, i)
        if name in names:
            name = f"{name[:MAX_PARTITION_NAME - 4]}_{i}"
        names.add(name)
        parts.append((name, part))
    key = ", ".join(columns)
    if greenplum:
        lines = []
        prev: str | None = None
        for name, part in parts:
            if part.is_default:
                lines.append(f"    DEFAULT PARTITION {ident(name)}")
                continue
            if p.kind == "LIST":
                lines.append(f"    PARTITION {ident(name)} VALUES ({', '.join(part.values)})")
                continue
            bound = ", ".join(part.values)
            lower, upper = ("EXCLUSIVE", "INCLUSIVE") if p.upper_inclusive else ("INCLUSIVE", "EXCLUSIVE")
            start = f"START ({prev}) {lower} " if prev is not None else ""
            lines.append(f"    PARTITION {ident(name)} {start}END ({bound}) {upper}")
            prev = bound
        result.clause = f"PARTITION BY {p.kind} ({key})\n(\n" + ",\n".join(lines) + "\n)"
        return result

    result.clause = f"PARTITION BY {p.kind} ({key})"
    statements = []
    prev = None
    width = len(columns)
    for name, part in parts:
        child = qualified(schema, f"{table}_{name}")
        if part.is_default:
            bounds = "DEFAULT"
        elif p.kind == "LIST":
            bounds = f"FOR VALUES IN ({', '.join(part.values)})"
        else:
            lower = prev or ", ".join(["MINVALUE"] * width)
            bounds = f"FOR VALUES FROM ({lower}) TO ({', '.join(part.values)})"
            prev = ", ".join(part.values)
        statements.append(f"CREATE TABLE IF NOT EXISTS {child} PARTITION OF {qualified(schema, table)} {bounds};\n")
    if p.kind == "RANGE" and p.upper_inclusive:
        result.warnings.append(f"{schema}.{table}: в PostgreSQL верхняя граница секции не входит в секцию, "
                               "а в источнике (RANGE LEFT) входит — значения, равные границе, попадут в соседнюю")
    result.after = "".join(statements)
    return result
