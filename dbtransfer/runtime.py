"""Загрузка CSV и прогон загрузки по потоку (ТЗ, ФП-7).

Загрузка идёт в два шага: файл копируется (COPY) в сырую таблицу <stg>.<имя>_raw, где все колонки text,
затем SQL-запрос проверяет значения и переносит строки в типизированную таблицу с приведением типов.
Прогон вызывает сгенерированные функции трансформаций и активации в порядке потока одной транзакцией.
"""

from __future__ import annotations

import csv
import getpass
import hashlib
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import psycopg

from dbtransfer import naming
from dbtransfer.analysis.graph import build_graph, node, scope_from
from dbtransfer.generator import Package, TypeRules
from dbtransfer.generator.sql import literal, qualified
from dbtransfer.generator.tables import Column, TableBuilder, TableDef
from dbtransfer.generator.types import NUMERIC_TYPES
from dbtransfer.importers.csv_reader import decode, detect_delimiter
from dbtransfer.model import Adso, AdsoKind, DataSource, FieldDef, InfoObjectKind, Model, ObjectRef, ObjectType

MAX_ERRORS = 20
# Статусы журнала загрузок (ТЗ, 4.3.6)
LOADED, ACTIVATED, FAILED = "loaded", "activated", "failed"
ROLLED_BACK = "откат загрузки"


class LoadError(Exception):
    """Загрузка отменена; errors — первые ошибочные строки файла."""

    def __init__(self, message: str, errors: list[RowError] | None = None, total: int = 0):
        super().__init__(message)
        self.errors = errors or []
        self.total = total or len(self.errors)


class RunError(Exception):
    """Прогон не выполнен или отменён; текст понятен пользователю."""


@dataclass
class RowError:
    line: int
    field: str
    value: str
    problem: str

    def __str__(self) -> str:
        where = f"строка {self.line}" + (f", поле {self.field}" if self.field else "")
        value = f" «{self.value}»" if self.value else ""
        return f"{where}{value}: {self.problem}"


@dataclass
class LoadTarget:
    kind: str  # datasource, md_attr, md_text
    object_name: str
    table: TableDef

    @property
    def with_load_id(self) -> bool:
        return self.kind == "datasource"


@dataclass
class LoadResult:
    load_id: int
    object_name: str
    table: str
    rows: int
    file_name: str
    warnings: list[str] = field(default_factory=list)


# --- чтение файла ---

def read_csv(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Заголовок и строки файла с номерами строк (заголовок — строка 1). Кодировка и разделитель —
    как у выгрузок BW: UTF-8 или Windows-1251, «;», табуляция или «,»."""
    text, _ = decode(path.read_bytes())
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise LoadError(f"Файл {path.name} пустой: нет строки заголовка")
    reader = csv.reader(io.StringIO(text), delimiter=detect_delimiter(lines[0]))
    header = [h.strip() for h in next(reader)]
    rows = []
    for values in reader:
        if any(v.strip() for v in values):
            rows.append((reader.line_num, values))
    return header, rows


def _pseudo_field(col: Column) -> FieldDef:
    """Тип служебной колонки справочника (язык, тексты) в терминах BW для проверки и приведения."""
    m = re.match(r"(?:var)?char\((\d+)\)", col.type)
    return FieldDef(name=col.name.upper(), position=0, datatype="CHAR", length=int(m.group(1)) if m else 0,
                    is_key=col.not_null, nullable=not col.not_null)


def resolve_target(model: Model, rules: TypeRules, name: str, header: list[str]) -> LoadTarget:
    """Куда грузить файл: входная таблица DataSource или атрибуты/тексты признака (по заголовку)."""
    builder = TableBuilder(model, rules)
    obj = model.find(name)
    if isinstance(obj, DataSource):
        return LoadTarget("datasource", obj.name, builder.datasource_table(obj))
    iobj = model.info_objects.get(name)
    if iobj is not None and iobj.kind == InfoObjectKind.CHARACTERISTIC:
        tables = {t.role: t for t in builder.md_tables(iobj)}
        role = "md_text" if "LANGU" in {h.upper() for h in header} else "md_attr"
        if role not in tables:
            what = "текстов" if role == "md_text" else "атрибутов"
            raise LoadError(f"У признака {name} нет таблицы {what}: в модели у него нет "
                            f"{'текстов' if role == 'md_text' else 'атрибутов'}")
        return LoadTarget(role, name, tables[role])
    if isinstance(obj, Adso):
        raise LoadError(f"{name} — ADSO, а файлы загружаются в DataSource или справочник. "
                        "Загрузите файл в DataSource и выполните прогон: dbtransfer run <load_id>")
    raise LoadError(f"Объект {name} не найден. Файл грузится в DataSource (например, ZSALES_FF) "
                    "или в справочник признака (например, 0MATERIAL)")


def map_columns(target: LoadTarget, header: list[str]) -> list[Column]:
    """Колонка таблицы для каждой колонки файла; все колонки файла должны быть в таблице (ФП-7.2)."""
    result, unknown = [], []
    for h in header:
        up = h.upper()
        col = next((c for c in target.table.columns if c.field is not None
                    and up in (c.field.name.upper(), (c.field.info_object or "").upper())), None)
        col = col or target.table.column(naming.db_name(h)) or target.table.column(h.lower())
        if col is None or col.name in ("load_id", "record_no"):
            unknown.append(h)
        else:
            result.append(col)
    if unknown:
        raise LoadError(f"Колонок {', '.join(unknown)} нет в таблице {target.table.qname}. Колонки файла "
                        "называются как поля DataSource или InfoObject (в первой строке файла)")
    names = {c.name for c in result}
    missing = [c.name for c in target.table.columns
               if c.not_null and c.name not in names and c.name not in ("load_id", "record_no")]
    if missing and not target.with_load_id:
        raise LoadError(f"В файле нет ключевых колонок: {', '.join(missing)}")
    return result


# --- проверка и приведение (ТЗ 4.3.3, ФП-7.1, ФП-7.2) ---

def _f(col: Column) -> FieldDef:
    return col.field or _pseudo_field(col)


def _trim(src: str) -> str:
    return f"btrim({src})"


def check_sql(col: Column, src: str) -> list[tuple[str, str]]:
    """Условия «значение не приводится к типу» и текст проблемы."""
    f = _f(col)
    t = _trim(src)
    if f.datatype in ("CURR", "QUAN", "DEC", "FLTP"):
        norm = f"replace(replace({t}, ' ', ''), ',', '.')"
        checks = [(f"{t} <> '' AND {norm} !~ '^[-+]?[0-9]*[.]?[0-9]+$'", "не число")]
        if f.datatype != "FLTP" and f.length:
            whole = f.length - f.decimals
            checks.append((f"{t} <> '' AND length(ltrim(regexp_replace(split_part({norm}, '.', 1), '[^0-9]', '', "
                           f"'g'), '0')) > {whole}", f"больше {whole} знаков до запятой"))
        return checks
    if f.datatype in ("INT1", "INT2", "INT4", "INT8"):
        return [(f"{t} <> '' AND {t} !~ '^[-+]?[0-9]+$'", "не целое число")]
    if f.datatype == "DATS":
        ymd = (f"({t} ~ '^[0-9]{{8}}$' AND substr({t}, 5, 2) BETWEEN '01' AND '12' "
               f"AND substr({t}, 7, 2) BETWEEN '01' AND '31')")
        dmy = (f"({t} ~ '^[0-9]{{2}}[.][0-9]{{2}}[.][0-9]{{4}}$' AND substr({t}, 4, 2) BETWEEN '01' AND '12' "
               f"AND substr({t}, 1, 2) BETWEEN '01' AND '31')")
        return [(f"{t} NOT IN ('', '00000000') AND NOT {ymd} AND NOT {dmy}", "не дата ГГГГММДД или ДД.ММ.ГГГГ")]
    if f.datatype == "TIMS":
        return [(f"{t} <> '' AND {t} !~ '^[0-9]{{6}}$'", "не время ЧЧММСС")]
    checks = []
    if f.datatype == "NUMC":
        checks.append((f"{t} !~ '^[0-9]*$'", "не только цифры (NUMC)"))
    if f.length and f.datatype not in ("STRING", "SSTRING"):
        checks.append((f"length({src}) > {f.length}", f"длиннее {f.length} символов"))
    return checks


def convert_sql(col: Column, src: str, rules: TypeRules) -> str:
    """Значение сырой колонки, приведённое к типу колонки таблицы."""
    f = _f(col)
    t = _trim(src)
    key = col.not_null
    if f.datatype in NUMERIC_TYPES - {"INT1", "INT2", "INT4", "INT8"}:
        expr = f"nullif(replace(replace({t}, ' ', ''), ',', '.'), '')::{col.type}"
    elif f.datatype in ("INT1", "INT2", "INT4", "INT8"):
        expr = f"nullif({t}, '')::{col.type}"
    elif f.datatype == "DATS":
        empty = rules.initial_value(f) if key else "NULL"
        return (f"CASE WHEN {t} IN ('', '00000000') THEN {empty} "
                f"WHEN {t} ~ '[.]' THEN to_date({t}, 'DD.MM.YYYY') ELSE to_date({t}, 'YYYYMMDD') END")
    elif f.datatype == "TIMS":
        expr = f"CASE WHEN {t} = '' THEN NULL ELSE to_timestamp({t}, 'HH24MISS')::time END"
    elif f.datatype == "NUMC" and key:
        return f"CASE WHEN {t} = '' THEN {rules.initial_value(f)} ELSE {src} END"
    else:
        return src  # CHAR и прочие строки как есть, пустое значение — пустая строка, как в BW
    return f"coalesce({expr}, {rules.initial_value(f)})" if key else expr


def _row_errors(header: list[str], rows: list[tuple[int, list[str]]]) -> list[RowError]:
    return [RowError(line, "", "", f"{len(values)} значений вместо {len(header)}; проверьте разделитель и кавычки")
            for line, values in rows if len(values) != len(header)]


def _table_exists(cur: psycopg.Cursor, name: str) -> bool:
    return cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone()[0]


def load_file(conn: psycopg.Connection, model: Model, rules: TypeRules, object_name: str, path: Path,
              force: bool = False) -> LoadResult:
    """Загрузить CSV одной транзакцией. LoadError — загрузка отменена целиком."""
    if not path.is_file():
        raise LoadError(f"Файл {path} не найден")
    header, rows = read_csv(path)
    target = resolve_target(model, rules, object_name, header)
    columns = map_columns(target, header)
    if errors := _row_errors(header, rows):
        raise LoadError(f"Файл {path.name} не загружен: строки с неверным числом значений",
                        errors[:MAX_ERRORS], len(errors))

    meta = naming.schema("meta", model.schema_prefix)
    table = target.table
    raw = qualified(naming.schema("stg", model.schema_prefix), f"{table.name}_raw")
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    user = getpass.getuser()
    try:
        with conn.cursor() as cur:
            for name in (table.qname, f"{meta}.load_log"):
                if not _table_exists(cur, name):
                    raise LoadError(f"Таблицы {name} нет в базе. Сначала разверните пакет: dbtransfer deploy")
            if not force:
                prev = cur.execute(f"SELECT load_id FROM {meta}.load_log WHERE object_name = %s AND file_hash = %s "
                                   f"AND status <> '{FAILED}' ORDER BY load_id", (target.object_name, file_hash)
                                   ).fetchall()
                if prev:
                    raise LoadError(f"Этот файл уже загружался в {target.object_name} (загрузка "
                                    f"{', '.join(str(r[0]) for r in prev)}). Загрузить ещё раз: --force")

            # Шаг 1: сырая таблица, все колонки text, пустое значение остаётся пустой строкой
            cur.execute(f"DROP TABLE IF EXISTS {raw}")
            raw_cols = [c.sql_name for c in columns]
            cur.execute(f"CREATE TABLE {raw} (line_no bigint, " + ", ".join(f"{c} text" for c in raw_cols) + ")")
            with cur.copy(f"COPY {raw} (line_no, {', '.join(raw_cols)}) FROM STDIN") as copy:
                for line, values in rows:
                    copy.write_row([line, *values])

            # Шаг 2: проверка значений запросом к сырой таблице
            checks = [(c, cond, problem) for c in columns for cond, problem in check_sql(c, f"r.{c.sql_name}")]
            if checks:
                union = " UNION ALL ".join(
                    f"SELECT r.line_no, {literal(header[columns.index(c)])} AS field, r.{c.sql_name} AS value, "
                    f"{literal(problem)} AS problem FROM {raw} r WHERE {cond}" for c, cond, problem in checks)
                total = cur.execute(f"SELECT count(*) FROM ({union}) e").fetchone()[0]
                if total:
                    found = cur.execute(f"SELECT * FROM ({union}) e ORDER BY 1, 2 LIMIT {MAX_ERRORS}").fetchall()
                    raise LoadError(f"Файл {path.name} не загружен: {total} значений не приводятся к типам",
                                    [RowError(*r) for r in found], total)

            # Шаг 3: перенос в типизированную таблицу
            load_id = cur.execute(f"SELECT nextval('{meta}.load_id_seq')").fetchone()[0]
            exprs = [convert_sql(c, f"r.{c.sql_name}", rules) for c in columns]
            names = [c.sql_name for c in columns]
            if target.with_load_id:
                names = ["load_id", "record_no", *names]
                exprs = [str(load_id), "row_number() OVER (ORDER BY r.line_no)", *exprs]
            else:
                keys = [c for c in columns if c.not_null]
                join = " AND ".join(f"t.{c.sql_name} = {convert_sql(c, f'r.{c.sql_name}', rules)}" for c in keys)
                cur.execute(f"DELETE FROM {table.qname} t USING {raw} r WHERE {join}")
            cur.execute(f"INSERT INTO {table.qname} ({', '.join(names)}) SELECT {', '.join(exprs)} FROM {raw} r")
            count = cur.rowcount
            status = LOADED if target.with_load_id else ACTIVATED
            cur.execute(
                f"INSERT INTO {meta}.load_log (load_id, object_name, file_name, file_hash, rows_loaded, status, "
                "loaded_by, loaded_at, activated_at, message) VALUES (%s, %s, %s, %s, %s, %s, %s, now(), "
                f"{'NULL' if target.with_load_id else 'now()'}, %s)",
                (load_id, target.object_name, path.name, file_hash, count, status, user,
                 None if target.with_load_id else f"справочник: {table.qname}"))
        conn.commit()
    except psycopg.Error as e:
        conn.rollback()
        raise LoadError(f"Файл {path.name} не загружен: {(e.diag.message_primary or str(e)).strip()}") from e
    except LoadError:
        conn.rollback()
        raise
    return LoadResult(load_id, target.object_name, table.qname, count, path.name)


# --- прогон (ФП-7.3) ---

@dataclass
class Step:
    kind: str  # transformation или activation
    title: str
    function: str  # имя функции без сигнатуры
    stub: list[str] = field(default_factory=list)
    rows: int | None = None


@dataclass
class RunResult:
    load_id: int
    object_name: str
    steps: list[Step]


def _fn(name: str) -> str:
    return name.removesuffix("(bigint)")


def flow_steps(model: Model, package: Package, source: ObjectRef, scope: str | None) -> list[Step]:
    """Шаги прогона от источника вверх по потоку: трансформации в объект, затем его активация.
    Если задан состав переноса, берутся только его объекты."""
    g = build_graph(model)
    start = node(source)
    if start not in g:
        return []
    reach = nx.descendants(g, start)
    if scope:
        reach &= {node(r) for r in scope_from(model, scope).objects}
    reached = {start}
    steps = []
    for n in nx.lexicographical_topological_sort(g.subgraph(reach | {start})):
        if n == start:
            continue
        ref: ObjectRef = g.nodes[n]["ref"]
        incoming = sorted((t for t in model.transformations.values()
                           if node(t.target) == n and node(t.source) in reached), key=lambda t: (t.source.key, t.id))
        if not incoming or ref.type != ObjectType.ADSO:
            continue
        for t in incoming:
            obj = package.function_for(t.id, "transformations")
            if obj is None:
                raise RunError(f"Для трансформации {t.source.name} → {t.target.name} нет функции в пакете SQL. "
                               "Сгенерируйте и разверните пакет: dbtransfer deploy")
            steps.append(Step("transformation", f"трансформация {t.source.name} → {t.target.name}",
                              _fn(obj.name), list(obj.stub)))
        adso = model.adsos.get(ref.name)
        if adso and adso.kind in (AdsoKind.STANDARD, AdsoKind.CUMULATIVE):
            obj = package.function_for(adso.name, "activation")
            if obj is not None:
                steps.append(Step("activation", f"активация {adso.name}", _fn(obj.name)))
        reached.add(n)
    return steps


def run_load(conn: psycopg.Connection, model: Model, package: Package, load_id: int, scope: str | None = None,
             progress: Callable[[Step], None] | None = None) -> RunResult:
    """Прогнать загрузку по потоку одной транзакцией (ФП-7.3, 7.6, 7.7)."""
    meta = package.meta_schema
    with conn.cursor() as cur:
        if not _table_exists(cur, f"{meta}.load_log"):
            raise RunError("Служебных таблиц нет в базе. Сначала разверните пакет: dbtransfer deploy")
        row = cur.execute(f"SELECT object_name, status, message FROM {meta}.load_log WHERE load_id = %s",
                          (load_id,)).fetchone()
        if row is None:
            raise RunError(f"Загрузки {load_id} нет в журнале загрузок")
        object_name, status, message = row
        if status == ACTIVATED:
            raise RunError(f"Загрузка {load_id} уже активирована")
        if status == FAILED and message == ROLLED_BACK:
            raise RunError(f"Загрузка {load_id} откачена, её данных больше нет. Загрузите файл заново")
        earlier = cur.execute(f"SELECT min(load_id) FROM {meta}.load_log WHERE object_name = %s AND load_id < %s "
                              f"AND status = '{LOADED}'", (object_name, load_id)).fetchone()[0]
    conn.rollback()
    if earlier is not None:
        raise RunError(f"Сначала прогоните загрузку {earlier} того же объекта {object_name}: "
                       "загрузки активируются строго по возрастанию номера")
    source = model.find(object_name)
    if not isinstance(source, DataSource):
        raise RunError(f"Загрузка {load_id} — это {object_name}, а прогон выполняется для загрузок DataSource")
    steps = flow_steps(model, package, source.ref, scope)
    if not steps:
        raise RunError(f"Из {object_name} не идёт ни одной трансформации" + (f" в составе переноса {scope}"
                                                                               if scope else ""))
    stubs = [s for s in steps if s.stub]
    if stubs:
        raise RunError("Прогон не начат: в потоке есть заготовки, требующие ручной доработки: "
                       + "; ".join(f"{s.title} ({', '.join(s.stub)})" for s in stubs))

    current = None
    try:
        with conn.cursor() as cur:
            for step in steps:
                current = step
                step.rows = cur.execute(f"SELECT {step.function}(%s)", (load_id,)).fetchone()[0]
                if progress:
                    progress(step)
            cur.execute(f"UPDATE {meta}.load_log SET status = '{ACTIVATED}', activated_at = now() WHERE load_id = %s",
                        (load_id,))
        conn.commit()
    except psycopg.Error as e:
        conn.rollback()
        message = f"{current.title if current else 'прогон'}: {(e.diag.message_primary or str(e)).strip()}"
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {meta}.load_log SET status = '{FAILED}', message = %s WHERE load_id = %s",
                        (message, load_id))
        conn.commit()
        raise RunError(f"Прогон загрузки {load_id} отменён, все шаги откачены. Ошибка: {message}") from e
    return RunResult(load_id, object_name, steps)


def rollback_load(conn: psycopg.Connection, model: Model, load_id: int) -> int:
    """Откатить неактивированную загрузку DataSource: удалить её строки (ФП-7.5)."""
    meta = naming.schema("meta", model.schema_prefix)
    with conn.cursor() as cur:
        row = cur.execute(f"SELECT object_name, status FROM {meta}.load_log WHERE load_id = %s", (load_id,)).fetchone()
        if row is None:
            raise RunError(f"Загрузки {load_id} нет в журнале загрузок")
        object_name, status = row
        if status == ACTIVATED:
            raise RunError(f"Загрузка {load_id} уже активирована, её данные разошлись по потоку; откат невозможен")
        source = model.find(object_name)
        if not isinstance(source, DataSource):
            raise RunError(f"Загрузка {load_id} ({object_name}) — справочник, его откат не поддерживается")
        cur.execute(f"DELETE FROM {naming.data_table(model, source)} WHERE load_id = %s", (load_id,))
        deleted = cur.rowcount
        cur.execute(f"UPDATE {meta}.load_log SET status = '{FAILED}', message = %s WHERE load_id = %s",
                    (ROLLED_BACK, load_id))
    conn.commit()
    return deleted


__all__ = ["LoadError", "LoadResult", "RowError", "RunError", "RunResult", "Step", "flow_steps", "load_file",
           "read_csv", "rollback_load", "run_load"]
