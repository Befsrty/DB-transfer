"""Суммы CURR: внутренний формат BW и настоящие суммы (сдвиг запятой по TCURX).

BW хранит сумму всегда с двумя знаками после запятой. У валюты с другим числом знаков запятая сдвинута:
1 000 JPY (0 знаков) лежат как 10,00, а 1,234 KWD (3 знака) — как 12,34. Настоящая сумма —
внутренняя, умноженная на 10^(2 − знаки валюты). Число знаков валют — в таблице TCURX (поле CURRDEC);
валюты, которых там нет, имеют 2 знака.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from dbtransfer.model import FieldDef, Model

INTERNAL, EXTERNAL = "internal", "external"
FORMATS = (INTERNAL, EXTERNAL)
SAP_DECIMALS = 2  # знаки внутреннего формата и валют, которых нет в TCURX


class CurrencyError(Exception):
    """Ошибка в секции currency файла type_rules.yaml; текст понятен пользователю."""


def check_format(value: str, where: str) -> str:
    if value not in FORMATS:
        raise CurrencyError(f"type_rules.yaml, {where}: формат сумм «{value}» неизвестен, "
                            f"допустимо: {', '.join(FORMATS)}")
    return value


def decimals_of(model: Model | None, defaults: dict[str, int]) -> dict[str, int]:
    """Знаки валют: TCURX из выгрузки, а если её нет — значения по умолчанию из type_rules.yaml."""
    if model is not None and model.currency_decimals:
        return dict(model.currency_decimals)
    return {k.upper(): int(v) for k, v in (defaults or {}).items()}


def factor(decimals: int, source: str, target: str) -> Decimal:
    """Множитель, переводящий сумму из формата source в формат target для валюты с decimals знаками."""
    if source == target:
        return Decimal(1)
    shift = SAP_DECIMALS - decimals
    return Decimal(10) ** (shift if source == INTERNAL else -shift)


def _literal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def factor_sql(currency_sql: str, decimals: dict[str, int], source: str, target: str) -> str | None:
    """Выражение SQL с множителем по коду валюты в строке; None, если пересчёт не нужен."""
    if source == target:
        return None
    cases = [(cur, factor(d, source, target)) for cur, d in sorted(decimals.items()) if d != SAP_DECIMALS]
    if not cases:
        return None
    when = " ".join(f"WHEN '{cur}' THEN {_literal(f)}" for cur, f in cases)
    return f"CASE upper(btrim({currency_sql})) {when} ELSE 1 END"


def widening(decimals: dict[str, int]) -> tuple[int, int]:
    """Сколько добавить к numeric(p,s) суммы CURR, чтобы настоящая сумма поместилась без потерь:
    (знаков до запятой, знаков после запятой). JPY: +2 до запятой, KWD: +1 после."""
    whole = max((SAP_DECIMALS - d for d in decimals.values() if d < SAP_DECIMALS), default=0)
    frac = max((d - SAP_DECIMALS for d in decimals.values() if d > SAP_DECIMALS), default=0)
    return whole, frac


@dataclass
class CurrencyRef:
    """Откуда берётся валюта суммы: поле той же строки или постоянная валюта показателя."""

    field: str | None = None  # имя поля (FieldDef.name)
    fixed: str | None = None  # код валюты

    @property
    def found(self) -> bool:
        return bool(self.field or self.fixed)


def currency_ref(model: Model, fields: list[FieldDef], f: FieldDef) -> CurrencyRef:
    """Валюта суммы f среди полей fields: по InfoObject показателя (валюта или постоянная валюта),
    иначе единственное поле типа CUKY. Не нашлась — пустая ссылка."""
    iobj = model.info_objects.get(f.info_object or f.name)
    if iobj is not None:
        if iobj.fixed_currency:
            return CurrencyRef(fixed=iobj.fixed_currency.upper())
        if iobj.unit_info_object:
            unit = iobj.unit_info_object
            match = next((x for x in fields if unit in (x.info_object, x.name)), None)
            if match is not None:
                return CurrencyRef(field=match.name)
    cuky = [x for x in fields if x.datatype == "CUKY"]
    return CurrencyRef(field=cuky[0].name) if len(cuky) == 1 else CurrencyRef()
