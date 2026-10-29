"""Пакет SQL: все объекты в порядке развёртывания и файлы out/sql (ТЗ, ФП-5.10–5.12).

Пакет строится только из модели и правил, без времени и случайных значений, поэтому
одинаковые входные данные дают побайтно одинаковые файлы (ФП-5.11).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from dbtransfer import __version__, naming
from dbtransfer.analysis.graph import scope_from
from dbtransfer.generator.sql import ident, literal, qualified, sql_comment
from dbtransfer.generator.tables import Column, TableBuilder, TableDef
from dbtransfer.generator.transform import TransformBuilder, TransformSql
from dbtransfer.generator.types import TypeRules, is_key_figure
from dbtransfer.model import Adso, AdsoKind, InfoObjectKind, Model, RuleKind

DIALECTS = ("greenplum", "postgres")
LAYERS = ("stg", "md", "dwh", "mart", "meta")
# Группа -> файл и заголовок, в порядке развёртывания (ФП-6.1)
GROUPS = {
    "schemas": ("010_schemas.sql", "Схемы слоёв"),
    "meta": ("020_meta.sql", "Служебные таблицы"),
    "md": ("030_md.sql", "Справочники: атрибуты и тексты признаков"),
    "datasources": ("040_datasources.sql", "Входные таблицы DataSource"),
    "adso": ("050_adso.sql", "Таблицы ADSO"),
    "activation": ("060_activation.sql", "Функции активации ADSO"),
    "transformations": ("070_transformations.sql", "Трансформации"),
}
SEQUENCES = ("load_id_seq", "deploy_id_seq", "reconcile_run_seq")


@dataclass
class SqlObject:
    group: str
    kind: str  # schema, sequence, table, function
    name: str  # полное имя в базе
    sql: str
    bw_object: str = ""
    has_data: bool = False
    stub: list[str] = field(default_factory=list)  # причины заготовки (ФП-5.9)
    ref: str = ""  # ADSO для функции активации, id трансформации


@dataclass
class Package:
    model_name: str
    model_hash: str
    dialect: str
    schema_prefix: str = "dbt"
    objects: list[SqlObject] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def meta_schema(self) -> str:
        return naming.schema("meta", self.schema_prefix)

    @property
    def data_schemas(self) -> list[str]:
        """Схемы с данными: всё, кроме служебной."""
        return [naming.schema(layer, self.schema_prefix) for layer in LAYERS if layer != "meta"]

    @property
    def sql_hash(self) -> str:
        """Хеш всего пакета: меняется при изменении модели, правил или версии генератора."""
        return hashlib.sha256(self.files()["deploy_all.sql"].encode("utf-8")).hexdigest()[:16]

    @property
    def stubs(self) -> list[SqlObject]:
        return [o for o in self.objects if o.stub]

    @property
    def sql_lines(self) -> int:
        """Строк SQL в файлах пакета без deploy_all.sql и drop_all.sql (метрика ФП-3.7)."""
        return sum(text.count("\n") for name, text in self.files().items() if name[0].isdigit())

    @property
    def db_objects(self) -> int:
        """Объектов базы: схемы, таблицы, функции и последовательности (метрика ФП-3.7)."""
        return sum(len(SEQUENCES) if o.kind == "sequence" else 1 for o in self.objects)

    def counts(self) -> dict[str, int]:
        """Число объектов базы по видам: для сводки и метрик ФП-3.7."""
        result: dict[str, int] = {}
        for o in self.objects:
            result[o.kind] = result.get(o.kind, 0) + 1
        return result

    def function_for(self, ref: str, group: str) -> SqlObject | None:
        return next((o for o in self.objects if o.group == group and o.ref == ref), None)

    def header(self, title: str) -> str:
        return (f"-- {title}\n"
                f"-- Сгенерировано DB-transfer {__version__} (dbtransfer generate), диалект {self.dialect}.\n"
                f"-- Модель: {self.model_name or 'без названия'}, хеш {self.model_hash}. "
                "Файл перезаписывается при генерации, правки вносите в модель и правила.\n")

    def files(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for group, (file_name, title) in GROUPS.items():
            parts = [o.sql for o in self.objects if o.group == group]
            body = "\n".join(parts) if parts else "-- Объектов нет\n"
            result[file_name] = self.header(title) + "\n" + body
        deploy = [self.header("Весь пакет одним файлом: psql -f deploy_all.sql"), "\\set ON_ERROR_STOP on\n"]
        for file_name in sorted(result):
            deploy.append(f"\n-- ===== {file_name} =====\n\n" + result[file_name].split("\n", 3)[3])
        result["deploy_all.sql"] = "".join(deploy)
        schemas = [o.name for o in self.objects if o.kind == "schema"]
        result["drop_all.sql"] = (self.header("Удаление всех схем прототипа вместе с данными и журналами")
                                  + "\n" + "".join(f"DROP SCHEMA IF EXISTS {s} CASCADE;\n" for s in reversed(schemas)))
        return result

    def write(self, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.sql"):
            old.unlink()
        written = []
        for name, text in self.files().items():
            path = out_dir / name
            path.write_bytes(text.encode("utf-8"))  # \n и UTF-8 на любой ОС: файлы одинаковы везде
            written.append(path)
        return written


def _environment() -> Environment:
    env = Environment(loader=PackageLoader("dbtransfer.generator", "templates"), undefined=StrictUndefined,
                      trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True, autoescape=False)
    env.filters["sql_literal"] = literal
    env.filters["sql_comment"] = sql_comment
    return env


class Generator:
    def __init__(self, model: Model, rules: TypeRules, dialect: str = "greenplum", scope: str | None = None):
        if dialect not in DIALECTS:
            raise ValueError(f"Диалект {dialect} неизвестен, допустимо: {', '.join(DIALECTS)}")
        self.model = model
        self.rules = rules
        self.dialect = dialect
        self.env = _environment()
        self.tables = TableBuilder(model, rules)
        self.transforms = TransformBuilder(model, self.tables)
        self.package = Package(model.name, model.content_hash(), dialect, model.schema_prefix)
        self._select_scope(scope)

    def _select_scope(self, scope: str | None) -> None:
        m = self.model
        if scope is None:
            self.adsos = sorted(m.adsos.values(), key=lambda a: a.name)
            self.datasources = sorted(m.datasources.values(), key=lambda d: d.ref.key)
            self.trs = sorted(m.transformations.values(), key=lambda t: (t.source.key, t.target.key, t.id))
            used = set(m.info_objects)
        else:
            s = scope_from(m, scope)
            keys = {r.key for r in s.objects}
            self.adsos = sorted((a for a in m.adsos.values() if a.name in keys), key=lambda a: a.name)
            self.datasources = sorted((d for d in m.datasources.values() if d.ref.key in keys),
                                      key=lambda d: d.ref.key)
            self.trs = sorted((m.transformations[t] for t in s.transformations),
                              key=lambda t: (t.source.key, t.target.key, t.id))
            used = set(s.info_objects) | {f.info_object for a in self.adsos for f in a.fields if f.info_object}
        # Справочники: признаки потока и признаки, чьи атрибуты читаются в трансформациях
        used |= {r.param.split("-")[0] for t in self.trs for r in t.rules
                 if r.kind == RuleKind.READ_MD and r.param and "-" in r.param}
        used |= {a for n in list(used) if n in m.info_objects for a in m.info_objects[n].attributes}
        self.characteristics = sorted(
            (i for n, i in m.info_objects.items() if n in used and i.kind == InfoObjectKind.CHARACTERISTIC
             and (i.has_master_data or i.attributes or i.has_texts)), key=lambda i: i.name)

    # --- сборка ---
    def add(self, group: str, kind: str, name: str, sql: str, **kw) -> None:
        self.package.objects.append(SqlObject(group, kind, name, sql, **kw))

    def table_sql(self, t: TableDef) -> str:
        return self.env.get_template("table.sql.j2").render(t=t, greenplum=self.dialect == "greenplum")

    def add_table(self, group: str, t: TableDef) -> None:
        self.add(group, "table", t.qname, self.table_sql(t), bw_object=t.bw_object, has_data=True)

    def build(self) -> Package:
        prefix = self.model.schema_prefix
        for layer in LAYERS:
            name = ident(naming.schema(layer, prefix))
            self.add("schemas", "schema", name, f"CREATE SCHEMA IF NOT EXISTS {name};\n")
        self._meta()
        for iobj in self.characteristics:
            for t in self.tables.md_tables(iobj):
                self.add_table("md", t)
        for ds in self.datasources:
            self.add_table("datasources", self.tables.datasource_table(ds))
        for adso in self.adsos:
            for role in self.tables.adso_roles(adso):
                self.add_table("adso", self.tables.adso_table(adso, role))
        for adso in self.adsos:
            self._activation(adso)
        self._transformations()
        for datatype, fields in sorted(self.rules.unknown.items()):
            self.package.warnings.append(f"Тип {datatype} нет в type_rules.yaml, взят {self.rules.default_type}: "
                                         + ", ".join(sorted(set(fields))))
        return self.package

    def _meta(self) -> None:
        meta = naming.schema("meta", self.model.schema_prefix)
        sql = self.env.get_template("meta.sql.j2").render(schema=meta, schema_sql=ident(meta), sequences=SEQUENCES)
        self.add("meta", "sequence", ", ".join(qualified(meta, s) for s in SEQUENCES), sql)

        def col(name: str, tp: str, not_null: bool, comment: str) -> Column:
            return Column(name, tp, not_null, comment)

        defs = [
            TableDef(meta, "load_log", [
                col("load_id", "bigint", True, "Номер загрузки (последовательность load_id_seq)"),
                col("object_name", "varchar(60)", True, "Объект BW, в который загружен файл"),
                col("file_name", "text", False, "Имя загруженного файла"),
                col("file_hash", "varchar(64)", False, "SHA-256 файла"),
                col("rows_loaded", "bigint", False, "Число загруженных строк"),
                col("status", "varchar(20)", True, "loaded, activated или failed"),
                col("loaded_by", "varchar(128)", False, "Пользователь ОС"),
                col("loaded_at", "timestamp", False, "Время загрузки"),
                col("activated_at", "timestamp", False, "Время активации"),
                col("message", "text", False, "Итоги шагов прогона или текст ошибки"),
            ], "Журнал загрузок (ТЗ, 4.3.6)", primary_key=["load_id"], distributed_by=["load_id"]),
            TableDef(meta, "deploy_log", [
                col("deploy_id", "bigint", True, "Номер развёртывания (последовательность deploy_id_seq)"),
                col("model_hash", "varchar(64)", False, "Хеш модели"),
                col("object_name", "text", False, "Объект базы"),
                col("action", "varchar(20)", False, "create, recreate, skip"),
                col("status", "varchar(20)", False, "ok или error"),
                col("message", "text", False, "Текст ошибки"),
                col("deployed_by", "varchar(128)", False, "Пользователь ОС"),
                col("deployed_at", "timestamp", False, "Время развёртывания"),
            ], "Журнал развёртываний (ТЗ, 4.3.6)", distributed_by=["deploy_id"]),
            TableDef(meta, "reconcile_result", [
                col("run_id", "bigint", True, "Номер сверки (последовательность reconcile_run_seq)"),
                col("object_name", "varchar(60)", False, "Сверяемый объект BW"),
                col("etalon_file", "text", False, "Файл эталона"),
                col("key_values", "jsonb", False, "Значения ключа строки"),
                col("measure", "varchar(60)", False, "Показатель"),
                col("value_bw", "numeric", False, "Значение в эталоне"),
                col("value_adb", "numeric", False, "Значение в базе"),
                col("diff", "numeric", False, "Разница"),
                col("status", "varchar(30)", False, "совпало, расхождение, нет в ADB, нет в эталоне"),
                col("run_at", "timestamp", False, "Время сверки"),
            ], "Результаты сверок с эталоном (ТЗ, 4.3.6)", distributed_by=["run_id"]),
        ]
        for t in defs:
            self.add_table("meta", t)

    def _activation(self, adso: Adso) -> None:
        if adso.kind not in (AdsoKind.STANDARD, AdsoKind.CUMULATIVE):
            return
        keys = self.tables.adso_keys(adso)
        if not keys:
            self.package.warnings.append(f"У ADSO {adso.name} нет ключа: функция активации не создана")
            return
        active = self.tables.adso_table(adso, "active")
        inbound = self.tables.adso_table(adso, "inbound")
        fields = [c for c in active.columns if c.field is not None]
        key_cols = [ident(naming.column(f.name, f.info_object)) for f in keys]
        load_log = qualified(naming.schema("meta", self.model.schema_prefix), "load_log")
        fn_name = f"activate_{naming.db_name(adso.name)}"
        fn = qualified(active.schema, fn_name)
        ctx = dict(adso=adso.name, fn=fn, inbound=inbound.qname, active=active.qname, load_log=load_log,
                   columns=[c.sql_name for c in fields])
        if adso.kind == AdsoKind.STANDARD:
            change_log = self.tables.adso_table(adso, "change_log")
            dedup = (f"SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY "
                     f"{', '.join('i.' + k for k in key_cols)} ORDER BY i.record_no DESC) AS rn "
                     f"FROM {inbound.qname} i WHERE i.load_id = p_load_id) x WHERE x.rn = 1")
            before = [f"-a.{c.sql_name}" if is_key_figure(self.model, c.field) else f"a.{c.sql_name}"
                      for c in fields]
            sql = self.env.get_template("activation_standard.sql.j2").render(
                **ctx, change_log=change_log.qname, dedup=dedup, before=before,
                after=[f"d.{c.sql_name}" for c in fields], first_key=key_cols[0], key_list=", ".join(key_cols),
                join_ad=" AND ".join(f"a.{k} = d.{k}" for k in key_cols),
                comment=f"BW: активация ADSO {adso.name} (standard): перезапись и журнал изменений")
        else:
            measures = [c.sql_name for c in fields if c.sql_name not in key_cols]
            grouped = (f"SELECT {', '.join('i.' + k for k in key_cols)}"
                       + "".join(f", sum(i.{m}) AS {m}" for m in measures)
                       + f" FROM {inbound.qname} i WHERE i.load_id = p_load_id GROUP BY "
                       + ", ".join("i." + k for k in key_cols))
            sql = self.env.get_template("activation_cumulative.sql.j2").render(
                **ctx, measures=measures, grouped=grouped,
                sums=[f"{m} = coalesce(a.{m}, 0) + coalesce(g.{m}, 0)" for m in measures],
                g_columns=[f"g.{c.sql_name}" for c in fields],
                join_ag=" AND ".join(f"a.{k} = g.{k}" for k in key_cols),
                comment=f"BW: активация ADSO {adso.name} (cumulative): суммирование")
        self.add("activation", "function", f"{fn}(bigint)", sql, bw_object=f"ADSO {adso.name}", ref=adso.name)

    def _transformations(self) -> None:
        names: dict[str, int] = {}
        for tr in self.trs:
            base = f"tr_{naming.db_name(tr.source.name)}__{naming.db_name(tr.target.name)}"
            names[base] = names.get(base, 0) + 1
            fn_name = base if names[base] == 1 else f"{base}_{names[base]}"
            t: TransformSql = self.transforms.build(tr, fn_name)
            sql = self.env.get_template("transformation.sql.j2").render(t=t)
            self.add("transformations", "function", f"{t.fn}(bigint)", sql,
                     bw_object=f"Трансформация {tr.source.name} → {tr.target.name}", stub=list(t.stub), ref=tr.id)


def generate(model: Model, rules: TypeRules | None = None, dialect: str = "greenplum",
             scope: str | None = None) -> Package:
    return Generator(model, rules or TypeRules.load(), dialect, scope).build()


__all__ = ["DIALECTS", "GROUPS", "Generator", "Package", "SqlObject", "generate"]
