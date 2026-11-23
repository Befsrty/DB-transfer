"""Перевод типов, хранение и распределение по type_rules.yaml (ТЗ, 4.3.3, ФП-5.4, ФП-5.5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dbtransfer import currency as cur
from dbtransfer.config import RULES_DIR
from dbtransfer.model import FieldDef, InfoObject, InfoObjectKind, Model

TYPE_RULES_FILE = "type_rules.yaml"
STORAGE_KINDS = ("heap", "ao_column")
NUMERIC_TYPES = {"CURR", "QUAN", "DEC", "NUMERIC", "INT1", "INT2", "INT4", "INT8", "FLTP"}
TEXT_TYPES = {"CHAR", "FIXCHAR", "CUKY", "UNIT", "LANG", "STRING", "SSTRING"}


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
    # Правила для ClickHouse: второй шаг перевода типов, движки таблиц (секция clickhouse в type_rules.yaml)
    clickhouse: dict = field(default_factory=dict)
    # Суммы CURR: формат хранения, входящих файлов и эталона, знаки валют (секция currency)
    currency: dict = field(default_factory=dict)
    # Типы, которых нет в правилах: тип -> поля, для предупреждений генерации
    unknown: dict[str, list[str]] = field(default_factory=dict)
    currency_decimals: dict[str, int] = field(default_factory=dict)
    currency_from_tcurx: bool = False

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
        try:
            cur.check_format(rules.currency_store, "currency.store")
            cur.check_format(rules.currency.get("input", cur.INTERNAL), "currency.input")
            cur.check_format(rules.currency.get("input_file", cur.EXTERNAL), "currency.input_file")
            cur.check_format(rules.etalon_format, "currency.etalon")
            for obj, spec in rules.overrides.items():
                if isinstance(spec, dict) and "currency_input" in spec:
                    cur.check_format(spec["currency_input"], f"overrides.{obj}.currency_input")
        except cur.CurrencyError as e:
            raise RulesError(str(e)) from e
        rules.bind(None)
        for role, kind in rules.storage.items():
            if kind not in STORAGE_KINDS:
                raise RulesError(f"type_rules.yaml: хранение «{kind}» для {role} неизвестно, "
                                 f"допустимо: {', '.join(STORAGE_KINDS)}")
        return rules

    # --- суммы CURR ---

    def bind(self, model: Model | None) -> TypeRules:
        """Знаки валют модели (TCURX из выгрузки), а без неё — из type_rules.yaml."""
        self.currency_decimals = cur.decimals_of(model, self.currency.get("decimals") or {})
        self.currency_from_tcurx = bool(model is not None and model.currency_decimals)
        return self

    @property
    def currency_store(self) -> str:
        return self.currency.get("store", cur.INTERNAL)

    @property
    def etalon_format(self) -> str:
        return self.currency.get("etalon", self.currency_store)

    def currency_input(self, obj: str, flat_file: bool = False) -> str:
        """Формат сумм во входящем файле объекта: overrides.<объект>.currency_input, иначе input / input_file."""
        own = self.overrides.get(obj, {}).get("currency_input") if isinstance(self.overrides.get(obj), dict) else None
        if own:
            return own
        if flat_file:
            return self.currency.get("input_file", self.currency.get("input", self.currency_store))
        return self.currency.get("input", self.currency_store)

    def column_type(self, f: FieldDef, owner: str = "") -> str:
        if f.target_type:  # источник той же семьи (Greenplum): тип как есть
            return f.target_type
        template = self.types.get(f.datatype)
        if template is None or ("{length}" in template and f.length <= 0):
            self.unknown.setdefault(f.datatype or "(пусто)", []).append(f"{owner}.{f.name}" if owner else f.name)
            return self.default_type
        length, decimals = f.length, f.decimals
        if f.datatype == "CURR" and self.currency_store == cur.EXTERNAL:
            # Настоящая сумма JPY в 100 раз больше внутренней, KWD — на знак длиннее после запятой
            whole, frac = cur.widening(self.currency_decimals)
            length, decimals = length + whole + frac, decimals + frac
        return template.format(length=length, decimals=decimals)

    def initial_value(self, f: FieldDef) -> str:
        """Начальное значение BW как литерал SQL: для ключевых полей вместо NULL (4.3.3)."""
        if f.datatype == "NUMC":
            return "'" + "0" * max(f.length, 1) + "'"
        if f.datatype == "DATS":
            return f"DATE '{self.date_key_placeholder}'"
        if f.datatype == "TIMS":
            return "TIME '00:00:00'"
        if f.datatype in ("TIMESTAMP", "TIMESTAMPTZ"):
            return f"TIMESTAMP '{self.date_key_placeholder} 00:00:00'"
        if f.datatype == "BINARY":
            return "''::bytea"
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
