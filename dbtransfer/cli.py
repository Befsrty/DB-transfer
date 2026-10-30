"""Командная строка dbtransfer (ТЗ, раздел 4.4.2). Команды добавляются по шагам плана."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from dbtransfer import __version__
from dbtransfer.analysis.assessment import assess
from dbtransfer.analysis.excel import write_complexity, write_import_check
from dbtransfer.analysis.graph import ScopeError, scope_from
from dbtransfer.analysis.lineage import DOWN, UP, LineageError, lineage, render
from dbtransfer.config import load_bw_tables, load_db_settings
from dbtransfer.db import DbError, check_connection, connect, server_dialect
from dbtransfer.deploy import ACTION_TEXT, DeployError, deploy
from dbtransfer.generator import DIALECTS, Package, RulesError, TypeRules, generate
from dbtransfer.importers.bw_csv import SE16_HELP, ImportFailed, import_bw_export
from dbtransfer.model import Model, Severity
from dbtransfer.project import Project, ProjectError, find_project, init_project
from dbtransfer.reconcile import STATUSES, ReconcileError, reconcile
from dbtransfer.runtime import LoadError, RunError, load_file, rollback_load, run_load
from dbtransfer.scenario import ScenarioError, run_demo

# Коды возврата по ТЗ: 0 успех, 1 ошибка данных, 2 ошибка подключения или окружения
EXIT_DATA_ERROR = 1
EXIT_ENV_ERROR = 2


app = typer.Typer(
    help="DB-transfer: перенос структуры хранилища SAP BW в Greenplum/Arenadata DB.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="Работа с целевой базой.", no_args_is_help=True)
app.add_typer(db_app, name="db")

ProjectOption = Annotated[
    Path | None,
    typer.Option("--project", "-p", help="Папка проекта. По умолчанию текущая папка или demo/."),
]


def _version(value: bool) -> None:
    if value:
        typer.echo(f"dbtransfer {__version__}")
        raise typer.Exit()


def _fail(message: str, code: int = EXIT_DATA_ERROR) -> typer.Exit:
    typer.secho(f"Ошибка: {message}", fg=typer.colors.RED, err=True)
    return typer.Exit(code)


def _project(option: Path | None) -> Project:
    try:
        return find_project(option)
    except ProjectError as e:
        raise _fail(str(e), EXIT_ENV_ERROR) from None


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Показать версию.")
    ] = False,
) -> None:
    pass


@app.command("init")
def init_cmd(
    folder: Annotated[Path, typer.Argument(help="Папка нового проекта.")],
    name: Annotated[str | None, typer.Option(help="Название проекта.")] = None,
) -> None:
    """Создать проект: project.yaml, папка bw_export для выгрузок и data для тестовых данных."""
    try:
        project = init_project(folder, name)
    except ProjectError as e:
        raise _fail(str(e)) from None
    typer.echo(f"Проект создан: {project.config_path}, схемы в базе: {project.config.schema_prefix}_*")
    typer.echo(f"Положите CSV-выгрузки BW в {project.bw_export_dir} (список таблиц: dbtransfer tables) "
               f"и выполните: dbtransfer import -p {folder}")


@app.command("tables")
def tables_cmd() -> None:
    """Список таблиц BW для выгрузки и обязательные поля."""
    specs = load_bw_tables()
    group = None
    for spec in specs.values():
        if spec.group != group:
            group = spec.group
            typer.secho(f"\n{group}", bold=True)
        mark = "обязательная" if spec.required else "желательная"
        hyp = ", состав полей — гипотеза" if spec.hypothesis else ""
        typer.echo(f"  {spec.name:<12} {mark}{hyp}. {spec.purpose}")
        typer.echo(f"  {'':<12} поля: {', '.join(spec.required_columns)}")
    typer.echo("\n" + SE16_HELP)


@app.command("import")
def import_cmd(
    folder: Annotated[Path | None, typer.Argument(help="Папка с CSV-выгрузками. По умолчанию из project.yaml.")] = None,
    project: ProjectOption = None,
    encoding: Annotated[str | None, typer.Option(help="Кодировка вручную, например cp1251 или utf-8.")] = None,
    delimiter: Annotated[str | None, typer.Option(help="Разделитель вручную: ';', ',' или 'tab'.")] = None,
    language: Annotated[str, typer.Option(help="Язык текстов SAP: R русский, E английский.")] = "R",
) -> None:
    """Импорт и проверка выгрузок: модель в model.json, проверка файлов в out/import_check.xlsx."""
    proj = _project(project)
    source = folder or proj.bw_export_dir
    try:
        model = import_bw_export(source, language=language, encoding=encoding,
                                 delimiter="\t" if delimiter == "tab" else delimiter)
    except ImportFailed as e:
        raise _fail(str(e)) from None
    proj.save_model(model)
    check = write_import_check(model, proj.out_dir / "import_check.xlsx")

    typer.echo(f"Импорт из {source}")
    typer.echo(f"  InfoObject: {len(model.info_objects)}, ADSO: {len(model.adsos)}, "
               f"DataSource: {len(model.datasources)}, трансформации: {len(model.transformations)}, "
               f"DTP: {len(model.dtps)}, прочие объекты: {len(model.others)}")
    if model.inactive:
        typer.echo(f"  Пропущены неактивные: {', '.join(o.name for o in model.inactive)}")
    errors = [i for i in model.issues if i.severity == Severity.ERROR]
    warnings = [i for i in model.issues if i.severity == Severity.WARNING]
    for i in errors + warnings:
        where = f"{i.file}, строка {i.line}: " if i.file and i.line else (f"{i.file}: " if i.file else "")
        color = typer.colors.RED if i.severity == Severity.ERROR else typer.colors.YELLOW
        typer.secho(f"  {where}{i.message}", fg=color)
    typer.echo(f"Модель: {proj.model_path}")
    typer.echo(f"Проверка файлов: {check}")
    if errors:
        raise _fail(f"при импорте {len(errors)} ошибок, модель неполная; подробности в {check.name}")


@app.command("report")
def report_cmd(project: ProjectOption = None) -> None:
    """Отчёт о сложности в out/complexity.xlsx."""
    proj = _project(project)
    try:
        model = proj.load_model()
    except ProjectError as e:
        raise _fail(str(e)) from None
    report = assess(model)
    try:
        package = generate(model, TypeRules.load(proj.root))
        sql_lines, db_objects = package.sql_lines, package.db_objects
    except RulesError:
        sql_lines = db_objects = None
    path = write_complexity(model, report, proj.out_dir / "complexity.xlsx", sql_lines, db_objects)
    typer.echo(f"Объектов: {report.total}; уровень 1: {report.by_level[1]} ({report.level1_share:.0%}), "
               f"уровень 2: {report.by_level[2]}, уровень 3: {report.by_level[3]}")
    typer.echo(f"Ручная работа по нормам: {report.hours:g} ч")
    for r in report.routines:
        typer.echo(f"  Рутина: {r.kind}, {r.lines} строк, {r.source} → {r.target}")
    typer.echo(f"Отчёт: {path}")


@app.command("scope")
def scope_cmd(
    from_: Annotated[str | None, typer.Option("--from", help="Объект, от которого берётся поток.")] = None,
    project: ProjectOption = None,
) -> None:
    """Задать или показать состав переноса: поток от объекта вниз до источников."""
    proj = _project(project)
    try:
        model = proj.load_model()
    except ProjectError as e:
        raise _fail(str(e)) from None
    name = from_ or proj.config.scope_from
    if not name:
        raise _fail("состав переноса не задан: dbtransfer scope --from <объект>")
    try:
        scope = scope_from(model, name)
    except ScopeError as e:
        raise _fail(str(e)) from None
    if from_:
        proj.set_scope(from_)
        typer.echo(f"Состав переноса сохранён в {proj.config_path.name}: поток от {from_}")
    typer.echo(f"Объекты ({len(scope.objects)}): {' → '.join(r.name for r in scope.objects)}")
    typer.echo(f"Трансформации: {len(scope.transformations)}, DTP: {len(scope.dtps)}, "
               f"InfoObject: {len(scope.info_objects)}")


@app.command("lineage")
def lineage_cmd(
    target: Annotated[str, typer.Argument(help="Поле в виде ОБЪЕКТ.ПОЛЕ, например ZSD_A02.0NETVAL_INV.")],
    up: Annotated[bool, typer.Option("--up", help="Куда идёт поле.")] = False,
    down: Annotated[bool, typer.Option("--down", help="Откуда пришло поле (по умолчанию).")] = False,
    project: ProjectOption = None,
) -> None:
    """Lineage поля: откуда пришло (--down) или куда идёт (--up)."""
    if up and down:
        raise _fail("укажите что-то одно: --up или --down")
    if "." not in target:
        raise _fail("поле указывается как ОБЪЕКТ.ПОЛЕ, например ZSD_A02.0NETVAL_INV")
    obj, field_name = target.split(".", 1)
    proj = _project(project)
    try:
        model = proj.load_model()
        direction = UP if up else DOWN
        tree = lineage(model, obj, field_name, direction)
    except (ProjectError, ScopeError, LineageError) as e:
        raise _fail(str(e)) from None
    typer.echo(render(tree, direction))


@app.command("generate")
def generate_cmd(
    project: ProjectOption = None,
    dialect: Annotated[str, typer.Option(help=f"Диалект SQL: {', '.join(DIALECTS)}.")] = "greenplum",
    scope: Annotated[bool, typer.Option("--scope", help="Только состав переноса из project.yaml.")] = False,
) -> None:
    """Сгенерировать пакет SQL в out/sql/: DDL, функции активации и трансформаций."""
    if dialect not in DIALECTS:
        raise _fail(f"диалект {dialect} неизвестен, допустимо: {', '.join(DIALECTS)}")
    proj = _project(project)
    try:
        model = proj.load_model()
        rules = TypeRules.load(proj.root)
        if scope and not proj.config.scope_from:
            raise _fail("состав переноса не задан: dbtransfer scope --from <объект>")
        package = generate(model, rules, dialect, scope=proj.config.scope_from if scope else None)
    except (ProjectError, RulesError, ScopeError) as e:
        raise _fail(str(e)) from None
    out = proj.out_dir / "sql"
    paths = package.write(out)
    kinds = package.counts()
    lines = sum(p.read_text(encoding="utf-8").count("\n") for p in paths if p.name[0].isdigit())
    typer.echo(f"Схем: {kinds.get('schema', 0)}, таблиц: {kinds.get('table', 0)}, "
               f"функций: {kinds.get('function', 0)}; строк SQL: {lines}")
    for o in package.stubs:
        typer.secho(f"  Заготовка, требует ручной доработки: {o.bw_object} ({'; '.join(o.stub)})",
                    fg=typer.colors.YELLOW)
    for w in package.warnings:
        typer.secho(f"  {w}", fg=typer.colors.YELLOW)
    typer.echo(f"Пакет SQL: {out} (всё одним файлом: deploy_all.sql)")


EnvOption = Annotated[Path, typer.Option("--env-file", help="Файл с параметрами подключения.")]


def _connect(env_file: Path):
    try:
        return connect(load_db_settings(env_file))
    except DbError as e:
        raise _fail(str(e), EXIT_ENV_ERROR) from None


def _model_package(proj: Project, conn) -> tuple[Model, TypeRules, Package]:
    try:
        model = proj.load_model()
        rules = TypeRules.load(proj.root)
        return model, rules, generate(model, rules, server_dialect(conn))
    except (ProjectError, RulesError) as e:
        raise _fail(str(e)) from None


@app.command("deploy")
def deploy_cmd(
    project: ProjectOption = None,
    recreate: Annotated[bool, typer.Option("--recreate", help="Пересоздать таблицы (тестовые данные удалятся).")]
    = False,
    env_file: EnvOption = Path(".env"),
) -> None:
    """Развернуть пакет SQL в базе: схемы, таблицы, функции. Журнал — meta.deploy_log."""
    proj = _project(project)
    with _connect(env_file) as conn:
        model, rules, package = _model_package(proj, conn)
        package.write(proj.out_dir / "sql")
        typer.echo(f"Диалект: {package.dialect}, хеш пакета {package.sql_hash}")
        try:
            result = deploy(conn, package, recreate, out_dir=proj.out_dir)
        except DeployError as e:
            raise _fail(str(e)) from None
    counts = result.plan.counts()
    typer.echo("План: " + ", ".join(f"{ACTION_TEXT[a]} {n}" for a, n in sorted(counts.items())))
    for item in result.items:
        if item.action in ("create", "recreate") and item.obj.kind == "table":
            typer.echo(f"  {ACTION_TEXT[item.action]}: {item.obj.name}")
    if result.log_file:
        typer.echo(f"Журнал: {result.log_file}")
    if not result.ok:
        raise _fail(f"развёртывание отменено, {result.error}")
    typer.secho(f"Развёрнуто, номер развёртывания {result.deploy_id}", fg=typer.colors.GREEN)


@app.command("load")
def load_cmd(
    obj: Annotated[str, typer.Argument(help="DataSource (ZSALES_FF) или признак-справочник (0MATERIAL).")],
    file: Annotated[Path, typer.Argument(help="CSV-файл: первая строка — имена полей.")],
    force: Annotated[bool, typer.Option("--force", help="Загрузить файл, даже если он уже загружался.")] = False,
    project: ProjectOption = None,
    env_file: EnvOption = Path(".env"),
) -> None:
    """Загрузить CSV в базу и вывести номер загрузки (load_id)."""
    proj = _project(project)
    with _connect(env_file) as conn:
        model, rules, _ = _model_package(proj, conn)
        try:
            res = load_file(conn, model, rules, obj, file, force)
        except LoadError as e:
            typer.secho(f"Ошибка: {e}", fg=typer.colors.RED, err=True)
            for err in e.errors:
                typer.secho(f"  {err}", fg=typer.colors.RED, err=True)
            if e.total > len(e.errors):
                typer.secho(f"  … и ещё {e.total - len(e.errors)}", fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_DATA_ERROR) from None
    typer.echo(f"Загружено строк: {res.rows} в {res.table}")
    typer.secho(f"load_id: {res.load_id}", fg=typer.colors.GREEN)


@app.command("run")
def run_cmd(
    load_id: Annotated[int, typer.Argument(help="Номер загрузки из dbtransfer load.")],
    project: ProjectOption = None,
    env_file: EnvOption = Path(".env"),
) -> None:
    """Прогнать загрузку по потоку состава переноса: трансформации и активации, одной транзакцией."""
    proj = _project(project)
    with _connect(env_file) as conn:
        model, _, package = _model_package(proj, conn)
        try:
            result = run_load(conn, model, package, load_id, proj.config.scope_from,
                              progress=lambda s: typer.echo(f"  {s.title}: {s.rows} строк"))
        except (RunError, ScopeError) as e:
            raise _fail(str(e)) from None
    typer.secho(f"Загрузка {result.load_id} ({result.object_name}) активирована", fg=typer.colors.GREEN)


@app.command("rollback")
def rollback_cmd(
    load_id: Annotated[int, typer.Argument(help="Номер неактивированной загрузки.")],
    project: ProjectOption = None,
    env_file: EnvOption = Path(".env"),
) -> None:
    """Откатить загрузку, которая ещё не прогонялась: удалить её строки из входной таблицы."""
    proj = _project(project)
    with _connect(env_file) as conn:
        try:
            deleted = rollback_load(conn, proj.load_model(), load_id)
        except (RunError, ProjectError) as e:
            raise _fail(str(e)) from None
    typer.echo(f"Загрузка {load_id} откачена, удалено строк: {deleted}")


@app.command("reconcile")
def reconcile_cmd(
    obj: Annotated[str, typer.Argument(help="ADSO, например ZSD_A02.")],
    etalon: Annotated[Path, typer.Argument(help="CSV эталона с колонками по InfoObject.")],
    tolerance: Annotated[float | None, typer.Option(help="Допуск по показателям (по умолчанию 0,01).")] = None,
    project: ProjectOption = None,
    env_file: EnvOption = Path(".env"),
) -> None:
    """Сверить активные данные ADSO с эталоном; протокол в Excel и meta.reconcile_result."""
    proj = _project(project)
    with _connect(env_file) as conn:
        try:
            r = reconcile(conn, proj.load_model(), obj, etalon, proj.out_dir, proj.root, tolerance)
        except (ReconcileError, ProjectError) as e:
            raise _fail(str(e)) from None
    typer.echo(f"Сверка {obj} с {etalon.name}: " + ", ".join(f"{s} {r.count(s)}" for s in STATUSES))
    for _, row in r.problems.head(10).iterrows():
        key = " / ".join(str(row[k]) for k in r.spec.keys)
        diffs = ", ".join(f"{m} {row[f'{m}_diff']:+g}" for m in r.spec.measures if row[f"{m}_diff"])
        typer.echo(f"  {row['status']}: {key}" + (f" ({diffs})" if diffs else ""))
    for lower in r.lower:
        typer.echo(f"Нижний слой {lower.object_name}: " + ("совпал" if lower.ok else "тоже не совпал"))
    typer.echo(f"Протокол: {r.excel}")
    if not r.ok:
        raise typer.Exit(EXIT_DATA_ERROR)


@app.command("run-demo")
def run_demo_cmd(
    project: ProjectOption = None,
    env_file: EnvOption = Path(".env"),
) -> None:
    """Сквозной сценарий на демо-наборе: от импорта до сверки, с итоговыми метриками."""
    proj = _project(project)
    with _connect(env_file) as conn:
        try:
            s = run_demo(proj, conn, typer.echo, server_dialect(conn))
        except (ScenarioError, LoadError, RunError, ReconcileError, RulesError, ScopeError) as e:
            raise _fail(f"сценарий остановлен: {e}") from None
    r = s.report
    rows = [
        ("Объектов уровня 1", f"{r.by_level[1]} из {r.total} ({r.level1_share:.0%})"),
        ("Ручная работа по нормам", f"{r.hours:g} ч"),
        ("Строк SQL", str(s.package.sql_lines)),
        ("Объектов БД", str(s.package.db_objects)),
        ("Загрузок", ", ".join(f"{name}: {n} строк" for name, _, n in s.loads)),
        *[(f"Сверка {x.object_name}", f"совпало {x.count('совпало')} из {len(x.detail)}") for x in s.reconciles],
        ("Время", f"{s.seconds:.1f} с"),
    ]
    width = max(len(a) for a, _ in rows)
    typer.echo("\nИтог")
    for a, b in rows:
        typer.echo(f"  {a.ljust(width)}  {b}")
    if not s.ok:
        raise _fail("сверка нашла расхождения")
    typer.secho("Демо прошло: цифры совпали с эталоном", fg=typer.colors.GREEN)


@app.command("ui")
def ui_cmd(
    project: ProjectOption = None,
    env_file: EnvOption = Path(".env"),
    port: Annotated[int, typer.Option("--port", help="Порт веб-интерфейса.")] = 8501,
) -> None:
    """Веб-интерфейс: схема потоков, карточки, сложность, SQL, развёртывание, сверка. Открывается в браузере."""
    proj = _project(project)
    app_file = Path(__file__).parent / "ui" / "app.py"
    command = [sys.executable, "-m", "streamlit", "run", str(app_file), "--server.port", str(port),
               "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal",
               "--", "--project", str(proj.root), "--env-file", str(env_file.resolve())]
    typer.echo(f"Веб-интерфейс: http://localhost:{port} (остановить: Ctrl+C)")
    try:
        code = subprocess.call(command)
    except KeyboardInterrupt:
        code = 0
    raise typer.Exit(code)


@db_app.command("check")
def db_check(
    env_file: Annotated[Path, typer.Option("--env-file", help="Файл с параметрами подключения.")] = Path(".env"),
) -> None:
    """Проверить подключение к базе: версия СУБД и право создавать схемы."""
    settings = load_db_settings(env_file)
    typer.echo(f"Подключение: {settings.describe()}")
    try:
        info = check_connection(settings)
    except DbError as e:
        typer.secho(f"Ошибка: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ENV_ERROR) from None
    typer.echo(f"СУБД: {info.version}")
    if info.can_create_schema:
        typer.secho("Права на создание схем есть. Всё готово.", fg=typer.colors.GREEN)
    else:
        typer.secho(f"У пользователя {settings.user} нет права CREATE в базе {settings.dbname}.", fg=typer.colors.RED)
        raise typer.Exit(EXIT_ENV_ERROR)
