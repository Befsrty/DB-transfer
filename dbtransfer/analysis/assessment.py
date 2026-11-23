"""Уровни автоматизации объектов и трудоёмкость ручной части (ТЗ, 4.3.2, ФП-3.1…3.7).

Уровень 1 — переводится в SQL автоматически, 2 — частично или черновиком, 3 — только выявляется.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

from dbtransfer.analysis.formulas import unsupported_functions
from dbtransfer.config import RULES_DIR
from dbtransfer.model import (
    AdsoKind,
    InfoObjectKind,
    Model,
    ObjectType,
    RoutineKind,
    RuleKind,
    Severity,
    SourceKind,
    Transformation,
)

ROUTINE_LABELS = {
    RoutineKind.START: "стартовая рутина ABAP",
    RoutineKind.END: "конечная рутина ABAP",
    RoutineKind.EXPERT: "экспертная рутина ABAP",
    RoutineKind.FIELD: "рутина поля ABAP",
}
ADSO_LABELS = {
    AdsoKind.STAGING: "ADSO staging",
    AdsoKind.STANDARD: "ADSO standard",
    AdsoKind.CUMULATIVE: "ADSO cumulative",
    AdsoKind.DIRECT_UPDATE: "ADSO direct update",
}
IOBJ_LABELS = {
    InfoObjectKind.CHARACTERISTIC: "InfoObject: признак",
    InfoObjectKind.TIME: "InfoObject: время",
    InfoObjectKind.UNIT: "InfoObject: единица или валюта",
    InfoObjectKind.KEY_FIGURE: "InfoObject: показатель",
}
OTHER = {
    # тип: подпись, уровень, причина, норма трудоёмкости
    ObjectType.COMPOSITE_PROVIDER: ("CompositeProvider", 2, "CompositeProvider не генерируется в прототипе",
                                    "composite_provider"),
    ObjectType.CLASSIC_DSO: ("Классический DSO", 2, "объект BW 7.x, не генерируется в прототипе", "classic_dso"),
    ObjectType.QUERY: ("BEx-запрос", 3, "BEx-запрос", "query"),
    ObjectType.HIERARCHY: ("Иерархия", 3, "иерархия", "hierarchy"),
    ObjectType.PROCESS_CHAIN: ("Цепочка процессов", 3, "цепочка процессов", "process_chain"),
    ObjectType.VIEW: ("Представление", 2, "SQL представления переписывается под Greenplum вручную", "view"),
    ObjectType.DB_ROUTINE: ("Процедура, функция, триггер", 3, "код PL/SQL или T-SQL переносится вручную",
                            "db_routine"),
    ObjectType.EXTERNAL_TABLE: ("Внешняя таблица", 2, "внешняя таблица (gpfdist, PXF, s3) создаётся вручную: "
                                "адреса и серверы источника в новой среде другие", "external_table"),
    ObjectType.SCRIPT: ("Скрипт загрузки", 3, "скрипт BTEQ / FastLoad / MultiLoad / TPT переписывается: SQL Teradata "
                        "и команды утилиты (.IF, .EXPORT, BEGIN LOADING) — на psql, gpload или gpfdist", "script"),
}
# Teradata: код на SQL Teradata (макросы, процедуры SPL, UDF), перевод на PL/pgSQL
OTHER_TERADATA = {
    ObjectType.VIEW: ("Представление", 2, "SQL Teradata (QUALIFY, SEL, FORMAT, даты как числа) переписывается "
                      "под Greenplum", "view"),
    ObjectType.DB_ROUTINE: ("Процедура, функция, триггер", 3, "макрос, процедура SPL, UDF или триггер Teradata "
                            "переписывается на PL/pgSQL или SQL-функцию", "db_routine"),
}
# Источник той же семьи (Greenplum, Yandex MPP, Cloudberry): код и представления на том же языке, перевода нет,
# остаётся проверка на целевой версии (Greenplum 6 — PostgreSQL 9.4, Greengage 7 и ADB 7 — PostgreSQL 12)
OTHER_SAME_FAMILY = {
    ObjectType.VIEW: ("Представление", 1, "SQL переносится как есть после таблиц; проверить на целевой версии",
                      "view_same_family"),
    ObjectType.DB_ROUTINE: ("Процедура, функция, триггер", 2,
                            "код на том же языке (PL/pgSQL, SQL) переносится без перевода, проверяется "
                            "на целевой версии (6 → 7: PostgreSQL 9.4 → 12)", "db_routine_same_family"),
}
# Подписи для моделей из словаря Oracle и MS SQL Server: ADSO — целевая таблица, DataSource — входная
TABLE_LABELS = {AdsoKind.STANDARD: "Таблица", AdsoKind.STAGING: "Таблица без ключа"}
SOURCE_TABLE_LABEL = "Входная таблица"
# Порядок типов в сводке (ТЗ, ФП-3.1)
TYPE_ORDER = [*IOBJ_LABELS.values(), *ADSO_LABELS.values(), "DataSource", "Трансформация", "DTP",
              "CompositeProvider", "BEx-запрос", "Иерархия", "Цепочка процессов", "Классический DSO",
              *TABLE_LABELS.values(), SOURCE_TABLE_LABEL, "Представление", "Процедура, функция, триггер",
              "Внешняя таблица", "Скрипт загрузки",
              "Не разобрано"]


@cache
def load_effort_norms(path: Path = RULES_DIR / "effort_norms.yaml") -> dict[str, float]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass
class Assessment:
    type: str
    name: str
    level: int
    reasons: list[str] = field(default_factory=list)
    hours: float = 0.0
    description: str = ""

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


@dataclass
class RoutineInfo:
    transformation: str
    source: str
    target: str
    kind: str
    code_id: str
    lines: int
    code: str | None


@dataclass
class TranslationSummary:
    """Итог переводчика SQL: сколько переведено автоматически и сколько ручной работы осталось."""

    dialect: str
    views: int = 0
    views_auto: int = 0
    views_draft: int = 0
    routines: int = 0  # процедуры, функции, члены пакетов, макросы
    routine_statements: int = 0
    routine_statements_auto: int = 0
    lines: int = 0  # строк кода у объектов с текстом
    lines_auto: float = 0.0  # строк, переведённых без TODO (по доле операторов)
    todos: int = 0
    hours_manual: float = 0.0  # ручная работа по прежним нормам (переписать всё вручную)
    hours_left: float = 0.0  # ручная работа после перевода
    no_text: int = 0  # представления и процедуры без текста в выгрузке: переводить нечего

    @property
    def auto_share(self) -> float:
        return self.lines_auto / self.lines if self.lines else 0.0

    @property
    def views_share(self) -> float:
        return self.views_auto / self.views if self.views else 0.0

    @property
    def statements_share(self) -> float:
        return self.routine_statements_auto / self.routine_statements if self.routine_statements else 0.0


@dataclass
class Report:
    objects: list[Assessment]
    routines: list[RoutineInfo]
    flat_files: list[str]
    unparsed: list[Assessment]
    translation: TranslationSummary | None = None

    @property
    def by_type(self) -> Counter:
        return Counter(a.type for a in self.objects + self.unparsed)

    @property
    def by_level(self) -> Counter:
        return Counter(a.level for a in self.objects + self.unparsed)

    @property
    def total(self) -> int:
        return len(self.objects) + len(self.unparsed)

    @property
    def level1_share(self) -> float:
        return self.by_level[1] / self.total if self.total else 0.0

    @property
    def hours(self) -> float:
        return sum(a.hours for a in self.objects + self.unparsed)


def transformation_label(model: Model, t: Transformation) -> str:
    return f"{t.source.name} → {t.target.name}"


def assess_transformation(t: Transformation, norms: dict[str, float]) -> Assessment:
    a = Assessment(type="Трансформация", name=t.id, level=1)
    for r in t.all_routines:
        a.level = 3
        lines = f", {r.lines} строк" if r.code else ", кода нет в выгрузке"
        a.reasons.append(f"{ROUTINE_LABELS[r.kind]}{lines}")
        a.hours += norms["routine_abap"] + norms["routine_abap_per_10_lines"] * math.ceil(r.lines / 10)
    for rule in t.rules:
        if rule.kind == RuleKind.UNKNOWN:
            a.level = 3
            a.reasons.append(f"правило {rule.rule_id} не разобрано (вид «{rule.raw_kind}»)")
            a.hours += norms["unknown_rule"]
        elif rule.kind == RuleKind.READ_MD:
            a.level = max(a.level, 2)
            a.reasons.append(f"чтение атрибута {rule.param or ''} в {', '.join(rule.target_fields)}".rstrip())
            a.hours += norms["read_md_rule"]
        elif rule.kind == RuleKind.FORMULA:
            bad = unsupported_functions(rule.param or "")
            if bad:
                a.level = max(a.level, 2)
                a.reasons.append(f"формула не поддерживается ({', '.join(bad)}): {rule.param}")
                a.hours += norms["unsupported_formula"]
    if a.level == 1:
        kinds = {r.kind for r in t.rules}
        names = {RuleKind.DIRECT: "прямые присвоения", RuleKind.CONSTANT: "константы",
                 RuleKind.FORMULA: "формулы из словаря", RuleKind.TIME: "преобразования времени"}
        a.reasons.append(", ".join(label for k, label in names.items() if k in kinds) or "нет правил")
    return a


def assess(model: Model, dialect: str | None = None) -> Report:
    """Отчёт о сложности. dialect — цель переводчика SQL (по умолчанию ADB 7.5, greenplum7)."""
    from dbtransfer.generator.dialects import DEFAULT_DIALECT

    dialect = dialect or DEFAULT_DIALECT
    norms = load_effort_norms()
    objects: list[Assessment] = []
    for iobj in sorted(model.info_objects.values(), key=lambda i: i.name):
        objects.append(Assessment(type=IOBJ_LABELS[iobj.kind], name=iobj.name, level=1,
                                  reasons=["генерируется автоматически"], description=iobj.description))
    for adso in sorted(model.adsos.values(), key=lambda a: a.name):
        label = ADSO_LABELS[adso.kind] if model.is_bw else TABLE_LABELS.get(adso.kind, ADSO_LABELS[adso.kind])
        objects.append(Assessment(type=label, name=adso.name, level=1,
                                  reasons=["генерируется автоматически"], description=adso.description))
    for t in sorted(model.tables.values(), key=lambda t: t.key_name):
        reasons = ["генерируется автоматически, данные загружаются напрямую"]
        if t.partitioning is not None:
            reasons.append(f"секционирование {t.partitioning.kind} ({len(t.partitioning.partitions)} секций)")
        objects.append(Assessment(type=TABLE_LABELS[AdsoKind.STANDARD if t.key else AdsoKind.STAGING],
                                  name=t.key_name, level=1, reasons=reasons, description=t.description))
    flat_files = []
    for ds in sorted(model.datasources.values(), key=lambda d: d.ref.key):
        if not model.is_bw:
            flat_files.append(ds.ref.key)
            objects.append(Assessment(type=SOURCE_TABLE_LABEL, name=ds.ref.key, level=1,
                                      reasons=[f"данные таблицы {model.source_label} загружаются CSV-выгрузкой"],
                                      description=ds.description))
        elif ds.is_flat_file:
            flat_files.append(ds.ref.key)
            objects.append(Assessment(type="DataSource", name=ds.ref.key, level=1, reasons=["плоский файл"],
                                      description=ds.description))
        else:
            objects.append(Assessment(type="DataSource", name=ds.ref.key, level=2,
                                      reasons=[f"источник «{ds.source_type}», не плоский файл"],
                                      hours=norms["non_file_datasource"], description=ds.description))
    routines = []
    for t in sorted(model.transformations.values(), key=lambda t: (t.source.name, t.target.name)):
        a = assess_transformation(t, norms)
        a.description = transformation_label(model, t)
        objects.append(a)
        for r in t.all_routines:
            routines.append(RoutineInfo(transformation=t.id, source=t.source.key, target=t.target.key,
                                        kind=ROUTINE_LABELS[r.kind], code_id=r.code_id, lines=r.lines, code=r.code))
    for d in sorted(model.dtps.values(), key=lambda d: d.name):
        mode = {"FULL": "полная", "DELTA": "дельта"}.get(d.mode, d.mode)
        objects.append(Assessment(type="DTP", name=d.name, level=1, reasons=[f"{mode} загрузка"],
                                  description=f"{d.source.name} → {d.target.name}"))
    translated = _translations(model, dialect)
    summary = TranslationSummary(dialect) if translated is not None else None
    for o in sorted(model.others, key=lambda o: (o.type.value, o.name)):
        if summary is not None and o.type in (ObjectType.VIEW, ObjectType.DB_ROUTINE):
            items = translated.get(o.name, [])
            if items:
                objects.append(_assess_translated(o, items, norms, summary))
                continue
            if not o.text.strip():
                summary.no_text += 1
        known = OTHER_SAME_FAMILY if model.source_kind == SourceKind.GREENPLUM and o.type in OTHER_SAME_FAMILY \
            else OTHER_TERADATA if model.source_kind == SourceKind.TERADATA and o.type in OTHER_TERADATA else OTHER
        label, level, reason, norm = known.get(o.type, (o.type.value, 3, "прочий объект", "unparsed_object"))
        hours = norms[norm]
        if o.lines and f"{norm}_per_50_lines" in norms:
            hours += norms[f"{norm}_per_50_lines"] * math.ceil(o.lines / 50)
        objects.append(Assessment(type=label, name=o.name, level=level, reasons=[reason], hours=hours,
                                  description=o.info))

    unparsed = []
    for i in model.issues:
        if i.skipped and i.severity != Severity.INFO:
            where = f"{i.file}, строка {i.line}" if i.file and i.line else (i.file or "")
            unparsed.append(Assessment(type="Не разобрано", name=i.object or where, level=3,
                                       reasons=[i.message], hours=norms["unparsed_object"], description=where))
    return Report(objects=objects, routines=routines, flat_files=flat_files, unparsed=unparsed, translation=summary)


def _manual_hours(o, norms: dict[str, float]) -> float:
    norm = "view" if o.type == ObjectType.VIEW else "db_routine"
    return norms[norm] + norms[f"{norm}_per_50_lines"] * math.ceil(o.lines / 50) if o.lines else norms[norm]


def _assess_translated(o, items: list, norms: dict[str, float], s: TranslationSummary) -> Assessment:
    """Объект с переводом: уровень и часы по результату переводчика, а не по норме «переписать вручную»."""
    from dbtransfer.translate import AUTO, FAILED

    manual = _manual_hours(o, norms)
    lines = o.lines or o.text.count("\n") + 1
    todos = [t for i in items for t in i.todos]
    s.lines += lines
    s.todos += len(todos)
    s.hours_manual += manual
    label = "Представление" if o.type == ObjectType.VIEW else "Процедура, функция, триггер"
    if o.type == ObjectType.VIEW:
        t = items[0]
        s.views += 1
        if t.status == AUTO:
            s.views_auto += 1
            s.lines_auto += lines
            hours = norms["view_translated"] + norms["view_translated_per_50_lines"] * math.ceil(lines / 50)
            reasons = ["переведено автоматически, в пакете 080_views.sql"] + (
                ["правила: " + "; ".join(t.notes)] if t.notes else [])
            level = 1
        elif t.status == FAILED:
            hours, level = manual, 3
            reasons = ["не разобрано переводчиком: " + "; ".join(todos)]
        else:
            s.views_draft += 1
            hours = min(manual, norms["view_translated"] + norms["translation_todo"] * len(todos))
            level = 2
            reasons = ["черновик в drafts_views.sql, TODO: " + "; ".join(todos)]
    else:
        s.routines += len(items)
        stmts = sum(i.statements for i in items)
        ok = sum(i.auto_statements for i in items)
        s.routine_statements += stmts
        s.routine_statements_auto += ok
        share = ok / stmts if stmts else 0.0
        if all(i.status == FAILED for i in items):
            hours, level = manual, 3
            reasons = ["не переведено: " + "; ".join(todos)]
        else:
            s.lines_auto += lines * share
            review = norms["routine_draft_review"] + norms["routine_draft_review_per_50_lines"] * math.ceil(lines / 50)
            hours = min(manual, review + norms["translation_todo"] * len(todos))
            level = 2
            what = f"черновик PL/pgSQL в drafts_routines.sql: операторов {stmts}, без TODO {ok} ({share:.0%})"
            reasons = [what] + ([f"TODO ({len(todos)}): " + "; ".join(todos[:5]) + (" …" if len(todos) > 5 else "")]
                                if todos else [])
    s.hours_left += hours
    return Assessment(type=label, name=o.name, level=level, reasons=reasons, hours=hours, description=o.info)


def _translations(model: Model, dialect: str) -> dict[str, list] | None:
    """Переводы объектов модели по имени объекта источника (у пакета — все его члены).
    None — перевода для этого источника нет (SAP BW, Greenplum той же семьи)."""
    from dbtransfer.translate import SOURCE_DIALECTS, translate_model

    if model.is_bw or model.source_kind == SourceKind.GREENPLUM or model.source_kind not in SOURCE_DIALECTS:
        return None
    if not any(o.type in (ObjectType.VIEW, ObjectType.DB_ROUTINE) for o in model.others):
        return None
    result: dict[str, list] = {}
    names = {o.name.upper(): o.name for o in model.others}
    for t in translate_model(model, dialect):
        name = t.name
        if "__" in name:  # член пакета SCHEMA.PKG__PROC -> пакет SCHEMA.PKG
            name = name.split("__", 1)[0]
        result.setdefault(names.get(name.upper(), name), []).append(t)
    return result
