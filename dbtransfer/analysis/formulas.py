"""Проверка формулы трансформации по словарю функций (ТЗ, приложение В)."""

from __future__ import annotations

import re

SUPPORTED_FUNCTIONS = {
    "IF", "CONCATENATE", "SUBSTRING", "STRLEN", "TOUPPER", "ABS", "ROUND", "IS_INITIAL",
    "DATE_YEAR", "DATE_MONTH",
}
SUPPORTED_WORDS = {"AND", "OR", "NOT"}

_FUNCTION = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_STRING = re.compile(r"'[^']*'")


def unsupported_functions(formula: str) -> list[str]:
    """Функции формулы, которых нет в словаре. Пустой список — формула переводится автоматически."""
    text = _STRING.sub("''", formula)
    return sorted({f.upper() for f in _FUNCTION.findall(text)} - SUPPORTED_FUNCTIONS)
