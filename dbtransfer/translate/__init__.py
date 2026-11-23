"""Перевод SQL источника (Oracle, T-SQL, Teradata) на диалект цели: представления и процедуры."""

from __future__ import annotations

from dbtransfer.translate.names import NameMap, Relation
from dbtransfer.translate.sql import AUTO, FAILED, PARTIAL, SOURCE_DIALECTS, STATUS_TEXT, QueryResult, translate_query


def translate_model(model, dialect: str, rules=None) -> list:
    """Все представления (в порядке создания) и процедуры модели с текстом."""
    from dbtransfer.translate.routines import RoutineSet
    from dbtransfer.translate.views import translate_views

    names = NameMap(model, rules)
    routines = RoutineSet(model, dialect, names)
    routines.register(routines.objects())  # имена функций нужны и представлениям
    return [*translate_views(model, dialect, rules, names), *routines.translate_all()]


__all__ = ["AUTO", "FAILED", "PARTIAL", "SOURCE_DIALECTS", "STATUS_TEXT", "NameMap", "QueryResult", "Relation",
           "translate_model", "translate_query"]
