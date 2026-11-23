"""Модель метаданных хранилища SAP BW (ТЗ, раздел 4.3.2).

Модель не зависит ни от источника, ни от целевой СУБД: её строит импорт CSV-выгрузок
(dbtransfer.importers.bw_csv), а отчёт, lineage и генерация SQL работают только с ней.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# Версия формата model.json. Меняется при несовместимых изменениях классов ниже.
SCHEMA_VERSION = 1


class InfoObjectKind(StrEnum):
    CHARACTERISTIC = "CHA"
    KEY_FIGURE = "KYF"
    TIME = "TIM"
    UNIT = "UNI"


class AdsoKind(StrEnum):
    STAGING = "staging"
    STANDARD = "standard"
    CUMULATIVE = "cumulative"
    DIRECT_UPDATE = "direct_update"


class ObjectType(StrEnum):
    """Типы объектов так, как они записаны в RSTRAN и RSBKDTP, плюс объекты только для отчёта."""

    DATASOURCE = "RSDS"
    ADSO = "ADSO"
    INFOOBJECT = "IOBJ"
    COMPOSITE_PROVIDER = "HCPR"
    QUERY = "ELEM"
    HIERARCHY = "HIER"
    PROCESS_CHAIN = "CHAIN"
    CLASSIC_DSO = "ODSO"
    DTP = "DTPA"
    # Объекты реляционных источников (Oracle, MS SQL Server): только для отчёта
    VIEW = "VIEW"
    DB_ROUTINE = "PROC"
    TABLE = "TABL"  # таблица, переносимая 1:1 (DbTable)


class SourceKind(StrEnum):
    """Из чего построена модель: выгрузки SAP BW или словарь реляционной СУБД."""

    SAP_BW = "sap_bw"
    ORACLE = "oracle"
    MSSQL = "mssql"


SOURCE_LABELS = {SourceKind.SAP_BW: "BW", SourceKind.ORACLE: "Oracle", SourceKind.MSSQL: "MS SQL"}


class RuleKind(StrEnum):
    DIRECT = "DIRECT"
    CONSTANT = "CONSTANT"
    FORMULA = "FORMULA"
    TIME = "TIME"
    READ_MD = "READ_MD"
    ROUTINE = "ROUTINE"
    UNKNOWN = "UNKNOWN"


class RoutineKind(StrEnum):
    START = "start"
    END = "end"
    EXPERT = "expert"
    FIELD = "field"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Issue(BaseModel):
    """Замечание импорта: файл, строка, объект и что не так."""

    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    object: str | None = None
    # Объект из-за этой проблемы не попал в модель (для списка «Не разобрано»)
    skipped: bool = False


class ObjectRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ObjectType
    name: str
    # DataSource уникален только вместе с исходной системой (LOGSYS)
    source_system: str | None = None

    @property
    def key(self) -> str:
        return f"{self.name}/{self.source_system}" if self.source_system else self.name

    def __str__(self) -> str:
        return self.key


class FieldDef(BaseModel):
    """Поле ADSO или DataSource. Тип ADB добавляет генератор (шаг 3)."""

    name: str  # InfoObject для ADSO, имя поля для DataSource
    position: int
    info_object: str | None = None
    bw_column: str | None = None  # имя колонки в таблице BW (DD03L), для ADSO
    datatype: str
    length: int
    decimals: int = 0
    is_key: bool = False
    nullable: bool = True
    comment: str = ""


class InfoObject(BaseModel):
    name: str
    kind: InfoObjectKind
    short_text: str = ""
    description: str = ""
    datatype: str = ""
    length: int = 0
    decimals: int = 0
    conversion_exit: str = ""
    has_master_data: bool = False
    has_texts: bool = False
    has_hierarchies: bool = False
    attributes: list[str] = Field(default_factory=list)
    # Только для показателей
    key_figure_type: str | None = None
    aggregation: str | None = None
    unit_info_object: str | None = None
    fixed_currency: str | None = None
    fixed_unit: str | None = None


class Adso(BaseModel):
    name: str
    kind: AdsoKind
    description: str = ""
    info_area: str = ""
    fields: list[FieldDef] = Field(default_factory=list)
    # Роль таблицы BW -> имя: inbound (1), active (2), change_log (3)
    bw_tables: dict[str, str] = Field(default_factory=dict)

    @property
    def ref(self) -> ObjectRef:
        return ObjectRef(type=ObjectType.ADSO, name=self.name)

    @property
    def key(self) -> list[str]:
        return [f.name for f in self.fields if f.is_key]

    def field(self, name: str) -> FieldDef | None:
        return next((f for f in self.fields if f.name == name), None)


class DataSource(BaseModel):
    name: str
    source_system: str
    source_type: str = ""  # FILE для плоского файла
    description: str = ""
    delta_process: str = ""
    fields: list[FieldDef] = Field(default_factory=list)

    @property
    def is_flat_file(self) -> bool:
        return self.source_type == "FILE"

    @property
    def ref(self) -> ObjectRef:
        return ObjectRef(type=ObjectType.DATASOURCE, name=self.name, source_system=self.source_system)

    def field(self, name: str) -> FieldDef | None:
        return next((f for f in self.fields if f.name == name), None)


class Partition(BaseModel):
    """Секция: для RANGE — верхняя граница, для LIST — список значений. Значения — литералы SQL целевой базы
    (TIMESTAMP '2027-01-01 00:00:00', 100, 'RU'); MAXVALUE и DEFAULT — секция по умолчанию."""

    name: str
    values: list[str] = Field(default_factory=list)

    @property
    def is_default(self) -> bool:
        return not self.values or self.values[0] in ("MAXVALUE", "DEFAULT")


class Partitioning(BaseModel):
    """Секционирование таблицы источника."""

    kind: str  # RANGE или LIST; HASH в Greenplum заменяется распределением и сюда не попадает
    columns: list[str] = Field(default_factory=list)
    partitions: list[Partition] = Field(default_factory=list)
    # RANGE: граница входит в нижнюю секцию (MS SQL RANGE LEFT); по умолчанию — в верхнюю (Oracle, RANGE RIGHT)
    upper_inclusive: bool = False
    note: str = ""  # что не перенесено: интервальное создание секций, подсекции


class DbTable(BaseModel):
    """Таблица реляционного источника, переносимая 1:1: одна таблица в целевой базе с исходным именем.
    Данные грузятся в неё напрямую из CSV (перезапись по ключу), без входных таблиц и активации."""

    schema_name: str  # схема в источнике
    name: str
    description: str = ""
    fields: list[FieldDef] = Field(default_factory=list)
    key_kind: str = ""  # первичный, уникальный или пусто
    partitioning: Partitioning | None = None
    # Колонки ключа с регистронезависимой сортировкой в источнике (MS SQL *_CI_*): сравниваются через lower()
    ci_columns: list[str] = Field(default_factory=list)

    @property
    def key_name(self) -> str:
        return f"{self.schema_name}.{self.name}"

    @property
    def ref(self) -> ObjectRef:
        return ObjectRef(type=ObjectType.TABLE, name=self.key_name)

    @property
    def key(self) -> list[str]:
        return [f.name for f in self.fields if f.is_key]

    def field(self, name: str) -> FieldDef | None:
        return next((f for f in self.fields if f.name == name), None)


class Routine(BaseModel):
    kind: RoutineKind
    code_id: str
    code: str | None = None  # None: кода нет в выгрузке RSAABAP

    @property
    def lines(self) -> int:
        return len(self.code.splitlines()) if self.code else 0


class Rule(BaseModel):
    rule_id: int
    kind: RuleKind
    source_fields: list[str] = Field(default_factory=list)
    target_fields: list[str] = Field(default_factory=list)
    # Константа, текст формулы или читаемый атрибут (0MATERIAL-0MATL_GROUP)
    param: str | None = None
    routine: Routine | None = None
    raw_kind: str = ""  # вид правила как в выгрузке, для неразобранных


class Transformation(BaseModel):
    id: str
    source: ObjectRef
    target: ObjectRef
    rules: list[Rule] = Field(default_factory=list)
    routines: list[Routine] = Field(default_factory=list)  # стартовая, конечная, экспертная

    @property
    def all_routines(self) -> list[Routine]:
        return self.routines + [r.routine for r in self.rules if r.routine]


class Dtp(BaseModel):
    name: str
    source: ObjectRef
    target: ObjectRef
    mode: str  # FULL или DELTA
    filter: str | None = None


class OtherObject(BaseModel):
    """Объекты, которые прототип только показывает в отчёте: CompositeProvider, запросы и т. п."""

    type: ObjectType
    name: str
    related: list[ObjectRef] = Field(default_factory=list)
    info: str = ""
    lines: int = 0  # строк кода (представления, процедуры), для трудоёмкости


class InactiveObject(BaseModel):
    type: str
    name: str
    versions: list[str]
    file: str


class Model(BaseModel):
    """Всё хранилище: объекты, потоки и замечания импорта (класс Project в ТЗ 4.3.2)."""

    schema_version: int = SCHEMA_VERSION
    name: str = ""
    bw_version: str = ""
    source_kind: SourceKind = SourceKind.SAP_BW
    schema_prefix: str = "dbt"
    source: str = ""
    imported_at: datetime | None = None
    language: str = "R"
    info_objects: dict[str, InfoObject] = Field(default_factory=dict)
    adsos: dict[str, Adso] = Field(default_factory=dict)
    datasources: dict[str, DataSource] = Field(default_factory=dict)
    transformations: dict[str, Transformation] = Field(default_factory=dict)
    dtps: dict[str, Dtp] = Field(default_factory=dict)
    others: list[OtherObject] = Field(default_factory=list)
    # Таблицы реляционного источника, переносимые 1:1 (Oracle, MS SQL Server): «СХЕМА.ТАБЛИЦА» -> таблица
    tables: dict[str, DbTable] = Field(default_factory=dict)
    # Знаки после запятой у валют из TCURX (только валюты, у которых не 2 знака): JPY -> 0, KWD -> 3
    currency_decimals: dict[str, int] = Field(default_factory=dict)
    inactive: list[InactiveObject] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    # Сведения о прочитанных файлах для отчёта о проверке: имя -> свойства
    files: dict[str, dict[str, str | int]] = Field(default_factory=dict)

    def issue(self, severity: Severity, message: str, file: str | None = None, line: int | None = None,
              obj: str | None = None, skipped: bool = False) -> None:
        self.issues.append(Issue(severity=severity, message=message, file=file, line=line, object=obj,
                                 skipped=skipped))

    def object(self, ref: ObjectRef) -> Adso | DataSource | InfoObject | DbTable | None:
        store = {ObjectType.ADSO: self.adsos, ObjectType.DATASOURCE: self.datasources,
                 ObjectType.INFOOBJECT: self.info_objects, ObjectType.TABLE: self.tables}.get(ref.type, {})
        return store.get(ref.key)

    def find(self, name: str) -> Adso | DataSource | InfoObject | OtherObject | None:
        """Объект по имени, как его вводит пользователь: ZSD_A01, ZSALES_FF или ZSALES_FF/FLATFILE."""
        if name in self.adsos:
            return self.adsos[name]
        if name in self.datasources:
            return self.datasources[name]
        matches = [ds for ds in self.datasources.values() if ds.name == name]
        if len(matches) == 1:
            return matches[0]
        if name in self.info_objects:
            return self.info_objects[name]
        table = self.find_table(name)
        if table is not None:
            return table
        return next((o for o in self.others if o.name == name), None)

    def find_table(self, name: str) -> DbTable | None:
        """Таблица 1:1 по имени SALES.ORDERS, SALES_ORDERS или ORDERS (если однозначно), без учёта регистра."""
        upper = name.upper()
        for key, t in self.tables.items():
            if upper in (key.upper(), f"{t.schema_name}_{t.name}".upper()):
                return t
        matches = [t for t in self.tables.values() if t.name.upper() == upper]
        return matches[0] if len(matches) == 1 else None

    @property
    def is_bw(self) -> bool:
        return self.source_kind == SourceKind.SAP_BW

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS[self.source_kind]

    def find_datasource(self, name: str) -> DataSource | None:
        """DataSource по ключу ZSALES_FF/FLATFILE или по имени, если оно однозначно. В моделях из словаря
        Oracle и MS SQL Server входная таблица и ADSO называются одинаково, find вернул бы ADSO."""
        if name in self.datasources:
            return self.datasources[name]
        matches = [ds for ds in self.datasources.values() if ds.name == name]
        return matches[0] if len(matches) == 1 else None

    def content_hash(self) -> str:
        """Хеш содержимого без времени импорта: одинаковые выгрузки дают одинаковый хеш.
        Поля, добавленные для реляционных источников, в хеш не входят: хеш моделей BW не меняется."""
        exclude: dict = {"imported_at": True, "source": True, "files": True, "source_kind": True,
                         "others": {"__all__": {"lines"}}}
        if not self.tables:
            exclude["tables"] = True
        if not self.currency_decimals:
            exclude["currency_decimals"] = True
        data = self.model_dump_json(exclude=exclude)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Model:
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"{path}: формат модели версии {version}, а приложение понимает версию {SCHEMA_VERSION}. "
                "Выполните импорт заново: dbtransfer import"
            )
        return cls.model_validate(raw)
