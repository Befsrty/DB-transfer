"""Развёртывание пакета SQL в базе (ТЗ, ФП-6): план, пересоздание, журнал deploy_log.

Порядок объектов задаёт генератор (схемы, служебные таблицы, справочники, входные таблицы,
таблицы ADSO, функции активации, трансформации). Всё выполняется одной транзакцией:
при ошибке база остаётся как была, а в журнал пишется, на каком объекте упало.
"""

from __future__ import annotations

import csv
import getpass
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psycopg

from dbtransfer.generator import Package, SqlObject

# Действия над объектом в плане и журнале
CREATE = "create"      # объекта нет, будет создан
KEEP = "keep"          # таблица уже есть и останется как есть (CREATE TABLE IF NOT EXISTS)
REPLACE = "replace"    # функция заменяется (CREATE OR REPLACE)
RECREATE = "recreate"  # пересоздание с потерей данных (--recreate)

ACTION_TEXT = {CREATE: "создать", KEEP: "оставить как есть", REPLACE: "заменить", RECREATE: "пересоздать"}


class DeployError(Exception):
    """Развёртывание не выполнено; текст понятен пользователю."""


@dataclass
class PlanItem:
    obj: SqlObject
    exists: bool
    action: str
    status: str = ""  # ok, error, skipped — после выполнения
    message: str = ""


@dataclass
class DeployPlan:
    items: list[PlanItem]
    new_hash: str
    last_hash: str | None
    recreate: bool

    @property
    def model_changed(self) -> bool:
        return self.last_hash is not None and self.last_hash != self.new_hash

    @property
    def kept_tables(self) -> list[PlanItem]:
        return [i for i in self.items if i.obj.kind == "table" and i.action == KEEP and i.obj.group != "meta"]

    @property
    def blocked(self) -> bool:
        """ФП-6.3: модель изменилась, а таблицы с данными остались бы старыми."""
        return self.model_changed and not self.recreate and bool(self.kept_tables)

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for i in self.items:
            result[i.action] = result.get(i.action, 0) + 1
        return result


@dataclass
class DeployResult:
    deploy_id: int | None
    plan: DeployPlan
    ok: bool
    error: str = ""
    items: list[PlanItem] = field(default_factory=list)
    log_file: Path | None = None  # копия журнала в out/


def _exists(cur: psycopg.Cursor, obj: SqlObject) -> bool:
    if obj.kind == "schema":
        name = obj.name.strip('"')
        return cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)).fetchone() is not None
    if obj.kind == "function":
        return cur.execute("SELECT to_regprocedure(%s) IS NOT NULL", (obj.name,)).fetchone()[0]
    # Последовательности создаются одним блоком: смотрим по первой
    name = obj.name.split(", ")[0]
    return cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone()[0]


def last_deployed_hash(conn: psycopg.Connection, package: Package) -> str | None:
    """Хеш пакета последнего успешного развёртывания или None, если развёртываний не было."""
    log = f"{package.meta_schema}.deploy_log"
    with conn.cursor() as cur:
        if not cur.execute("SELECT to_regclass(%s) IS NOT NULL", (log,)).fetchone()[0]:
            return None
        rows = cur.execute(
            f"SELECT deploy_id, bool_and(status = 'ok'), max(model_hash) FROM {log} "
            "GROUP BY deploy_id ORDER BY deploy_id DESC").fetchall()
    return next((h for _, ok, h in rows if ok), None)


def make_plan(conn: psycopg.Connection, package: Package, recreate: bool = False) -> DeployPlan:
    """Что будет создано, оставлено и пересоздано (ФП-6.2). База не меняется."""
    items = []
    with conn.cursor() as cur:
        for obj in package.objects:
            exists = _exists(cur, obj)
            if obj.kind == "function":
                action = REPLACE if exists else CREATE
            elif not exists:
                action = CREATE
            elif recreate and obj.kind == "table" and obj.group != "meta":
                action = RECREATE
            else:
                action = KEEP
            items.append(PlanItem(obj, exists, action))
    conn.rollback()
    return DeployPlan(items, package.sql_hash, last_deployed_hash(conn, package), recreate)


def _drop_data_schemas(cur: psycopg.Cursor, package: Package) -> None:
    """Пересоздание: схемы с данными удаляются целиком, журнал загрузок очищается.
    Журналы развёртываний и сверок остаются для истории."""
    for schema in package.data_schemas:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    log = f"{package.meta_schema}.load_log"
    if cur.execute("SELECT to_regclass(%s) IS NOT NULL", (log,)).fetchone()[0]:
        cur.execute(f"DELETE FROM {log}")


def _write_log(conn: psycopg.Connection, package: Package, plan: DeployPlan, user: str) -> int | None:
    meta = package.meta_schema
    with conn.cursor() as cur:
        if not cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{meta}.deploy_log",)).fetchone()[0]:
            return None  # упали раньше, чем создалась служебная схема
        deploy_id = cur.execute(f"SELECT nextval('{meta}.deploy_id_seq')").fetchone()[0]
        cur.executemany(
            f"INSERT INTO {meta}.deploy_log (deploy_id, model_hash, object_name, action, status, message, "
            "deployed_by, deployed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, now())",
            [(deploy_id, plan.new_hash, i.obj.name, i.action, i.status, i.message or None, user)
             for i in plan.items])
    conn.commit()
    return deploy_id


def save_log(result: DeployResult, out_dir: Path, now: datetime | None = None) -> Path:
    """Копия журнала развёртывания в папке проекта (ФП-1.3): out/deploy_<номер>_<время>.csv."""
    now = now or datetime.now()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"deploy_{result.deploy_id or 'failed'}_{now:%Y%m%d_%H%M%S}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["deploy_id", "model_hash", "object_name", "kind", "action", "status", "message", "deployed_at"])
        for i in result.items:
            w.writerow([result.deploy_id or "", result.plan.new_hash, i.obj.name, i.obj.kind, i.action, i.status,
                        i.message, f"{now:%Y-%m-%d %H:%M:%S}"])
    return path


def deploy(conn: psycopg.Connection, package: Package, recreate: bool = False,
           progress: Callable[[PlanItem], None] | None = None, out_dir: Path | None = None) -> DeployResult:
    """Развернуть пакет. DeployError, если модель изменилась, а --recreate не задан (ФП-6.3).
    С out_dir журнал развёртывания сохраняется и в папку проекта (save_log)."""
    plan = make_plan(conn, package, recreate)
    if plan.blocked:
        names = ", ".join(i.obj.name for i in plan.kept_tables)
        raise DeployError(
            f"Модель или правила изменились с последнего развёртывания (хеш {plan.last_hash} → {plan.new_hash}), "
            f"а таблицы уже есть и без пересоздания останутся старыми: {names}. "
            "Чтобы пересоздать их (тестовые данные удалятся): dbtransfer deploy --recreate")
    user = getpass.getuser()
    failed = None
    try:
        with conn.cursor() as cur:
            if recreate:
                _drop_data_schemas(cur, package)
            for item in plan.items:
                failed = item
                cur.execute(item.obj.sql)
                item.status = "ok"
                if progress:
                    progress(item)
        conn.commit()
        failed = None
    except psycopg.Error as e:
        conn.rollback()
        message = (e.diag.message_primary or str(e)).strip()
        for item in plan.items:
            item.status = "skipped" if item.status == "" else "rolled back"
        if failed is not None:
            failed.status, failed.message = "error", message
        deploy_id = _write_log(conn, package, plan, user)
        where = f" на объекте {failed.obj.name}" if failed else ""
        result = DeployResult(deploy_id, plan, False, f"ошибка{where}: {message}", plan.items)
    else:
        result = DeployResult(_write_log(conn, package, plan, user), plan, True, "", plan.items)
    if out_dir is not None:
        result.log_file = save_log(result, out_dir)
    return result
