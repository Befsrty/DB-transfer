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
    DbTable,
    Dtp,
    FieldDef,
    Model,
    ObjectRef,
    ObjectType,
    OtherObject,
    Partitioning,
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
    caveat: str = ""  # чем перенос типа отличается от источника (⚠ в справочнике типов)
    lossy: bool = False  # значения меняются при переносе (точность, часовой пояс) — предупреждение, а не сведение
    target_type: str = ""  # тип целевой базы как есть (источник Greenplum), см. FieldDef.target_type


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
    partitioning: Partitioning | None = None
    ci_columns: list[str] = field(default_factory=list)  # текстовые колонки с регистронезависимой сортировкой
    # Источник Greenplum: распределение (HASH, RANDOM, REPLICATED) и хранение как есть, см. DbTable
    distribution: str = ""
    distributed_by: list[str] = field(default_factory=list)
    storage: str = ""
    storage_options: str = ""

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
    title: str = ""  # имя в отчёте, если это не схема.имя (скрипт BTEQ — путь к файлу)

    @property
    def full_name(self) -> str:
        return self.title or f"{self.schema}.{self.name}"


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
    notes: list[str] = field(default_factory=list)  # сведения о базе целиком


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


FETCH_ROWS = 5000  # строк за одно обращение к базе: словарь на 10 000 таблиц — сотни тысяч строк колонок


def _fetch(conn, sql: str, spec: BwTable) -> list[Row]:
    """Строки запроса порциями по FETCH_ROWS: драйвер не держит весь результат второй копией,
    а для oracledb большая порция заметно сокращает число обращений к серверу (по умолчанию 100)."""
    cur = conn.cursor()
    try:
        for attr in ("arraysize", "prefetchrows"):  # oracledb; у pymssql arraysize тоже есть
            if hasattr(cur, attr):
                try:
                    setattr(cur, attr, FETCH_ROWS)
                except Exception:  # noqa: BLE001, S110 — не у всех драйверов свойство изменяемое
                    pass
        cur.execute(sql)
        header = [d[0].upper() for d in cur.description]
        missing = [c for c in spec.required_columns if c not in header]
        if missing:
            raise DictImportFailed(f"Запрос к {spec.name} не вернул колонки {', '.join(missing)}")
        index = {h: i for i, h in enumerate(header)}
        rows: list[Row] = []
        line = 2
        while chunk := cur.fetchmany(FETCH_ROWS):
            for values in chunk:
                rows.append(Row(line, index=index, cells=tuple(_text(v).rstrip() for v in values)))
                line += 1
        return rows
    finally:
        cur.close()


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
            result.rows = _fetch(conn, sql, spec)
        except DictImportFailed:
            raise
        except Exception as e:  # noqa: BLE001 — драйверы бросают свои классы ошибок
            if spec.required:
                raise DictImportFailed(f"Запрос к {name} не выполнен: {e}") from e
            result.issue(Severity.WARNING, f"Запрос к {name} не выполнен, сведения пропущены: {e}")
            files[name] = result
            continue
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

def _check_names(model: Model, t: SourceTable, mode: str) -> None:
    longest = naming.db_name(t.model_name) + LONGEST_SUFFIX if mode == TARGET_ADSO else naming.db_name(t.name)
    if len(longest) > MAX_IDENTIFIER:
        model.issue(Severity.WARNING, f"Имя {longest} длиннее {MAX_IDENTIFIER} символов: Greenplum его обрежет. "
                                      "Сократите имя таблицы в шаблонах naming.yaml или переименуйте при переносе",
                    obj=t.full_name)
    seen: dict[str, str] = {}
    for c in t.columns:
        db = naming.db_name(c.name)
        if len(db) > MAX_IDENTIFIER:
            model.issue(Severity.WARNING, f"Имя колонки {c.name} длиннее {MAX_IDENTIFIER} символов: Greenplum его "
                                          "обрежет. Переименуйте колонку при переносе", obj=t.full_name)
        if db in seen:
            model.issue(Severity.ERROR, f"Колонки {seen[db]} и {c.name} дают одно имя {db} в целевой базе",
                        obj=t.full_name)
        if mode == TARGET_ADSO and db in SERVICE_COLUMNS:
            model.issue(Severity.ERROR, f"Колонка {c.name} совпадает со служебной колонкой {db} входных таблиц: "
                                        "переименуйте её при выгрузке (и в словаре, и в данных)", obj=t.full_name)
        seen[db] = c.name


def _field(c: SourceColumn, is_key: bool) -> FieldDef:
    comment = c.native_type + (f". {c.comment}" if c.comment else "")
    return FieldDef(name=c.name, position=c.position, bw_column=c.name, datatype=c.datatype, length=c.length,
                    decimals=c.decimals, is_key=is_key, nullable=c.nullable and not is_key, comment=comment,
                    target_type=c.target_type)


# Режимы переноса таблиц реляционного источника
TARGET_TABLE = "table"  # одна таблица с исходным именем, загрузка напрямую (по умолчанию)
TARGET_ADSO = "adso"  # как поток BW: входная таблица, ADSO standard, трансформация, активация
TARGET_MODES = (TARGET_TABLE, TARGET_ADSO)


def _check_table(model: Model, t: SourceTable, columns: list[SourceColumn], key: list[str], mode: str) -> None:
    if len(key) != len(t.key):
        model.issue(Severity.WARNING, f"Ключ {t.key_constraint} таблицы {t.full_name}: колонок "
                                      f"{', '.join(k for k in t.key if k not in key)} нет среди колонок таблицы",
                    obj=t.full_name)
    for c in columns:
        if not c.known:
            model.issue(Severity.WARNING, f"Тип {c.native_type} колонки {t.full_name}.{c.name} не распознан: "
                                          "колонка переносится как text. Тип задаётся в type_rules.yaml проекта",
                        obj=t.full_name)
    caveats: dict[tuple[str, bool], list[str]] = {}
    for c in columns:
        if c.caveat:
            caveats.setdefault((c.caveat, c.lossy), []).append(c.name)
    for (text, lossy), names in caveats.items():
        model.issue(Severity.WARNING if lossy else Severity.INFO,
                    f"Таблица {t.full_name}, {'колонки' if len(names) > 1 else 'колонка'} {', '.join(names)}: {text}",
                    obj=t.full_name)
    if t.key_kind == "уникальный":
        nullable = [c.name for c in columns if c.name in key and c.nullable]
        model.issue(Severity.INFO, f"У таблицы {t.full_name} нет первичного ключа, ключом взят уникальный "
                                   f"{t.key_constraint} ({', '.join(key)})"
                                   + (f"; колонки {', '.join(nullable)} допускают NULL, в ключе пустое значение "
                                      "заменяется начальным" if nullable else ""), obj=t.full_name)
    ci_key = [c for c in t.ci_columns if c in key]
    if ci_key:
        model.issue(Severity.WARNING, f"Колонки ключа {', '.join(ci_key)} таблицы {t.full_name} в источнике "
                                      "сравниваются без учёта регистра (MS SQL *_CI_*, Teradata NOT CASESPECIFIC), "
                                      "а в Greenplum — с учётом. "
                                      "Загрузка и сверка сравнивают их через lower(); соединения в своих запросах "
                                      "тоже пишите через lower()", obj=t.full_name)
    if not key:
        what = ("загружается только добавлением строк, без перезаписи" if mode == TARGET_TABLE
                else "переносится без активации (только входящая таблица)")
        model.issue(Severity.WARNING, f"У таблицы {t.full_name} нет первичного и уникального ключа: она {what}, "
                                      "сверка по ключу невозможна", obj=t.full_name)
    for note in t.notes:
        model.issue(Severity.INFO, note, obj=t.full_name)


def _add_table(model: Model, t: SourceTable, system: str, source_type: str, mode: str) -> None:
    name = t.model_name
    if name in model.adsos or t.full_name in model.tables:
        model.issue(Severity.ERROR, f"Таблица {t.full_name} совпадает по имени {name} с другой таблицей, пропущена",
                    obj=t.full_name, skipped=True)
        return
    if not t.columns:
        model.issue(Severity.WARNING, f"У таблицы {t.full_name} нет колонок в словаре, пропущена",
                    obj=t.full_name, skipped=True)
        return
    _check_names(model, t, mode)
    columns = sorted(t.columns, key=lambda c: c.position)
    names = {c.name for c in columns}
    key = [k for k in t.key if k in names]
    _check_table(model, t, columns, key, mode)

    if mode == TARGET_TABLE:
        model.tables[t.full_name] = DbTable(
            schema_name=t.schema, name=t.name, description=t.comment, key_kind=t.key_kind,
            fields=[_field(c, c.name in key) for c in columns], partitioning=t.partitioning,
            ci_columns=[c for c in t.ci_columns if c in key], distribution=t.distribution,
            distributed_by=[c for c in t.distributed_by if c in names], storage=t.storage,
            storage_options=t.storage_options)
        return
    if t.partitioning is not None:
        model.issue(Severity.INFO, f"Секционирование таблицы {t.full_name} в режиме ADSO не переносится",
                    obj=t.full_name)
    if t.distribution:
        model.issue(Severity.INFO, f"Распределение и хранение таблицы {t.full_name} в режиме ADSO берутся из "
                                   "type_rules.yaml, а не из источника", obj=t.full_name)
    ds = DataSource(name=name, source_system=system, source_type=source_type,
                    description=t.comment or t.full_name,
                    fields=[_field(c, False) for c in columns])
    model.datasources[ds.ref.key] = ds
    kind = AdsoKind.STANDARD if key else AdsoKind.STAGING
    adso = Adso(name=name, kind=kind, description=t.comment or t.full_name, info_area=t.schema,
                fields=[_field(c, c.name in key) for c in columns])
    model.adsos[name] = adso
    tran = Transformation(id=f"TR_{name}", source=ds.ref, target=adso.ref,
                          rules=[Rule(rule_id=i, kind=RuleKind.DIRECT, raw_kind="DIRECT", source_fields=[c.name],
                                      target_fields=[c.name]) for i, c in enumerate(columns, start=1)])
    model.transformations[tran.id] = tran
    model.dtps[f"DTP_{name}"] = Dtp(name=f"DTP_{name}", source=ds.ref, target=adso.ref, mode="DELTA")


_WORD = re.compile(r"[A-Z0-9_$#]+(?:\.[A-Z0-9_$#]+)?")


def _related(text: str, by_name: dict[str, list[SourceTable]], schema: str, mode: str) -> list[ObjectRef]:
    """Таблицы, упомянутые в тексте представления: SCHEMA.TABLE или TABLE той же схемы. Текст разбивается на
    слова один раз, поэтому время не зависит от числа таблиц в словаре."""
    words = set(_WORD.findall(re.sub(r'["\[\]]', "", text.upper())))
    found: dict[str, SourceTable] = {}
    for w in words:
        owner, _, table = w.rpartition(".")
        for t in by_name.get(table, []):
            if (owner and owner == t.schema.upper()) or (not owner and t.schema.upper() == schema.upper()):
                found[t.full_name] = t
    return [ObjectRef(type=ObjectType.TABLE, name=t.full_name) if mode == TARGET_TABLE
            else ObjectRef(type=ObjectType.ADSO, name=t.model_name)
            for t in sorted(found.values(), key=lambda t: t.full_name)]


def build_model(catalog: Catalog, files: dict[str, TableFile], kind: SourceKind, source: str, system: str,
                source_type: str, mode: str = TARGET_TABLE) -> Model:
    if mode not in TARGET_MODES:
        raise DictImportFailed(f"Режим переноса «{mode}» неизвестен, допустимо: {', '.join(TARGET_MODES)}")
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
        f.rows = []  # строки уже разобраны в catalog; на большом словаре это сотни мегабайт
    catalog.tables.sort(key=lambda t: (t.schema, t.name))
    by_name: dict[str, list[SourceTable]] = {}
    for t in catalog.tables:
        _add_table(model, t, system, source_type, mode)
        if t.model_name in model.adsos or t.full_name in model.tables:
            by_name.setdefault(t.name.upper(), []).append(t)
        t.columns = []  # колонки уже в модели, для поиска связей нужны только имена
    for o in sorted(catalog.objects, key=lambda o: (o.type.value, o.schema, o.name)):
        info = o.label + (f", строк: {o.lines}" if o.lines else "") + (f", статус {o.status}" if o.status else "")
        related = _related(o.text, by_name, o.schema, mode) if o.text else []
        if related:
            info += ", таблицы: " + ", ".join(r.name for r in related)
        model.others.append(OtherObject(type=o.type, name=o.full_name, related=related, info=info, lines=o.lines))
    for note in catalog.notes:
        model.issue(Severity.INFO, note)
    for fk in catalog.foreign_keys:
        target = f" → {fk.ref_table}" if fk.ref_table else ""
        model.issue(Severity.INFO, f"Внешний ключ {fk.name} ({', '.join(fk.columns)}){target} не переносится: "
                                   "Greenplum не проверяет внешние ключи, целостность проверяется сверкой",
                    obj=fk.table)
    return model
