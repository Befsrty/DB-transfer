"""Командная строка dbtransfer (ТЗ, раздел 4.4.2). Команды добавляются по шагам плана."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dbtransfer import __version__
from dbtransfer.analysis.assessment import assess
from dbtransfer.analysis.excel import write_complexity, write_import_check
from dbtransfer.analysis.graph import ScopeError, scope_from
from dbtransfer.analysis.lineage import DOWN, UP, LineageError, lineage, render
from dbtransfer.config import load_bw_tables, load_db_settings
from dbtransfer.db import DbError, check_connection
from dbtransfer.importers.bw_csv import ImportFailed, import_bw_export
from dbtransfer.model import Severity
from dbtransfer.project import Project, ProjectError, find_project, init_project

# Коды возврата по ТЗ: 0 успех, 1 ошибка данных, 2 ошибка подключения или окружения
EXIT_DATA_ERROR = 1
EXIT_ENV_ERROR = 2

SE16_HELP = """\
Как выгрузить таблицу через SE16:
  1. Транзакция SE16 (или SE16N), введите имя таблицы.
  2. Включите технические имена полей: Настройки → Параметры пользователя → «Имя поля».
  3. В «Макс. число совпадений» оставьте пусто, чтобы выгрузить все строки. Для DD03L
     ограничьте TABNAME таблицами ADSO и справочников (/BIC/A*, /BI0/P*, /BI0/T*, /BIC/P*, /BIC/T*).
  4. Выполните (F8), затем Система → Список → Сохранить → Локальный файл,
     формат «Электронная таблица» или «Текст с табуляцией».
  5. Сохраните файл под именем таблицы: RSDIOBJ.csv или RSDIOBJ.txt, все файлы в одну папку.
Кодировка (UTF-8 или Windows-1251) и разделитель (; , или табуляция) определяются автоматически.
Подробнее: docs/bw-export.md"""

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
    """Создать проект: project.yaml и папку bw_export для выгрузок."""
    try:
        project = init_project(folder, name)
    except ProjectError as e:
        raise _fail(str(e)) from None
    typer.echo(f"Проект создан: {project.config_path}")
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
    path = write_complexity(model, report, proj.out_dir / "complexity.xlsx")
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
