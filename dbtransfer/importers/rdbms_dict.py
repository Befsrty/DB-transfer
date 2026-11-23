"""Общая часть импорта словаря реляционной СУБД (Oracle, MS SQL Server) в модель.

Модуль диалекта (oracle_dict.py, mssql_dict.py) читает свои представления словаря — из папки CSV
или напрямую из базы — и приводит их к описанию каталога: таблицы с колонками и ключами,
представления, процедуры, внешние ключи. Отсюда модель строится одинаково для всех СУБД:

* таблица SCHEMA.TABLE -> DataSource SCHEMA_TABLE (входная таблица, куда грузится CSV с данными)
  и ADSO SCHEMA_TABLE (целевая таблица): standard, если есть первичный или уникальный ключ,
  иначе staging (только входящая таблица, без активации);
* между ними — трансформация из прямых присвоений по всем колонкам и дельта-DTP;
* представления и процедуры — прочие объекты (только отчёт о сложности, без генерации SQL);
* внешние ключи и секционирование — сведения в замечаниях импорта (в Greenplum не переносятся).

Тип колонки источника переводится в тип модели (CHAR, DEC, INT4, DATS, TIMESTAMP, ...), дальше
работают обычные правила dbtransfer/rules/type_rules.yaml.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dbtransfer import naming
from dbtransfer.config import BwTable
from dbtransfer.importers.bw_csv import ImportFailed
from dbtransfer.importers.csv_reader import Row, TableFile, read_table
from dbtransfer.model import (
    Adso,
    AdsoKind,
    DataSource,
    Dtp,
    FieldDef,
    Model,
    ObjectRef,
    ObjectType,
    OtherObject,
    Rule,
    RuleKind,
    Severity,
    SourceKind,
    Transformation,
)

# Предел длины имени в PostgreSQL/Greenplum; длинные имена сервер молча обрезает
MAX_IDENTIFIER = 63
LONGEST_SUFFIX = "_changelog"
# Служебные колонки входных таблиц и журналов (generator/tables.py): колонка источника с таким именем мешает
SERVICE_COLUMNS = {"load_id", "record_no", "recordmode"}


class DictImportFailed(ImportFailed):
    """Словарь нельзя импортировать: нет папки, нет файлов или не удалось подключиться."""


@dataclass
class SourceColumn:
    name: str
    position: int
    native_type: str  # тип как в источнике, для комментария: NUMBER(10,2), nvarchar(50)
    datatype: str  # тип модели: CHAR, DEC, INT4, DATS, TIMESTAMP, ...
    length: int = 0
    decimals: int = 0
    nullable: bool = True
    comment: str = ""
    known: bool = True  # тип распознан; иначе колонка переносится как text


@dataclass
class SourceTable:
    schema: str
    name: str
    columns: list[SourceColumn] = field(default_factory=list)
    comment: str = ""
    key: list[str] = field(default_factory=list)
    key_kind: str = ""  # первичный, уникальный или пусто
    key_constraint: str = ""
    notes: list[str] = field(default_factory=list)  # сведения для замечаний импорта

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def model_name(self) -> str:
        return f"{self.schema}_{self.name}"


@dataclass
class SourceObject:
    """Представление, процедура, функция, пакет, триггер: в модели — OtherObject."""

    schema: str
    name: str
    type: ObjectType
    label: str  # «представление», «процедура», «пакет» ...
    lines: int = 0
    text: str = ""
    status: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class ForeignKey:
    name: str
    table: str  # SCHEMA.TABLE
    columns: list[str]
    ref_table: str  # SCHEMA.TABLE или пусто, если ссылка не найдена


@dataclass
class Catalog:
    tables: list[SourceTable] = field(default_factory=list)
    objects: list[SourceObject] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)


# --- чтение представлений словаря ---

def read_folder(folder: Path | str, specs: dict[str, BwTable], how: tuple[str, str], encoding: str | None = None,
                delimiter: str | None = None) -> dict[str, TableFile]:
    folder = Path(folder)
    if not folder.is_dir():
        raise DictImportFailed(f"Папка не найдена: {folder}")
    files = {name: read_table(folder, spec, encoding, delimiter, how) for name, spec in specs.items()}
    if not any(f.path for f in files.values()):
        first = next(iter(specs))
        raise DictImportFailed(f"В папке {folder} нет ни одной выгрузки словаря (ожидаются файлы вида {first}.csv)")
    return files


def _text(value: object) -> str:
    if value is None:
        return ""
    if hasattr(value, "read"):  # CLOB/LOB драйвера Oracle
        value = value.read()
    return str(value)


def read_connection(conn, queries: dict[str, str], specs: dict[str, BwTable]) -> dict[str, TableFile]:
    """Выполнить запросы словаря через DB-API-подключение (oracledb, pymssql) и вернуть строки
    в том же виде, что при чтении CSV. Ошибка в необязательном запросе — замечание, не отказ."""
    files: dict[str, TableFile] = {}
    for name, spec in specs.items():
        result = TableFile(table=name, encoding="из базы")
        sql = queries.get(name)
        if sql is None:
            files[name] = result
            continue
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                header = [d[0].upper() for d in cur.description]
                data = cur.fetchall()
            finally:
                cur.close()
        except Exception as e:  # noqa: BLE001 — драйверы бросают свои классы ошибок
            if spec.required:
                raise DictImportFailed(f"Запрос к {name} не выполнен: {e}") from e
            result.issue(Severity.WARNING, f"Запрос к {name} не выполнен, сведения пропущены: {e}")
            files[name] = result
            continue
        missing = [c for c in spec.required_columns if c not in header]
        if missing:
            raise DictImportFailed(f"Запрос к {name} не вернул колонки {', '.join(missing)}")
        result.rows = [Row(line=i, values={h: _text(v).rstrip() for h, v in zip(header, values, strict=True)})
                       for i, values in enumerate(data, start=2)]
        result.usable = True
        files[name] = result
    return files


def schema_filter(schemas: Iterable[str] | None, system_schemas: set[str]):
    """Функция «схема нужна»: список схем пользователя или все, кроме системных."""
    wanted = {s.strip().upper() for s in schemas or [] if s.strip()}
    if wanted:
        return lambda s: s.upper() in wanted
    return lambda s: s.upper() not in system_schemas


def sql_in_list(schemas: Iterable[str]) -> str:
    """Список схем для IN (...) в тексте запроса словаря: значения в кавычках, кавычки удвоены."""
    return ", ".join("'" + s.strip().replace("'", "''") + "'" for s in schemas if s.strip())


def to_int(value: str) -> int:
    value = (value or "").strip()
    try:
        return int(float(value)) if value else 0
    except ValueError:
        return 0


def count_lines(text: str) -> int:
    return len(text.strip().splitlines()) if text.strip() else 0


# --- построение модели ---

def _check_names(model: Model, t: SourceTable) -> None:
    longest = naming.db_name(t.model_name) + LONGEST_SUFFIX
    if len(longest) > MAX_IDENTIFIER:
        model.issue(Severity.WARNING, f"Имя {longest} длиннее {MAX_IDENTIFIER} символов: Greenplum его обрежет. "
                                      "Сократите имя таблицы в шаблонах naming.yaml или переименуйте при переносе",
                    obj=t.full_name)
    seen: dict[str, str] = {}
    for c in t.columns:
        db = naming.db_name(c.name)
        if db in seen:
            model.issue(Severity.ERROR, f"Колонки {seen[db]} и {c.name} дают одно имя {db} в целевой базе",
                        obj=t.full_name)
        if db in SERVICE_COLUMNS:
            model.issue(Severity.ERROR, f"Колонка {c.name} совпадает со служебной колонкой {db} входных таблиц: "
                                        "переименуйте её при выгрузке (и в словаре, и в данных)", obj=t.full_name)
        seen[db] = c.name


def _field(c: SourceColumn, is_key: bool) -> FieldDef:
    comment = c.native_type + (f". {c.comment}" if c.comment else "")
    return FieldDef(name=c.name, position=c.position, bw_column=c.name, datatype=c.datatype, length=c.length,
                    decimals=c.decimals, is_key=is_key, nullable=c.nullable and not is_key, comment=comment)


def _add_table(model: Model, t: SourceTable, system: str, source_type: str) -> None:
    name = t.model_name
    if name in model.adsos:
        model.issue(Severity.ERROR, f"Таблица {t.full_name} совпадает по имени {name} с другой таблицей, пропущена",
                    obj=t.full_name, skipped=True)
        return
    if not t.columns:
        model.issue(Severity.WARNING, f"У таблицы {t.full_name} нет колонок в словаре, пропущена",
                    obj=t.full_name, skipped=True)
        return
    _check_names(model, t)
    columns = sorted(t.columns, key=lambda c: c.position)
    names = {c.name for c in columns}
    key = [k for k in t.key if k in names]
    if len(key) != len(t.key):
        model.issue(Severity.WARNING, f"Ключ {t.key_constraint} таблицы {t.full_name}: колонок "
                                      f"{', '.join(k for k in t.key if k not in names)} нет среди колонок таблицы",
                    obj=t.full_name)
    for c in columns:
        if not c.known:
            model.issue(Severity.WARNING, f"Тип {c.native_type} колонки {t.full_name}.{c.name} не распознан: "
                                          "колонка переносится как text. Тип задаётся в type_rules.yaml проекта",
                        obj=t.full_name)
    if t.key_kind == "уникальный":
        nullable = [c.name for c in columns if c.name in key and c.nullable]
        model.issue(Severity.INFO, f"У таблицы {t.full_name} нет первичного ключа, ключом взят уникальный "
                                   f"{t.key_constraint} ({', '.join(key)})"
                                   + (f"; колонки {', '.join(nullable)} допускают NULL, в ключе пустое значение "
                                      "заменяется начальным" if nullable else ""), obj=t.full_name)
    for note in t.notes:
        model.issue(Severity.INFO, note, obj=t.full_name)

    ds = DataSource(name=name, source_system=system, source_type=source_type,
                    description=t.comment or t.full_name,
                    fields=[_field(c, False) for c in columns])
    model.datasources[ds.ref.key] = ds
    kind = AdsoKind.STANDARD if key else AdsoKind.STAGING
    if not key:
        model.issue(Severity.WARNING, f"У таблицы {t.full_name} нет первичного и уникального ключа: она переносится "
                                      "без активации (только входящая таблица), сверка по ключу невозможна",
                    obj=t.full_name)
    adso = Adso(name=name, kind=kind, description=t.comment or t.full_name, info_area=t.schema,
                fields=[_field(c, c.name in key) for c in columns])
    model.adsos[name] = adso
    tran = Transformation(id=f"TR_{name}", source=ds.ref, target=adso.ref,
                          rules=[Rule(rule_id=i, kind=RuleKind.DIRECT, raw_kind="DIRECT", source_fields=[c.name],
                                      target_fields=[c.name]) for i, c in enumerate(columns, start=1)])
    model.transformations[tran.id] = tran
    model.dtps[f"DTP_{name}"] = Dtp(name=f"DTP_{name}", source=ds.ref, target=adso.ref, mode="DELTA")


def _related(text: str, tables: dict[str, SourceTable], schema: str) -> list[ObjectRef]:
    """Таблицы, упомянутые в тексте представления: SCHEMA.TABLE или TABLE той же схемы."""
    found = []
    upper = re.sub(r'["\[\]]', "", text.upper())
    for full, t in tables.items():
        qualified = re.search(rf"(?<![\w$#]){re.escape(full.upper())}(?![\w$#])", upper)
        local = t.schema.upper() == schema.upper() and re.search(
            rf"(?<![\w$#.]){re.escape(t.name.upper())}(?![\w$#])", upper)
        if qualified or local:
            found.append(ObjectRef(type=ObjectType.ADSO, name=t.model_name))
    return found


def build_model(catalog: Catalog, files: dict[str, TableFile], kind: SourceKind, source: str, system: str,
                source_type: str) -> Model:
    model = Model(source=source, imported_at=datetime.now().replace(microsecond=0), source_kind=kind)
    for f in files.values():
        model.issues.extend(f.issues)
        model.files[f.table] = {
            "file": f.path.name if f.path else ("из базы" if f.usable else ""),
            "encoding": f.encoding,
            "delimiter": {";": ";", "\t": "табуляция", ",": ","}.get(f.delimiter, f.delimiter),
            "rows": len(f.rows),
            "status": "прочитан" if f.usable else ("нет файла" if f.path is None else "ошибка"),
        }
    for t in sorted(catalog.tables, key=lambda t: (t.schema, t.name)):
        _add_table(model, t, system, source_type)
    tables = {t.full_name: t for t in catalog.tables if t.model_name in model.adsos}
    for o in sorted(catalog.objects, key=lambda o: (o.type.value, o.schema, o.name)):
        info = o.label + (f", строк: {o.lines}" if o.lines else "") + (f", статус {o.status}" if o.status else "")
        related = _related(o.text, tables, o.schema) if o.text else []
        if related:
            info += ", таблицы: " + ", ".join(r.name for r in related)
        model.others.append(OtherObject(type=o.type, name=o.full_name, related=related, info=info, lines=o.lines))
    for fk in catalog.foreign_keys:
        target = f" → {fk.ref_table}" if fk.ref_table else ""
        model.issue(Severity.INFO, f"Внешний ключ {fk.name} ({', '.join(fk.columns)}){target} не переносится: "
                                   "Greenplum не проверяет внешние ключи, целостность проверяется сверкой",
                    obj=fk.table)
    return model
