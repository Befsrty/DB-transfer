"""Имена объектов источника в целевой базе: таблицы, ADSO и представления модели.

Переводчик SQL заменяет ссылки на таблицы источника (SALES.ORDERS, [dbo].[Orders], ORDERS той же схемы)
на имена в целевой базе (dbt_sales.orders) и знает типы колонок: от них зависит перевод арифметики дат
(в Oracle DATE + 1 — следующий день, в PostgreSQL timestamp + 1 — ошибка) и сложения строк в T-SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dbtransfer import naming
from dbtransfer.generator.sql import ident, qualified
from dbtransfer.generator.types import TypeRules
from dbtransfer.model import DbTable, FieldDef, Model, ObjectType

# Категории типов колонок, нужные переводчику
DATE, TIMESTAMP, TIME, STRING, INTEGER, NUMBER = "date", "timestamp", "time", "string", "integer", "number"


def type_category(target_type: str) -> str:
    """Категория по типу целевой базы: timestamp(0) -> timestamp, varchar(20) -> string."""
    t = target_type.lower().strip()
    if t.startswith("timestamp"):
        return TIMESTAMP
    if t.startswith("date"):
        return DATE
    if t.startswith("time"):
        return TIME
    if t.startswith(("varchar", "char", "text", "bpchar", "character", "string", "fixedstring", "nullable(string")):
        return STRING
    if t.startswith(("smallint", "integer", "int", "bigint", "uint")):
        return INTEGER
    if t.startswith(("numeric", "decimal", "double", "real", "float")):
        return NUMBER
    return ""


@dataclass
class Relation:
    """Таблица или представление в целевой базе."""

    source: str  # СХЕМА.ИМЯ в источнике
    target: str  # схема.имя в целевой базе, готовое для SQL
    kind: str = "table"  # table или view
    columns: dict[str, str] = field(default_factory=dict)  # колонка в целевой базе -> категория типа


class NameMap:
    """Ссылки источника -> имена целевой базы. Ключ — (СХЕМА, ИМЯ) в верхнем регистре."""

    def __init__(self, model: Model | None = None, rules: TypeRules | None = None):
        self.relations: dict[tuple[str, str], Relation] = {}
        # Функции источника, переведённые вместе с моделью: (СХЕМА, ИМЯ) -> имя в целевой базе
        self.functions: dict[tuple[str, str], str] = {}
        if model is not None:
            self._from_model(model, rules or TypeRules.load())

    def add(self, schema: str, name: str, relation: Relation) -> None:
        self.relations[(schema.upper(), name.upper())] = relation

    def _from_model(self, model: Model, rules: TypeRules) -> None:
        def columns(fields: list[FieldDef], adso: bool) -> dict[str, str]:
            result = {}
            for f in fields:
                col = naming.column(f.name, f.info_object) if adso else naming.db_name(f.name)
                result[col] = type_category(rules.column_type(f))
            return result

        for t in model.tables.values():
            self.add(t.schema_name, t.name, Relation(t.key_name, naming.data_table(model, t),
                                                     columns=columns(t.fields, False)))
        # Режим ADSO: таблица SALES.ORDERS стала ADSO SALES_ORDERS; схема источника — в имени
        for o in model.others:
            if o.type != ObjectType.VIEW or "." not in o.name:
                continue
            schema, name = o.name.split(".", 1)
            self.add(schema, name, Relation(o.name, view_target(model, schema, name), kind="view"))
        if not model.is_bw:
            for adso in model.adsos.values():
                schema = adso.info_area
                if not schema or not adso.name.startswith(schema + "_"):
                    continue
                table = adso.name[len(schema) + 1:]
                if (schema.upper(), table.upper()) not in self.relations:
                    self.add(schema, table, Relation(f"{schema}.{table}", naming.data_table(model, adso),
                                                     columns=columns(adso.fields, True)))

    def resolve_function(self, schema: str | None, name: str, default_schema: str) -> str | None:
        if schema:
            return self.functions.get((schema.upper(), name.upper()))
        return self.functions.get((default_schema.upper(), name.upper()))

    def resolve(self, schema: str | None, name: str, default_schema: str) -> Relation | None:
        if schema:
            return self.relations.get((schema.upper(), name.upper()))
        found = self.relations.get((default_schema.upper(), name.upper()))
        if found is not None:
            return found
        matches = [r for (_, n), r in self.relations.items() if n == name.upper()]
        return matches[0] if len(matches) == 1 else None


def view_target(model: Model, schema: str, name: str) -> str:
    """Имя представления в целевой базе: схема как у таблиц той же схемы источника (SALES.V -> dbt_sales.v)."""
    target_schema = naming.load_naming()["schemas"]["source"].format(prefix=model.schema_prefix,
                                                                      schema=schema.lower())
    return qualified(target_schema, name.lower())


def table_target(model: Model, t: DbTable) -> str:
    return naming.data_table(model, t)


__all__ = ["DATE", "INTEGER", "NUMBER", "STRING", "TIME", "TIMESTAMP", "NameMap", "Relation", "ident",
           "type_category", "view_target"]
