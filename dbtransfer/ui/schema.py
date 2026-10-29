"""Схема потоков в формате DOT для st.graphviz_chart (ТЗ, ФП-4.1, ФП-4.2, ФП-4.6).

Узлы — DataSource, ADSO, CompositeProvider и запросы; рёбра — трансформации и участие в CompositeProvider.
Цвет узла — слой, цвет рамки — уровень автоматизации, трансформация с рутиной — красное ребро.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from dbtransfer import naming
from dbtransfer.analysis.assessment import Report
from dbtransfer.analysis.graph import build_graph
from dbtransfer.analysis.lineage import Node
from dbtransfer.generator.tables import KIND_TEXT
from dbtransfer.model import Adso, Model, ObjectRef, ObjectType

LAYER_LABELS = {"source": "Источник", "stg": "Вход (stg)", "dwh": "Хранение (dwh)", "mart": "Витрины (mart)",
                "report": "Отчётность"}
LAYER_FILL = {"source": "#E8EEF7", "stg": "#EAF4EA", "dwh": "#DCEBFA", "mart": "#FDF0D5", "report": "#EFE7F7"}
LEVEL_COLOR = {1: "#3B8E3B", 2: "#C98B00", 3: "#C0392B"}
TYPE_LABELS = {ObjectType.DATASOURCE: "DataSource", ObjectType.ADSO: "ADSO",
               ObjectType.COMPOSITE_PROVIDER: "CompositeProvider", ObjectType.QUERY: "BEx-запрос"}
HIGHLIGHT = "#E8590C"


@dataclass
class Highlight:
    """Что подсветить на схеме: объекты и рёбра «источник → цель» по именам объектов BW."""

    objects: set[str] = field(default_factory=set)
    edges: set[tuple[str, str]] = field(default_factory=set)


def lineage_highlight(root: Node) -> Highlight:
    """Объекты и переходы пути lineage. Ребро берётся из трансформации шага («источник → цель»)."""
    h = Highlight()
    for _, n in root.walk():
        h.objects.add(n.object)
        if n.via and " → " in n.via.label:
            src, tgt = n.via.label.split(" → ", 1)
            h.edges.add((src, tgt))
    # Шаг на уровне объекта (CompositeProvider) связан с родителем без трансформации
    for _, n in root.walk():
        for c in n.children:
            if c.via is None:
                h.edges.add((n.object, c.object))
    return h


def layer_of(model: Model, ref: ObjectRef) -> str:
    if ref.type == ObjectType.DATASOURCE:
        return "source"
    if ref.type == ObjectType.ADSO:
        adso = model.adsos.get(ref.name)
        return naming.adso_layer(model, adso) if isinstance(adso, Adso) else "dwh"
    return "report"


def levels(report: Report) -> dict[str, int]:
    """Уровень автоматизации по имени объекта или id трансформации."""
    return {a.name: a.level for a in report.objects}


def _q(text: str) -> str:
    """Строка DOT в кавычках; перевод строки — \\n."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _attrs(values: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in values.items())


def flow_dot(model: Model, report: Report, types: set[str] | None = None, layers: set[str] | None = None,
             focus: str | None = None, direction: str = "both", highlight: Highlight | None = None,
             selected: str | None = None) -> str:
    """DOT-текст схемы. types — подписи из TYPE_LABELS, layers — ключи LAYER_LABELS,
    focus — объект, от которого показывать поток вверх (up), вниз (down) или в обе стороны (both)."""
    g = build_graph(model)
    lv = levels(report)
    hl = highlight or Highlight()
    keep = set(g.nodes)
    if focus:
        start = next((n for n in g.nodes if g.nodes[n]["ref"].name == focus), None)
        if start is not None:
            part = {start}
            if direction in ("down", "both"):
                part |= nx.ancestors(g, start)
            if direction in ("up", "both"):
                part |= nx.descendants(g, start)
            keep &= part
    shown = []
    for n in sorted(keep):
        ref: ObjectRef = g.nodes[n]["ref"]
        if ref.type not in TYPE_LABELS:
            continue
        if types and TYPE_LABELS[ref.type] not in types:
            continue
        if layers and layer_of(model, ref) not in layers:
            continue
        shown.append(n)

    lines = ["digraph flow {", '  rankdir=LR; bgcolor="transparent";',
             '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, penwidth=2];',
             '  edge [fontname="Helvetica", fontsize=9, color="#7A8699"];']
    for n in shown:
        ref = g.nodes[n]["ref"]
        layer = layer_of(model, ref)
        level = lv.get(ref.key, lv.get(ref.name, 1))
        obj = model.object(ref)
        kind = f"ADSO {KIND_TEXT[obj.kind]}" if isinstance(obj, Adso) else TYPE_LABELS[ref.type]
        attrs = {"label": _q(f"{ref.name}\n{kind}"), "fillcolor": _q(LAYER_FILL[layer]),
                 "color": _q(LEVEL_COLOR.get(level, "#555555")),
                 "tooltip": _q(f"{LAYER_LABELS[layer]}, уровень автоматизации {level}")}
        if ref.name in hl.objects:
            attrs.update(color=_q(HIGHLIGHT), penwidth="4")
        if ref.name == selected:
            attrs["peripheries"] = "2"
        lines.append(f"  {_q(n)} [{_attrs(attrs)}];")
    visible = set(shown)
    for a, b, data in sorted(g.edges(data=True)):
        if a not in visible or b not in visible:
            continue
        src, tgt = g.nodes[a]["ref"].name, g.nodes[b]["ref"].name
        attrs = {}
        if data.get("kind") == "transformation":
            t = model.transformations[data["transformation"]]
            parts = ["рутина"] if t.all_routines else []
            dtp = next((d for d in model.dtps.values() if d.source.key == t.source.key
                        and d.target.key == t.target.key), None)
            if dtp:
                parts.append("дельта" if dtp.mode == "DELTA" else "полная")
            attrs["label"] = _q("\n".join(parts))
            level = 3 if t.all_routines else lv.get(t.id, 1)
            if level > 1:
                attrs.update(color=_q(LEVEL_COLOR[level]), penwidth="2", fontcolor=_q(LEVEL_COLOR[level]))
        else:
            attrs["style"] = _q("dashed")
        if (src, tgt) in hl.edges:
            attrs.update(color=_q(HIGHLIGHT), penwidth="3")
        lines.append(f"  {_q(a)} -> {_q(b)} [{_attrs(attrs)}];")
    lines.append("}")
    return "\n".join(lines)
