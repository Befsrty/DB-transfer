"""Пакет SQL для ClickHouse (Yandex Managed Service for ClickHouse и свой сервер).

Модель, состав переноса, таблицы и правила трансформаций — те же, что для Greengage. Отличия:
- схема слоя — база данных ClickHouse (CREATE DATABASE);
- типы переводятся второй ступенью по секции clickhouse в type_rules.yaml, движок таблицы — по роли;
- последовательностей нет: номера выдаёт приложение;
- функций PL/pgSQL нет: активация и трансформации — списки шагов SqlObject.steps, которые прогон
  выполняет по очереди с параметром запроса {p_load_id:Int64}. В файлах 060 и 070 те же шаги для чтения.
Шаблоны — templates/clickhouse/.
"""

from __future__ import annotations

import re

from dbtransfer import naming
from dbtransfer.generator.dialect import ClickHouseSql, ch_literal
from dbtransfer.generator.package import SEQUENCES, Generator, Package
from dbtransfer.generator.sql import ident, qualified
from dbtransfer.generator.tables import Column, TableDef
from dbtransfer.generator.transform import TransformBuilder
from dbtransfer.generator.types import RulesError, TypeRules, is_key_figure
from dbtransfer.model import Adso, AdsoKind, Model

STEP_MARK = "-- Шаг: "
_PG_TYPE = re.compile(r"^([a-z ]+?)\s*(?:\((\d+)(?:,\s*(\d+))?\))?$")


def _steps(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Текст шаблона -> вводные комментарии и шаги (заголовок, SQL без «;»)."""
    preamble, *blocks = text.split("\n" + STEP_MARK)
    steps = []
    for block in blocks:
        title, _, sql = block.partition("\n")
        steps.append((title.strip(), sql.strip().removesuffix(";").rstrip()))
    return preamble.rstrip("\n") + "\n", steps


def render_steps(preamble: str, steps: list[tuple[str, str]]) -> str:
    return preamble + "".join(f"-- Шаг {i}. {title}\n{sql};\n" for i, (title, sql) in enumerate(steps, 1))


class ClickHouseTypes:
    """Тип PostgreSQL из TableBuilder -> тип ClickHouse по type_rules.yaml (секция clickhouse)."""

    def __init__(self, rules: TypeRules):
        ch = rules.clickhouse or {}
        if not ch.get("types"):
            raise RulesError("type_rules.yaml: нет секции clickhouse.types — правил перевода типов для ClickHouse")
        self.types: dict[str, str] = ch["types"]
        self.numeric_default: str = ch.get("numeric_default", "Decimal(38,10)")
        self.engines: dict[str, str] = ch.get("engines", {})
        self.date_key_placeholder: str = ch.get("date_key_placeholder", "1900-01-01")

    def column_type(self, pg_type: str, not_null: bool) -> str:
        m = _PG_TYPE.match(pg_type.strip().lower())
        template = self.types.get(m.group(1)) if m else None
        if template is None:
            raise RulesError(f"type_rules.yaml, clickhouse.types: нет перевода для типа «{pg_type}»")
        if "{p}" in template:
            if m.group(2) is None:
                ch = self.numeric_default
            else:
                ch = template.format(p=m.group(2), s=m.group(3) or 0)
        else:
            ch = template
        return ch if not_null else f"Nullable({ch})"

    def engine(self, role: str) -> str:
        return self.engines.get(role or "meta", "MergeTree")


class ClickHouseGenerator(Generator):
    def __init__(self, model: Model, rules: TypeRules, dialect: str = "clickhouse", scope: str | None = None):
        super().__init__(model, rules, dialect, scope)
        self.ch_types = ClickHouseTypes(rules)
        self.sql = ClickHouseSql(self.ch_types.date_key_placeholder)
        self.transforms = TransformBuilder(model, self.tables, self.sql)
        self.env.filters["ch_literal"] = ch_literal

    # --- таблицы ---
    def order_by(self, t: TableDef) -> list[str]:
        """Ключ сортировки MergeTree: первичный ключ; для входящих таблиц и журналов — номер загрузки
        и записи (прогон читает их по load_id); иначе ключ распределения Greengage."""
        if t.primary_key:
            return t.primary_key
        if t.column("load_id") and t.column("record_no"):
            return ["load_id", "record_no"]
        return t.distributed_by

    def table_sql(self, t: TableDef) -> str:
        columns = [Column(c.sql_name, self.ch_types.column_type(c.type, c.not_null), c.not_null, c.comment)
                   for c in t.columns]
        order = self.order_by(t)
        order_by = order[0] if len(order) == 1 else f"({', '.join(order)})" if order else "tuple()"
        return self.env.get_template("clickhouse/table.sql.j2").render(
            t=t, columns=columns, width=max(len(c.name) for c in columns), engine=self.ch_types.engine(t.role),
            order_by=order_by)

    def build(self) -> Package:
        package = super().build()
        for o in package.objects:
            if o.kind == "schema":
                o.sql = f"CREATE DATABASE IF NOT EXISTS {o.name};\n"
        return package

    def _meta(self) -> None:
        note = self.env.get_template("clickhouse/meta.sql.j2").render(sequences=SEQUENCES)
        start = len(self.package.objects)
        super()._meta()
        # Последовательностей нет: вместо объекта — пояснение перед первой служебной таблицей
        self.package.objects = [o for o in self.package.objects if o.kind != "sequence"]
        self.package.objects[start].sql = note + self.package.objects[start].sql

    # --- активация ---
    def _activation(self, adso: Adso) -> None:
        if adso.kind not in (AdsoKind.STANDARD, AdsoKind.CUMULATIVE):
            return
        keys = self.tables.adso_keys(adso)
        if not keys:
            self.package.warnings.append(f"У ADSO {adso.name} нет ключа: шаги активации не созданы")
            return
        active = self.tables.adso_table(adso, "active")
        inbound = self.tables.adso_table(adso, "inbound")
        fields = [c for c in active.columns if c.field is not None]
        key_cols = [ident(naming.column(f.name, f.info_object)) for f in keys]
        p = self.sql.load_id
        ctx = dict(adso=adso.name, inbound=inbound.qname, active=active.qname, p=p,
                   columns=[c.sql_name for c in fields], key_list=", ".join(key_cols))
        if adso.kind == AdsoKind.STANDARD:
            change_log = self.tables.adso_table(adso, "change_log")
            # Последняя запись по ключу: LIMIT 1 BY вместо row_number() — обычный приём ClickHouse
            dedup = (f"SELECT * FROM {inbound.qname} WHERE load_id = {p} "
                     f"ORDER BY record_no DESC LIMIT 1 BY {', '.join(key_cols)}")
            before = [f"-a.{c.sql_name}" if is_key_figure(self.model, c.field) else f"a.{c.sql_name}"
                      for c in fields]
            text = self.env.get_template("clickhouse/activation_standard.sql.j2").render(
                **ctx, change_log=change_log.qname, dedup=dedup, before=before,
                after=[f"d.{c.sql_name}" for c in fields],
                join_ad=" AND ".join(f"a.{k} = d.{k}" for k in key_cols))
        else:
            measures = [c.sql_name for c in fields if c.sql_name not in key_cols]
            grouped = (f"SELECT {', '.join(f'i.{k} AS {k}' for k in key_cols)}"
                       + "".join(f", sum(i.{m}) AS {m}" for m in measures)
                       + f" FROM {inbound.qname} i WHERE i.load_id = {p} GROUP BY "
                       + ", ".join("i." + k for k in key_cols))
            merged = [f"g.{c}" if c in key_cols else
                      f"if(coalesce(a._hit, 0) = 1, coalesce(a.{c}, 0) + coalesce(g.{c}, 0), g.{c})"
                      for c in ctx["columns"]]
            work = self._work_table(adso, active)
            text = self.env.get_template("clickhouse/activation_cumulative.sql.j2").render(
                **ctx, work=work.qname, grouped=grouped, merged=merged,
                join_ag=" AND ".join(f"a.{k} = g.{k}" for k in key_cols))
        preamble, steps = _steps(text)
        self.add("activation", "steps", f"activate_{naming.db_name(adso.name)}", render_steps(preamble, steps),
                 bw_object=f"ADSO {adso.name}", ref=adso.name, steps=steps)

    def _work_table(self, adso: Adso, active: TableDef) -> TableDef:
        """Рабочая таблица активации cumulative: строки активных данных после сложения и признак _hit."""
        columns = [Column("_hit", "smallint", True, "Служебная: 1 — ключ уже был в активных данных, 0 — новый")]
        columns += [Column(c.name, c.type, c.not_null, c.comment, c.field) for c in active.columns]
        t = TableDef(naming.schema("stg", self.model.schema_prefix), naming.table("work", adso.name), columns,
                     f"Рабочая таблица активации ADSO {adso.name} (cumulative), заполняется при прогоне",
                     primary_key=list(active.primary_key), bw_object=f"ADSO {adso.name}", role="work")
        self.add_table("adso", t)
        return t

    # --- трансформации ---
    def _transformations(self) -> None:
        names: dict[str, int] = {}
        for tr in self.trs:
            base = f"tr_{naming.db_name(tr.source.name)}__{naming.db_name(tr.target.name)}"
            names[base] = names.get(base, 0) + 1
            name = base if names[base] == 1 else f"{base}_{names[base]}"
            t = self.transforms.build(tr, name)
            text = self.env.get_template("clickhouse/transformation.sql.j2").render(t=t)
            preamble, steps = _steps(text)
            self.add("transformations", "steps", qualified(t.schema, name), render_steps(preamble, steps),
                     bw_object=f"Трансформация {tr.source.name} → {tr.target.name}", stub=list(t.stub),
                     ref=tr.id, steps=steps)


__all__ = ["ClickHouseGenerator", "ClickHouseTypes", "render_steps"]
