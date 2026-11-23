"""Перевод представлений модели: заголовок CREATE VIEW, текст запроса, порядок по зависимостям.

Результат каждого представления — Translation: SQL для цели, статус (автоматически, черновик, не разобрано),
список todo и что сделали правила. Автоматически переведённые представления идут в пакет SQL
(080_views.sql) и разворачиваются, черновики — в drafts_views.sql с пометками TODO.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dbtransfer.generator.sql import sql_comment
from dbtransfer.generator.types import TypeRules
from dbtransfer.model import Model, ObjectType, OtherObject
from dbtransfer.translate.names import NameMap, view_target
from dbtransfer.translate.sql import AUTO, FAILED, PARTIAL, SOURCE_DIALECTS, SOURCE_NAMES, translate_query

_NAME_PART = r'(?:"[^"]+"|\[[^\]]+\]|[\w$#]+)'
_VIEW_HEAD = re.compile(
    r"^\s*(?:CREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?(?:(?:NO\s+)?FORCE\s+)?(?:(?:NON)?EDITIONABLE\s+)?|REPLACE\s+)"
    rf"(?:RECURSIVE\s+)?VIEW\s+(?P<name>{_NAME_PART}(?:\s*\.\s*{_NAME_PART})*)\s*"
    r"(?:\((?P<cols>[^()]*)\))?\s*(?:WITH\s+\w+(?:\s*,\s*\w+)*\s+)?AS\s+", re.I | re.S)
_VIEW_TAIL = re.compile(r"\s*(?:WITH\s+(?:(?:CASCADED|LOCAL)\s+)?CHECK\s+OPTION(?:\s+CONSTRAINT\s+\S+)?|"
                        r"WITH\s+READ\s+ONLY(?:\s+CONSTRAINT\s+\S+)?)?\s*;?\s*$", re.I)
# Join-индекс Teradata и материализованное представление — не запрос: в отчёте о сложности, без перевода
_NOT_A_VIEW = re.compile(r"^\s*CREATE\s+(?:JOIN\s+INDEX|MATERIALIZED\s+VIEW)\b", re.I)
_LEADING_COMMENTS = re.compile(r"^(?:\s*--[^\n]*\n|\s*/\*.*?\*/)*", re.S)


@dataclass
class Translation:
    type: ObjectType
    name: str  # СХЕМА.ИМЯ в источнике
    target: str  # имя в целевой базе
    source_dialect: str
    status: str
    sql: str = ""  # DDL для цели (для черновика — с комментариями TODO)
    todos: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    lines: int = 0
    depends: list[str] = field(default_factory=list)  # представления модели, на которые ссылается
    label: str = "представление"
    body: str = ""  # переведённый запрос (или исходный, если не разобран)
    columns: str = ""  # « (a, b)» — список колонок из заголовка CREATE VIEW

    @property
    def auto(self) -> bool:
        return self.status == AUTO


def split_view(text: str) -> tuple[str, list[str]]:
    """Текст представления -> (запрос, колонки из заголовка). Oracle хранит только запрос, MS SQL и Teradata —
    весь CREATE / REPLACE VIEW."""
    body = _LEADING_COMMENTS.sub("", text, count=1)
    m = _VIEW_HEAD.match(body)
    columns: list[str] = []
    if m:
        if m.group("cols"):
            columns = [c.strip().strip('"[]').lower() for c in m.group("cols").split(",") if c.strip()]
        body = body[m.end():]
    body = _VIEW_TAIL.sub("", body)
    return body.strip(), columns


def translate_view(obj: OtherObject, model: Model, names: NameMap, dialect: str) -> Translation:
    source = SOURCE_DIALECTS[model.source_kind]
    schema, _, name = obj.name.partition(".")
    target = view_target(model, schema, name)
    query, columns = split_view(obj.text)
    result = translate_query(query, source, dialect, names, obj.schema_name or schema)
    t = Translation(ObjectType.VIEW, obj.name, target, source, result.status, todos=list(result.todos),
                    notes=list(result.notes), lines=obj.lines or obj.text.count("\n") + 1)
    if result.error:
        t.todos.insert(0, result.error)
    t.depends = [r.source for r in result.references if r.kind == "view"]
    cols = ""
    if columns:
        if dialect == "clickhouse":
            t.todos.append("список колонок в заголовке представления: в ClickHouse задайте псевдонимы в SELECT")
            t.status = PARTIAL if t.status == AUTO else t.status
        else:
            cols = " (" + ", ".join(c if re.fullmatch(r"[a-z_][a-z0-9_$]*", c) else f'"{c}"' for c in columns) + ")"
    t.body = result.sql if not result.error else query
    t.columns = cols
    t.sql = render(t)
    return t


def render(t: Translation) -> str:
    """DDL представления; у черновика — комментарии TODO и исходный текст, если он не разобран."""
    head = f"-- {SOURCE_NAMES[t.source_dialect]}: представление {t.name}"
    if t.notes:
        head += "\n" + sql_comment("переведено: " + "; ".join(t.notes))
    body, cols = t.body, t.columns
    ddl = f"CREATE OR REPLACE VIEW {t.target}{cols} AS\n{body};\n"
    if t.status == AUTO:
        return f"{head}\n{ddl}"
    todos = "\n".join(f"-- TODO: {x}" for x in t.todos)
    if t.status == FAILED:
        return f"{head}\n{todos}\n-- Исходный текст:\n/*\n{body}\n*/\n"
    return f"{head}\n{todos}\n{ddl}"


def translate_views(model: Model, dialect: str, rules: TypeRules | None = None,
                    names: NameMap | None = None) -> list[Translation]:
    """Все представления модели с текстом, в порядке создания (сначала те, на которые ссылаются)."""
    if model.source_kind not in SOURCE_DIALECTS or model.is_bw:
        return []
    from dbtransfer.model import SourceKind

    if model.source_kind == SourceKind.GREENPLUM:
        return []  # та же семья: перевода нет (см. assessment, view_same_family)
    names = names or NameMap(model, rules)
    views = [o for o in model.others if o.type == ObjectType.VIEW and o.text.strip() and "." in o.name
             and not _NOT_A_VIEW.match(o.text)]
    result = {o.name: translate_view(o, model, names, dialect) for o in sorted(views, key=lambda o: o.name)}
    # Представление на черновике или не из модели — тоже черновик: иначе развёртывание упадёт
    changed = True
    while changed:
        changed = False
        for t in result.values():
            if t.status != AUTO:
                continue
            bad = [d for d in t.depends if d in result and result[d].status != AUTO]
            if bad:
                t.status = PARTIAL
                t.todos.append(f"ссылается на представление, переведённое не полностью: {', '.join(bad)}")
                t.sql = render(t)
                changed = True
    ordered: list[Translation] = []
    seen: set[str] = set()

    def visit(name: str, stack: tuple[str, ...] = ()) -> None:
        if name in seen or name not in result or name in stack:
            return
        for d in result[name].depends:
            visit(d, (*stack, name))
        seen.add(name)
        ordered.append(result[name])

    for name in result:
        visit(name)
    return ordered


__all__ = ["AUTO", "FAILED", "PARTIAL", "Translation", "render", "split_view", "translate_view", "translate_views"]
