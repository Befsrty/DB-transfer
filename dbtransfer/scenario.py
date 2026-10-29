"""Сквозной сценарий run-demo (ТЗ, ФП-9.1): импорт → отчёт → SQL → развёртывание → загрузки → прогон → сверка."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from dbtransfer.analysis.assessment import Report, assess
from dbtransfer.analysis.excel import write_complexity, write_import_check
from dbtransfer.deploy import DeployError, deploy
from dbtransfer.generator import Package, TypeRules, generate
from dbtransfer.importers.bw_csv import import_bw_export
from dbtransfer.model import Severity
from dbtransfer.project import Project
from dbtransfer.reconcile import ReconcileResult, reconcile
from dbtransfer.runtime import load_file, run_load

Echo = Callable[[str], None]


class ScenarioError(Exception):
    """Сценарий остановлен; текст говорит, на каком шаге."""


@dataclass
class DemoSummary:
    report: Report
    package: Package
    loads: list[tuple[str, int, int]] = field(default_factory=list)  # файл, load_id, строк
    reconciles: list[ReconcileResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.reconciles) and bool(self.reconciles)


def data_files(folder: Path) -> tuple[list[tuple[str, Path]], list[tuple[str, Path]], list[tuple[str, Path]]]:
    """Справочники md_<признак>_attr/_text.csv, загрузки <DataSource>_load<N>.csv и эталоны etalon_<ADSO>.csv."""
    md, loads, etalons = [], [], []
    for path in sorted(folder.glob("*.csv")):
        if m := re.fullmatch(r"md_(.+)_(attr|text)\.csv", path.name):
            md.append((m.group(1), path))
        elif m := re.fullmatch(r"(.+)_load(\d+)\.csv", path.name):
            loads.append((m.group(1), int(m.group(2)), path))
        elif m := re.fullmatch(r"etalon_([^.]+)\.csv", path.name):
            if not m.group(1).endswith("_broken"):
                etalons.append((m.group(1), path))
    md.sort(key=lambda x: (x[0], "text" in x[1].name))
    return md, [(n, p) for n, _, p in sorted(loads)], etalons


def run_demo(project: Project, conn: psycopg.Connection, echo: Echo, dialect: str = "greenplum") -> DemoSummary:
    start = time.monotonic()
    echo("1. Импорт выгрузок BW")
    model = import_bw_export(project.bw_export_dir)
    project.save_model(model)
    write_import_check(model, project.out_dir / "import_check.xlsx")
    errors = [i for i in model.issues if i.severity == Severity.ERROR]
    if errors:
        raise ScenarioError(f"импорт: {len(errors)} ошибок, первая: {errors[0].message}")

    echo("2. Отчёт о сложности")
    report = assess(model)
    rules = TypeRules.load(project.root)
    package = generate(model, rules, dialect)
    write_complexity(model, report, project.out_dir / "complexity.xlsx", package.sql_lines, package.db_objects)

    echo("3. Генерация SQL")
    package.write(project.out_dir / "sql")

    echo("4. Развёртывание с пересозданием")
    try:
        result = deploy(conn, package, recreate=True)
    except DeployError as e:
        raise ScenarioError(f"развёртывание: {e}") from e
    if not result.ok:
        raise ScenarioError(f"развёртывание: {result.error}")

    summary = DemoSummary(report, package)
    data = project.root / project.config.data
    md, loads, etalons = data_files(data)
    echo("5. Загрузка справочников")
    for obj, path in md:
        res = load_file(conn, model, rules, obj, path, force=True)
        echo(f"   {path.name}: {res.rows} строк")
    for n, (obj, path) in enumerate(loads, start=1):
        echo(f"{5 + 2 * n - 1}. Загрузка {path.name}")
        res = load_file(conn, model, rules, obj, path, force=True)
        summary.loads.append((path.name, res.load_id, res.rows))
        echo(f"   load_id {res.load_id}, строк {res.rows}")
        echo(f"{5 + 2 * n}. Прогон загрузки {res.load_id}")
        run_load(conn, model, package, res.load_id, project.config.scope_from,
                 progress=lambda s: echo(f"   {s.title}: {s.rows} строк"))
    step = 6 + 2 * len(loads)
    echo(f"{step}. Сверка с эталонами")
    for obj, path in etalons:
        r = reconcile(conn, model, obj, path, project.out_dir, project.root)
        summary.reconciles.append(r)
        echo(f"   {obj}: совпало {r.count('совпало')} из {len(r.detail)}")
    summary.seconds = time.monotonic() - start
    return summary
