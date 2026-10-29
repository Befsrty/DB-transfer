"""Мелкие помощники SQL: имена, строковые литералы, комментарии."""

from __future__ import annotations

import re

# Зарезервированные слова PostgreSQL 9.4 (основа Greengage 6): такие имена берутся в кавычки
RESERVED = frozenset("""
all analyse analyze and any array as asc asymmetric authorization binary both case cast check collate
collation column concurrently constraint create cross current_catalog current_date current_role
current_schema current_time current_timestamp current_user default deferrable desc distinct do else end
except false fetch for foreign freeze from full grant group having ilike in initially inner intersect into
is isnull join lateral leading left like limit localtime localtimestamp natural not notnull null offset on
only or order outer over overlaps placing primary references returning right select session_user similar
some symmetric table then to trailing true union unique user using variadic verbose when where window with
distributed partition
""".split())

_PLAIN = re.compile(r"^[a-z_][a-z0-9_]*$")


def ident(name: str) -> str:
    """Имя в SQL: как есть, если это простое имя, иначе в двойных кавычках."""
    if _PLAIN.match(name) and name not in RESERVED:
        return name
    return '"' + name.replace('"', '""') + '"'


def qualified(schema: str, name: str) -> str:
    return f"{ident(schema)}.{ident(name)}"


def literal(value: str) -> str:
    """Строковый литерал SQL."""
    return "'" + value.replace("'", "''") + "'"


def sql_comment(text: str) -> str:
    """Многострочный текст как строки комментария «-- »."""
    return "\n".join(f"-- {line}".rstrip() for line in text.splitlines())
