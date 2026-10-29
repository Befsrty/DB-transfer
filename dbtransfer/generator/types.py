"""Перевод типов, хранение и распределение по type_rules.yaml (ТЗ, 4.3.3, ФП-5.4, ФП-5.5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dbtransfer.config import RULES_DIR
from dbtransfer.model import FieldDef, InfoObject, InfoObjectKind, Model

TYPE_RULES_FILE = "type_rules.yaml"
STORAGE_KINDS = ("heap", "ao_column")
NUMERIC_TYPES = {"CURR", "QUAN", "DEC", "INT1", "INT2", "INT4", "INT8", "FLTP"}
TEXT_TYPES = {"CHAR", "CUKY", "UNIT", "LANG", "STRING", "SSTRING"}


class RulesError(Exception):
    """Ошибка в type_rules.yaml; текст понятен пользователю."""


def _merge(base: dict, extra: dict) -> dict:
    result = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class TypeRules:
    types: dict[str, str]
    default_type: str
    date_key_placeholder: str
    storage: dict[str, str]
    storage_options: dict[str, str]
    overrides: dict[str, dict] = field(default_factory=dict)
    # Типы, которых нет в правилах: тип -> поля, для предупреждений генерации
    unknown: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, project_dir: Path | None = None) -> TypeRules:
        """Правила по умолчанию, поверх них — type_rules.yaml проекта, если он есть."""
        raw = yaml.safe_load((RULES_DIR / TYPE_RULES_FILE).read_text(encoding="utf-8"))
        if project_dir is not None and (project_dir / TYPE_RULES_FILE).is_file():
            path = project_dir / TYPE_RULES_FILE
            try:
                extra = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                raise RulesError(f"{path}: файл не читается: {e}") from e
            raw = _merge(raw, extra)
        rules = cls(**{k: v if v is not None else {} for k, v in raw.items()})
        for role, kind in rules.storage.items():
            if kind not in STORAGE_KINDS:
                raise RulesError(f"type_rules.yaml: хранение «{kind}» для {role} неизвестно, "
                                 f"допустимо: {', '.join(STORAGE_KINDS)}")
        return rules

    def column_type(self, f: FieldDef, owner: str = "") -> str:
        template = self.types.get(f.datatype)
        if template is None or ("{length}" in template and f.length <= 0):
            self.unknown.setdefault(f.datatype or "(пусто)", []).append(f"{owner}.{f.name}" if owner else f.name)
            return self.default_type
        return template.format(length=f.length, decimals=f.decimals)

    def initial_value(self, f: FieldDef) -> str:
        """Начальное значение BW как литерал SQL: для ключевых полей вместо NULL (4.3.3)."""
        if f.datatype == "NUMC":
            return "'" + "0" * max(f.length, 1) + "'"
        if f.datatype == "DATS":
            return f"DATE '{self.date_key_placeholder}'"
        if f.datatype == "TIMS":
            return "TIME '00:00:00'"
        if f.datatype in NUMERIC_TYPES:
            return "0"
        return "''"

    def storage_for(self, role: str, obj: str | None = None) -> str:
        kind = self.overrides.get(obj or "", {}).get("storage", {}).get(role) or self.storage.get(role, "heap")
        if kind not in STORAGE_KINDS:
            raise RulesError(f"type_rules.yaml, overrides.{obj}: хранение «{kind}» неизвестно, "
                             f"допустимо: {', '.join(STORAGE_KINDS)}")
        return kind

    def distributed_by(self, obj: str) -> list[str] | None:
        """Ключ распределения из overrides: список имён полей BW или колонок."""
        value = self.overrides.get(obj, {}).get("distributed_by")
        return list(value) if value else None


def is_key_figure(model: Model, f: FieldDef) -> bool:
    """Показатель: по InfoObject, а если его нет в модели — по числовому типу."""
    iobj: InfoObject | None = model.info_objects.get(f.info_object or f.name)
    if iobj is not None:
        return iobj.kind == InfoObjectKind.KEY_FIGURE
    return f.datatype in NUMERIC_TYPES
