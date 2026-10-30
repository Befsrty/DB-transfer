"""Проект DB-transfer: папка с project.yaml, моделью model.json и результатами в out/ (ТЗ, ФП-1)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from dbtransfer.model import Model

PROJECT_FILE = "project.yaml"
MODEL_FILE = "model.json"
OUT_DIR = "out"

TEMPLATE = """\
# Проект DB-transfer (ТЗ, ФП-1.1). Пути относительно этого файла.
name: {name}
bw_version: BW/4HANA
# Схемы в базе: <prefix>_stg, _md, _dwh, _mart, _meta. У демо-проекта префикс dbt: свой, чтобы не пересечься
schema_prefix: {prefix}
# Папка с CSV-выгрузками таблиц BW (список таблиц: dbtransfer tables)
bw_export: bw_export
data: data
# Профиль подключения: переменные DBT_DB_* из .env
db_profile: DBT_DB
# Состав переноса: поток от этого объекта вниз до источников (dbtransfer scope --from <объект>)
scope_from:
"""


class ProjectError(Exception):
    """Проект не найден или его файлы испорчены; текст понятен пользователю."""


class ProjectConfig(BaseModel):
    name: str = ""
    bw_version: str = "BW/4HANA"
    schema_prefix: str = "dbt"
    bw_export: str = "bw_export"
    data: str = "data"
    db_profile: str = "DBT_DB"
    scope_from: str | None = None


class Project:
    def __init__(self, root: Path, config: ProjectConfig):
        self.root = root
        self.config = config

    @property
    def config_path(self) -> Path:
        return self.root / PROJECT_FILE

    @property
    def model_path(self) -> Path:
        return self.root / MODEL_FILE

    @property
    def out_dir(self) -> Path:
        path = self.root / OUT_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def bw_export_dir(self) -> Path:
        return self.root / self.config.bw_export

    def load_model(self) -> Model:
        if not self.model_path.is_file():
            raise ProjectError(f"В проекте {self.root} ещё нет модели. Сначала выполните dbtransfer import")
        try:
            return Model.load(self.model_path)
        except ValueError as e:
            raise ProjectError(str(e)) from e

    def save_model(self, model: Model) -> None:
        model.name = self.config.name
        model.bw_version = self.config.bw_version
        model.schema_prefix = self.config.schema_prefix
        model.save(self.model_path)

    def set_scope(self, obj: str | None) -> None:
        """Записать состав переноса в project.yaml, сохранив комментарии файла."""
        text = self.config_path.read_text(encoding="utf-8")
        line = f"scope_from: {obj or ''}".rstrip()
        if re.search(r"^scope_from:.*$", text, flags=re.MULTILINE):
            text = re.sub(r"^scope_from:.*$", line, text, flags=re.MULTILINE)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"
        self.config_path.write_text(text, encoding="utf-8")
        self.config.scope_from = obj


def schema_prefix_for(folder_name: str) -> str:
    """Префикс схем нового проекта из имени папки: латиница, цифры и «_», не длиннее 20 символов.
    Совпасть с префиксом демо-проекта dbt он не может."""
    prefix = re.sub(r"[^a-z0-9]+", "_", folder_name.lower()).strip("_")[:20].strip("_")
    if not prefix or prefix == "dbt":
        return "proj"
    return prefix if prefix[0].isalpha() else f"p_{prefix}"[:20]


def init_project(folder: Path, name: str | None = None) -> Project:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PROJECT_FILE
    if path.exists():
        raise ProjectError(f"Проект уже существует: {path}")
    folder_name = folder.resolve().name
    path.write_text(TEMPLATE.format(name=name or folder_name, prefix=schema_prefix_for(folder_name)),
                    encoding="utf-8")
    (folder / "bw_export").mkdir(exist_ok=True)
    (folder / "data").mkdir(exist_ok=True)
    return open_project(folder)


def open_project(folder: Path) -> Project:
    path = folder / PROJECT_FILE
    if not path.is_file():
        raise ProjectError(f"В папке {folder} нет {PROJECT_FILE}. Создайте проект: dbtransfer init {folder}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config = ProjectConfig(**raw)
    except (yaml.YAMLError, ValidationError, TypeError) as e:
        raise ProjectError(f"{path}: файл проекта не читается: {e}") from e
    return Project(folder, config)


def find_project(option: Path | None = None) -> Project:
    """Проект из параметра --project, переменной DBT_PROJECT, текущей папки или демо-проекта demo/."""
    if option is not None:
        return open_project(option)
    if os.environ.get("DBT_PROJECT"):
        return open_project(Path(os.environ["DBT_PROJECT"]))
    for candidate in (Path("."), Path("demo")):
        if (candidate / PROJECT_FILE).is_file():
            return open_project(candidate)
    raise ProjectError("Проект не найден: укажите --project <папка> или создайте проект: dbtransfer init <папка>")
