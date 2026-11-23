"""DDL таблиц: справочники, входные таблицы DataSource, таблицы ADSO (ТЗ, ФП-5.1–5.6)."""

from __future__ import annotations

from dataclasses import dataclass, field

from dbtransfer import naming
from dbtransfer.generator.partitions import partition_sql
from dbtransfer.generator.sql import ident, qualified
from dbtransfer.generator.types import RulesError, TypeRules, is_key_figure
from dbtransfer.model import Adso, AdsoKind, DataSource, DbTable, FieldDef, InfoObject, Model

ROLE_TEXT = {"inbound": "входящая таблица", "active": "активные данные", "change_log": "журнал изменений"}
KIND_TEXT = {AdsoKind.STAGING: "staging", AdsoKind.STANDARD: "standard", AdsoKind.CUMULATIVE: "cumulative",
             AdsoKind.DIRECT_UPDATE: "direct update"}

# Служебные колонки входящих таблиц и журнала изменений (ТЗ, 4.3.4)
LOAD_ID = ("load_id", "bigint", "Служебная: номер загрузки (meta.load_log)")
RECORD_NO = ("record_no", "bigint", "Служебная: номер записи в загрузке")
RECORDMODE = ("recordmode", "char(1)", "Служебная: N новая запись, X before image, ' ' after image")

# Тексты признака: как в таблицах текстов BW (краткий, средний, длинный)
TEXT_COLUMNS = (("txtsh", "varchar(20)", "Краткий текст"), ("txtmd", "varchar(40)", "Средний текст"),
                ("txtlg", "varchar(60)", "Длинный текст"))


@dataclass
class Column:
    name: str
    type: str
    not_null: bool = False
    comment: str = ""
    field: FieldDef | None = None  # поле BW, если колонка не служебная

    @property
    def sql_name(self) -> str:
        return ident(self.name)


@dataclass
class TableDef:
    schema: str
    name: str
    columns: list[Column]
    comment: str
    storage: str = "heap"
    storage_options: str = ""
    primary_key: list[str] = field(default_factory=list)
    distributed_by: list[str] = field(default_factory=list)  # пусто — DISTRIBUTED RANDOMLY
    bw_object: str = ""
    role: str = ""
    partition_sql: str = ""  # PARTITION BY ... после DISTRIBUTED BY (Greenplum) или после колонок (PostgreSQL)
    after_sql: str = ""  # операторы после CREATE TABLE: секции PostgreSQL (PARTITION OF)

    @property
    def qname(self) -> str:
        return qualified(self.schema, self.name)

    @property
    def width(self) -> int:
        return max(len(c.sql_name) for c in self.columns)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


def type_text(f: FieldDef) -> str:
    size = f"{f.length},{f.decimals}" if f.decimals else str(f.length)
    return f"{f.datatype} {size}" if f.length else f.datatype


def field_comment(obj: str, f: FieldDef, iobj: InfoObject | None, model: Model | None = None) -> str:
    if model is not None and not model.is_bw:
        # Реляционный источник: в комментарии поля уже тип источника и описание колонки
        return f"{model.source_label}: {obj}.{f.name}" + (f" {f.comment}" if f.comment else "")
    text = f.comment or (iobj.description or iobj.short_text if iobj else "")
    return f"BW: {obj}.{f.name} ({type_text(f)})" + (f". {text}" if text else "")


def iobj_field(iobj: InfoObject) -> FieldDef:
    return FieldDef(name=iobj.name, position=0, info_object=iobj.name, datatype=iobj.datatype,
                    length=iobj.length, decimals=iobj.decimals, is_key=True, nullable=False)


class TableBuilder:
    def __init__(self, model: Model, rules: TypeRules, dialect: str = "greenplum"):
        self.model = model
        self.rules = rules
        self.dialect = dialect
        self.warnings: list[str] = []

    def _apply_storage(self, table: TableDef, role: str, obj: str) -> None:
        table.storage = self.rules.storage_for(role, obj)
        table.storage_options = self.rules.storage_options.get(table.storage, "")
        if table.storage != "heap" and table.primary_key:
            raise RulesError(f"type_rules.yaml: у {obj} таблица «{ROLE_TEXT.get(role, role)}» append-optimized, "
                             "а у таких таблиц в Greenplum не может быть PRIMARY KEY. Оставьте для неё heap")

    def _apply_distribution(self, table: TableDef, obj: str, default: list[str]) -> None:
        wanted = self.rules.distributed_by(obj)
        if wanted is None:
            table.distributed_by = default
            return
        columns = []
        for name in wanted:
            col = table.column(name) or table.column(naming.db_name(name))
            if col is None:
                raise RulesError(f"type_rules.yaml, overrides.{obj}.distributed_by: поля {name} нет в таблице "
                                 f"{table.qname}")
            columns.append(col.sql_name)
        if table.primary_key and not set(columns) <= set(table.primary_key):
            raise RulesError(f"type_rules.yaml, overrides.{obj}.distributed_by: ключ распределения должен входить "
                             f"в PRIMARY KEY {table.qname} ({', '.join(table.primary_key)}) — ограничение Greenplum")
        table.distributed_by = columns

    def field_columns(self, obj: str, fields: list[FieldDef], adso: bool, keys: set[str]) -> list[Column]:
        result = []
        for f in sorted(fields, key=lambda x: x.position):
            iobj = self.model.info_objects.get(f.info_object or f.name)
            name = naming.column(f.name, f.info_object if adso else None)
            result.append(Column(name=name, type=self.rules.column_type(f, obj), not_null=f.name in keys,
                                 comment=field_comment(obj, f, iobj, self.model), field=f))
        return result

    # --- справочники ---
    def md_tables(self, iobj: InfoObject) -> list[TableDef]:
        prefix = self.model.schema_prefix
        md = naming.schema("md", prefix)
        key = iobj_field(iobj)
        key_col = Column(naming.column(iobj.name), self.rules.column_type(key, iobj.name), True,
                         field_comment(iobj.name, key, iobj), key)
        tables = []
        if iobj.has_master_data or iobj.attributes:
            columns = [key_col]
            for attr in iobj.attributes:
                attr_obj = self.model.info_objects.get(attr)
                f = iobj_field(attr_obj) if attr_obj else FieldDef(name=attr, position=0, datatype="", length=0)
                f.is_key = False
                columns.append(Column(naming.column(attr), self.rules.column_type(f, iobj.name), False,
                                      f"BW: атрибут {attr} признака {iobj.name}"
                                      + (f". {attr_obj.description}" if attr_obj and attr_obj.description else ""),
                                      f))
            t = TableDef(md, naming.table("md_attr", iobj.name), columns,
                         f"BW: атрибуты признака {iobj.name}. {iobj.description}".strip(),
                         primary_key=[key_col.sql_name], bw_object=f"InfoObject {iobj.name}", role="md_attr")
            self._apply_storage(t, "md", iobj.name)
            self._apply_distribution(t, iobj.name, [key_col.sql_name])
            tables.append(t)
        if iobj.has_texts:
            columns = [key_col, Column("langu", "char(1)", True, "Язык текста (R русский, E английский)")]
            columns += [Column(n, tp, False, c) for n, tp, c in TEXT_COLUMNS]
            t = TableDef(md, naming.table("md_text", iobj.name), columns,
                         f"BW: тексты признака {iobj.name}. {iobj.description}".strip(),
                         primary_key=[key_col.sql_name, "langu"], bw_object=f"InfoObject {iobj.name}", role="md_text")
            self._apply_storage(t, "md", iobj.name)
            self._apply_distribution(t, iobj.name, [key_col.sql_name])
            tables.append(t)
        return tables

    # --- DataSource ---
    def datasource_table(self, ds: DataSource) -> TableDef:
        columns = [Column(n, tp, True, c) for n, tp, c in (LOAD_ID, RECORD_NO)]
        columns += self.field_columns(ds.name, ds.fields, adso=False, keys=set())
        kind = "плоский файл" if ds.is_flat_file else (ds.source_type or "источник")
        comment = (f"BW: DataSource {ds.name} ({kind}, система {ds.source_system}), входная таблица. "
                   f"{ds.description}".strip())
        if not self.model.is_bw:
            comment = f"{self.model.source_label}: {ds.description}, входная таблица для CSV с данными (система " \
                      f"{ds.source_system})"
        t = TableDef(naming.schema("stg", self.model.schema_prefix), naming.table("datasource", ds.name), columns,
                     comment,
                     bw_object=f"DataSource {ds.ref.key}", role="datasource")
        self._apply_storage(t, "datasource", ds.name)
        self._apply_distribution(t, ds.name, [])
        return t

    # --- ADSO ---
    def adso_keys(self, adso: Adso) -> list[FieldDef]:
        """Ключ активных данных: у cumulative — все признаки (ТЗ, 4.3.4), у прочих — ключ ADSO."""
        fields = sorted(adso.fields, key=lambda f: f.position)
        if adso.kind == AdsoKind.CUMULATIVE:
            return [f for f in fields if not is_key_figure(self.model, f)]
        if adso.kind == AdsoKind.STAGING:
            return []
        return [f for f in fields if f.is_key]

    def adso_roles(self, adso: Adso) -> list[str]:
        return {AdsoKind.STAGING: ["inbound"], AdsoKind.STANDARD: ["inbound", "active", "change_log"],
                AdsoKind.CUMULATIVE: ["inbound", "active"], AdsoKind.DIRECT_UPDATE: ["active"]}[adso.kind]

    def adso_table(self, adso: Adso, role: str) -> TableDef:
        keys = self.adso_keys(adso)
        key_names = {f.name for f in keys}
        columns = []
        if role in ("inbound", "change_log"):
            columns = [Column(n, tp, True, c) for n, tp, c in (LOAD_ID, RECORD_NO, RECORDMODE)]
        columns += self.field_columns(adso.name, adso.fields, adso=True, keys=key_names)
        key_cols = [ident(naming.column(f.name, f.info_object)) for f in keys]
        bw_table = adso.bw_tables.get(role)
        comment = (f"BW: ADSO {adso.name} ({KIND_TEXT[adso.kind]}), {ROLE_TEXT[role]}"
                   + (f", таблица BW {bw_table}" if bw_table else "") + "." + (f" {adso.description}"
                                                                              if adso.description else ""))
        if not self.model.is_bw:
            source_table = f"{adso.info_area}.{adso.name.removeprefix(adso.info_area + '_')}"
            comment = f"{self.model.source_label}: таблица {source_table}, {ROLE_TEXT[role]}." + (
                f" {adso.description}" if adso.description and adso.description != source_table else "")
        t = TableDef(naming.table_schema(self.model, adso, role), naming.table(role, adso.name), columns, comment,
                     primary_key=key_cols if role == "active" else [], bw_object=f"ADSO {adso.name}", role=role)
        self._apply_storage(t, role, adso.name)
        self._apply_distribution(t, adso.name, key_cols[:1])
        return t

    # --- таблица 1:1 (Oracle, MS SQL Server) ---
    def db_table(self, t: DbTable) -> TableDef:
        obj = t.key_name
        columns = []
        for f in sorted(t.fields, key=lambda x: x.position):
            columns.append(Column(name=naming.db_name(f.name), type=self.rules.column_type(f, obj),
                                  not_null=f.is_key or not f.nullable,
                                  comment=f"{self.model.source_label}: {obj}.{f.name}"
                                          + (f" {f.comment}" if f.comment else ""), field=f))
        key_cols = [ident(naming.db_name(f.name)) for f in sorted(t.fields, key=lambda x: x.position) if f.is_key]
        part_cols: list[str] = []
        if t.partitioning is not None:
            part_cols = [ident(naming.db_name(c)) for c in t.partitioning.columns]
            if key_cols and not set(part_cols) <= set(key_cols):
                self.warnings.append(f"{obj}: первичный ключ ({', '.join(key_cols)}) не включает колонки "
                                     f"секционирования ({', '.join(part_cols)}), а Greenplum этого требует. "
                                     "Таблица секционирована, PRIMARY KEY не объявлен; перезапись по ключу "
                                     "при загрузке работает и без него")
                key_cols_pk: list[str] = []
            else:
                key_cols_pk = key_cols
        else:
            key_cols_pk = key_cols
        role = "table" if key_cols_pk else "table_nokey"
        comment = f"{self.model.source_label}: таблица {obj}" + (f". {t.description}" if t.description else "")
        table = TableDef(naming.db_table_schema(self.model, t), naming.db_table_name(t), columns, comment,
                         primary_key=key_cols_pk, bw_object=f"Таблица {obj}", role=role)
        table.storage = self.rules.storage_for(role, obj)
        if table.storage != "heap" and table.primary_key:
            table.storage = "heap"
        table.storage_options = self.rules.storage_options.get(table.storage, "")
        self._apply_distribution(table, obj, key_cols[:1])
        if t.partitioning is not None and part_cols:
            ps = partition_sql(t.partitioning, table.schema, table.name, part_cols, self.dialect == "greenplum")
            table.partition_sql, table.after_sql = ps.clause, ps.after
            self.warnings.extend(ps.warnings)
            if t.partitioning.note:
                self.warnings.append(f"{obj}: {t.partitioning.note}")
        return table
