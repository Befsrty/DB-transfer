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
}
# Порядок типов в сводке (ТЗ, ФП-3.1)
TYPE_ORDER = [*IOBJ_LABELS.values(), *ADSO_LABELS.values(), "DataSource", "Трансформация", "DTP",
              "CompositeProvider", "BEx-запрос", "Иерархия", "Цепочка процессов", "Классический DSO",
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
class Report:
    objects: list[Assessment]
    routines: list[RoutineInfo]
    flat_files: list[str]
    unparsed: list[Assessment]

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


def assess(model: Model) -> Report:
    norms = load_effort_norms()
    objects: list[Assessment] = []
    for iobj in sorted(model.info_objects.values(), key=lambda i: i.name):
        objects.append(Assessment(type=IOBJ_LABELS[iobj.kind], name=iobj.name, level=1,
                                  reasons=["генерируется автоматически"], description=iobj.description))
    for adso in sorted(model.adsos.values(), key=lambda a: a.name):
        objects.append(Assessment(type=ADSO_LABELS[adso.kind], name=adso.name, level=1,
                                  reasons=["генерируется автоматически"], description=adso.description))
    flat_files = []
    for ds in sorted(model.datasources.values(), key=lambda d: d.ref.key):
        if ds.is_flat_file:
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
    for o in sorted(model.others, key=lambda o: (o.type.value, o.name)):
        label, level, reason, norm = OTHER.get(o.type, (o.type.value, 3, "прочий объект", "unparsed_object"))
        objects.append(Assessment(type=label, name=o.name, level=level, reasons=[reason], hours=norms[norm],
                                  description=o.info))

    unparsed = []
    for i in model.issues:
        if i.skipped and i.severity != Severity.INFO:
            where = f"{i.file}, строка {i.line}" if i.file and i.line else (i.file or "")
            unparsed.append(Assessment(type="Не разобрано", name=i.object or where, level=3,
                                       reasons=[i.message], hours=norms["unparsed_object"], description=where))
    return Report(objects=objects, routines=routines, flat_files=flat_files, unparsed=unparsed)
