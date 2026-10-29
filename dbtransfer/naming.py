"""Имена схем, таблиц и колонок в Greengage по dbtransfer/rules/naming.yaml (ТЗ, ФП-5.6)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

from dbtransfer.config import RULES_DIR
from dbtransfer.model import Adso, AdsoKind, DataSource, Model, ObjectType


@cache
def load_naming(path: Path = RULES_DIR / "naming.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def db_name(bw_name: str) -> str:
    """Имя BW -> имя в базе: 0NETVAL_INV -> netval_inv, /BIC/ZCUST -> zcust, ZSD_A01 -> zsd_a01."""
    name = bw_name.removeprefix("/BIC/").removeprefix("/BI0/")
    if name.startswith("0") and len(name) > 1 and not name[1:].isdigit():
        name = name[1:]
    return name.lower()


def schema(layer: str, prefix: str) -> str:
    return load_naming()["schemas"][layer].format(prefix=prefix)


def table(role: str, bw_name: str) -> str:
    return load_naming()["tables"][role].format(name=db_name(bw_name))


def adso_layer(model: Model, adso: Adso) -> str:
    """Слой ADSO (ТЗ, 3.3 и 4.3.4): staging — stg; standard и ADSO, из которого данные идут
    в другие ADSO, — dwh; прочие ADSO верхнего уровня — mart."""
    if adso.kind == AdsoKind.STAGING:
        return "stg"
    if adso.kind == AdsoKind.STANDARD:
        return "dwh"
    feeds_adso = any(t.source.type == ObjectType.ADSO and t.source.name == adso.name
                     and t.target.type == ObjectType.ADSO for t in model.transformations.values())
    return "dwh" if feeds_adso else "mart"


def data_table(model: Model, obj: Adso | DataSource) -> str:
    """Таблица, где лежат данные объекта (ТЗ, ФП-4.6): активные для ADSO, входящая для staging,
    входная для DataSource. Полное имя схема.таблица."""
    prefix = model.schema_prefix
    if isinstance(obj, DataSource):
        return f"{schema('stg', prefix)}.{table('datasource', obj.name)}"
    if obj.kind == AdsoKind.STAGING:
        return f"{schema('stg', prefix)}.{table('inbound', obj.name)}"
    return f"{schema(adso_layer(model, obj), prefix)}.{table('active', obj.name)}"


def md_attr_table(model: Model, characteristic: str) -> str:
    return f"{schema('md', model.schema_prefix)}.{table('md_attr', characteristic)}"


def column(field_name: str, info_object: str | None = None) -> str:
    """Колонка поля: по InfoObject для ADSO (0NETVAL_INV -> netval_inv), по имени поля для DataSource."""
    return db_name(info_object or field_name)


def table_schema(model: Model, adso: Adso, role: str) -> str:
    """Схема таблицы ADSO по роли: входящая — stg, активные данные и журнал — слой ADSO (ТЗ, 4.3.4)."""
    layer = "stg" if role == "inbound" else adso_layer(model, adso)
    return schema(layer, model.schema_prefix)
