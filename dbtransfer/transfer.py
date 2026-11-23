"""Перенос данных из источника той же семьи (Greenplum, Yandex MPP, Cloudberry) и сверка источника с целью.

Данные идут обычным путём загрузчика: выгрузка таблицы в CSV (COPY … TO STDOUT через мастер), затем
load_file в таблицу 1:1. Так работают те же проверки типов, журнал загрузок и перезапись по ключу, что и для
Oracle и MS SQL Server. Для сотен гигабайт это медленно — там нужны gpfdist / PXF или gpcopy между кластерами;
здесь цель — проверить перенос структуры на реальных данных и сверить результат.

Сверка: число строк и суммы всех числовых колонок каждой таблицы в источнике и в цели. Целые и numeric
сравниваются точно, real и double precision — с относительным допуском (порядок сложения по сегментам разный).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from dbtransfer import naming
from dbtransfer.generator.sql import ident
from dbtransfer.generator.tables import TableBuilder
from dbtransfer.generator.types import TypeRules
from dbtransfer.model import DbTable, FieldDef, Model, SourceKind
from dbtransfer.runtime import NULL_MARKER, LoadResult, load_file

NUMBER_TYPES = {"INT1", "INT2", "INT4", "INT8", "DEC", "NUMERIC", "CURR", "QUAN", "FLTP"}
FLOAT_TOLERANCE = 1e-9
# Сессия выгрузки: даты ISO, время в UTC, double precision без потери знаков (в Greenplum 6 по умолчанию 15)
EXPORT_SETTINGS = ("SET DateStyle = 'ISO, YMD'", "SET TimeZone = 'UTC'", "SET extra_float_digits = 3",
                   "SET bytea_output = 'hex'")


class TransferError(Exception):
    """Перенос или сверка не выполнены; текст понятен пользователю."""


def _q(name: str) -> str:
    """Имя источника всегда в кавычках: в каталоге оно записано точно, с регистром."""
    return '"' + name.replace('"', '""') + '"'


def _source_name(t: DbTable) -> str:
    return f"{_q(t.schema_name)}.{_q(t.name)}"


def _tables(model: Model, names: Iterable[str] | None) -> list[DbTable]:
    if model.source_kind != SourceKind.GREENPLUM:
        raise TransferError("Перенос данных напрямую из базы есть только для источника Greenplum; для других "
                            "источников выгрузите CSV и загрузите командой dbtransfer load")
    if not model.tables:
        raise TransferError("В модели нет таблиц 1:1: импортируйте каталог с --target table")
    if not names:
        return sorted(model.tables.values(), key=lambda t: t.key_name)
    result = []
    for name in names:
        t = model.find_table(name)
        if t is None:
            raise TransferError(f"Таблицы {name} нет в модели")
        result.append(t)
    return result


def file_name(t: DbTable) -> str:
    return f"{t.schema_name}.{t.name}.csv"


def export_table(conn, t: DbTable, path: Path) -> Path:
    """Выгрузить таблицу источника в CSV: заголовок — имена колонок, NULL — \\N, bytea — 0x и hex."""
    columns = []
    for f in sorted(t.fields, key=lambda x: x.position):
        col = _q(f.name)
        expr = f"'0x' || encode({col}, 'hex')" if f.datatype == "BINARY" else col  # пустой bytea — 0x, не NULL
        columns.append(f"{expr} AS {col}")
    sql = (f"COPY (SELECT {', '.join(columns)} FROM {_source_name(t)}) TO STDOUT "
           f"WITH (FORMAT csv, HEADER true, NULL '{NULL_MARKER}')")
    path.parent.mkdir(parents=True, exist_ok=True)
    with conn.cursor() as cur:
        for setting in EXPORT_SETTINGS:
            cur.execute(setting)
        with path.open("wb") as out, cur.copy(sql) as copy:
            for chunk in copy:
                out.write(chunk)
    if not getattr(conn, "autocommit", False):
        conn.rollback()
    return path


def export_data(conn, model: Model, out_dir: Path, names: Iterable[str] | None = None,
                progress: Callable[[DbTable, Path], None] | None = None) -> list[Path]:
    paths = []
    for t in _tables(model, names):
        path = export_table(conn, t, out_dir / file_name(t))
        paths.append(path)
        if progress:
            progress(t, path)
    return paths


def transfer_data(source_conn, target_conn, model: Model, rules: TypeRules, work_dir: Path,
                  names: Iterable[str] | None = None,
                  progress: Callable[[LoadResult], None] | None = None) -> list[LoadResult]:
    """Перенести данные таблиц: выгрузка из источника и загрузка в развёрнутую цель.

    Перенос полный: таблица цели очищается в той же транзакции, что и загрузка, поэтому повторный запуск
    не удваивает строки таблиц без ключа, а при ошибке загрузки в таблице остаются прежние данные."""
    builder = TableBuilder(model, rules)
    results = []
    for t in _tables(model, names):
        path = export_table(source_conn, t, work_dir / file_name(t))
        try:
            target_conn.execute(f"TRUNCATE {builder.db_table(t).qname}")  # без commit: его делает load_file
            result = load_file(target_conn, model, rules, t.key_name, path, force=True)
        except Exception:
            target_conn.rollback()
            raise
        results.append(result)
        if progress:
            progress(result)
    return results


# --- сверка источника с целью ---

@dataclass
class ColumnCheck:
    column: str
    source: Decimal | float | None
    target: Decimal | float | None
    exact: bool = True

    @property
    def ok(self) -> bool:
        if self.source is None or self.target is None:
            return self.source is None and self.target is None
        if self.exact:
            return Decimal(self.source) == Decimal(self.target)
        a, b = float(self.source), float(self.target)
        return math.isclose(a, b, rel_tol=FLOAT_TOLERANCE, abs_tol=FLOAT_TOLERANCE)


@dataclass
class TableCheck:
    table: str  # SCHEMA.TABLE в источнике
    target: str  # схема.таблица в цели
    source_rows: int | None = None
    target_rows: int | None = None
    sums: list[ColumnCheck] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.source_rows == self.target_rows and all(c.ok for c in self.sums)

    @property
    def problems(self) -> list[str]:
        if self.error:
            return [self.error]
        result = []
        if self.source_rows != self.target_rows:
            result.append(f"строк {self.source_rows} в источнике, {self.target_rows} в цели")
        result += [f"сумма {c.column}: {c.source} в источнике, {c.target} в цели" for c in self.sums if not c.ok]
        return result


def _measures(t: DbTable) -> list[FieldDef]:
    return [f for f in sorted(t.fields, key=lambda x: x.position) if f.datatype in NUMBER_TYPES]


def _totals(conn, table: str, columns: list[str]) -> tuple[int, list]:
    sums = ", ".join(f"sum({c})" for c in columns)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*){', ' + sums if sums else ''} FROM {table}")
        row = cur.fetchone()
    if not getattr(conn, "autocommit", False):
        conn.rollback()
    return int(row[0]), list(row[1:])


def compare_tables(source_conn, target_conn, model: Model, rules: TypeRules,
                   names: Iterable[str] | None = None) -> list[TableCheck]:
    """Число строк и суммы числовых колонок в источнике и в цели по каждой таблице 1:1."""
    builder = TableBuilder(model, rules)
    checks = []
    for t in _tables(model, names):
        target = builder.db_table(t)
        check = TableCheck(table=t.key_name, target=target.qname)
        measures = _measures(t)
        try:
            check.source_rows, source = _totals(source_conn, _source_name(t),
                                                [_q(f.name) for f in measures])
            check.target_rows, actual = _totals(target_conn, target.qname,
                                                [ident(naming.db_name(f.name)) for f in measures])
        except Exception as e:  # noqa: BLE001 — psycopg.Error и ошибки сети одинаково попадают в протокол
            for conn in (source_conn, target_conn):
                if not getattr(conn, "autocommit", False):
                    conn.rollback()
            check.error = str(e).strip().splitlines()[0]
            checks.append(check)
            continue
        check.sums = [ColumnCheck(f.name, s, a, exact=f.datatype != "FLTP")
                      for f, s, a in zip(measures, source, actual, strict=True)]
        checks.append(check)
    return checks
