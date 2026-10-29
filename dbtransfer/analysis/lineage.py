"""Lineage поля: откуда пришло поле и куда оно идёт через трансформации (ТЗ, ФП-4.6).

Строится только по модели, без базы. Правила обхода:
- прямое присвоение, время и формула ведут к полям источника; формула от нескольких полей ветвит путь;
- чтение атрибута ветвит путь в поле-ключ источника и в атрибут справочника (md.<признак>_attr);
- константа завершает путь;
- рутина поля и неразобранное правило обрывают путь: «дальше не известно»;
- шаг через трансформацию со стартовой, конечной или экспертной рутиной помечается
  «возможно изменено рутиной», даже при прямом присвоении;
- вверх путь доходит до полей ADSO верхнего слоя, а CompositeProvider и запросы над ними
  показываются последним шагом на уровне объекта.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dbtransfer import naming
from dbtransfer.analysis.graph import resolve_ref
from dbtransfer.model import Adso, DataSource, Model, ObjectRef, ObjectType, Rule, RuleKind, Transformation

RULE_LABELS = {
    RuleKind.DIRECT: "прямое присвоение",
    RuleKind.CONSTANT: "константа",
    RuleKind.FORMULA: "формула",
    RuleKind.TIME: "преобразование времени",
    RuleKind.READ_MD: "чтение атрибута",
    RuleKind.ROUTINE: "рутина",
    RuleKind.UNKNOWN: "неразобранное правило",
}

UP, DOWN = "up", "down"


class LineageError(Exception):
    pass


@dataclass
class Via:
    """Как получен шаг: трансформация и правило."""

    transformation: str
    label: str  # «источник → цель»
    rule: RuleKind
    param: str | None = None
    maybe_changed: bool = False  # у трансформации есть стартовая, конечная или экспертная рутина

    def describe(self) -> str:
        text = RULE_LABELS[self.rule]
        if self.rule == RuleKind.CONSTANT:
            text += f" '{self.param}'"
        elif self.param and self.rule in (RuleKind.FORMULA, RuleKind.READ_MD):
            text += f" {self.param}"
        return f"{text} ({self.label})"


@dataclass
class Node:
    object: str
    field: str | None  # None — шаг на уровне объекта (CompositeProvider, запрос)
    location: str | None = None  # схема.таблица.колонка в Greengage
    via: Via | None = None
    note: str | None = None  # конец пути: константа, рутина, уровень объекта
    children: list[Node] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.object}.{self.field}" if self.field else self.object

    def walk(self, depth: int = 0):
        yield depth, self
        for c in self.children:
            yield from c.walk(depth + 1)


def _location(model: Model, ref: ObjectRef, field_name: str) -> str | None:
    obj = model.object(ref)
    if not isinstance(obj, Adso | DataSource):
        return None
    f = obj.field(field_name)
    column = naming.db_name(f.info_object or f.name) if f and isinstance(obj, Adso) else naming.db_name(field_name)
    return f"{naming.data_table(model, obj)}.{column}"


def _via(t: Transformation, rule: Rule) -> Via:
    return Via(transformation=t.id, label=f"{t.source.name} → {t.target.name}", rule=rule.kind, param=rule.param,
               maybe_changed=bool(t.routines))


def _node(model: Model, ref: ObjectRef, field_name: str, via: Via | None = None) -> Node:
    return Node(object=ref.name, field=field_name, location=_location(model, ref, field_name), via=via)


def _same(a: ObjectRef, b: ObjectRef) -> bool:
    return a.type == b.type and a.key == b.key


def _down(model: Model, node: Node, ref: ObjectRef, seen: frozenset) -> None:
    for t in sorted(model.transformations.values(), key=lambda t: t.id):
        if not _same(t.target, ref):
            continue
        for rule in t.rules:
            if node.field not in rule.target_fields:
                continue
            via = _via(t, rule)
            if rule.kind == RuleKind.CONSTANT:
                node.children.append(Node(object=t.source.name, field=None, via=via, note="путь завершён: константа"))
            elif rule.kind in (RuleKind.ROUTINE, RuleKind.UNKNOWN):
                node.children.append(Node(object=t.source.name, field=", ".join(rule.source_fields) or None,
                                          via=via, note=f"дальше не известно: {RULE_LABELS[rule.kind]}"))
            else:
                for src in rule.source_fields:
                    child = _node(model, t.source, src, via)
                    node.children.append(child)
                    if (t.source.key, src) not in seen:
                        _down(model, child, t.source, seen | {(t.source.key, src)})
                if rule.kind == RuleKind.READ_MD and rule.param and "-" in rule.param:
                    characteristic, attribute = rule.param.split("-", 1)
                    node.children.append(Node(
                        object=characteristic, field=attribute, via=via, note="атрибут справочника",
                        location=f"{naming.md_attr_table(model, characteristic)}.{naming.db_name(attribute)}"))


def _consumers(model: Model, ref: ObjectRef) -> list[Node]:
    """CompositeProvider и запросы над объектом — на уровне объекта."""
    result = []
    for o in sorted(model.others, key=lambda o: o.name):
        if o.type == ObjectType.COMPOSITE_PROVIDER and any(_same(r, ref) for r in o.related):
            cp = Node(object=o.name, field=None, note="CompositeProvider, на уровне объекта")
            cp_ref = ObjectRef(type=ObjectType.COMPOSITE_PROVIDER, name=o.name)
            cp.children = [Node(object=q.name, field=None, note="BEx-запрос, на уровне объекта")
                           for q in model.others if q.type == ObjectType.QUERY
                           and any(_same(r, cp_ref) for r in q.related)]
            result.append(cp)
    return result


def _up(model: Model, node: Node, ref: ObjectRef, seen: frozenset) -> None:
    for t in sorted(model.transformations.values(), key=lambda t: t.id):
        if not _same(t.source, ref):
            continue
        for rule in t.rules:
            if node.field not in rule.source_fields:
                continue
            via = _via(t, rule)
            if rule.kind in (RuleKind.ROUTINE, RuleKind.UNKNOWN):
                node.children.append(Node(object=t.target.name, field=", ".join(rule.target_fields) or None,
                                          via=via, note=f"дальше не известно: {RULE_LABELS[rule.kind]}"))
                continue
            for tgt in rule.target_fields:
                child = _node(model, t.target, tgt, via)
                node.children.append(child)
                if (t.target.key, tgt) not in seen:
                    _up(model, child, t.target, seen | {(t.target.key, tgt)})
    if ref.type == ObjectType.ADSO:
        node.children.extend(_consumers(model, ref))


def lineage(model: Model, obj: str, field_name: str, direction: str = DOWN) -> Node:
    ref = resolve_ref(model, obj)
    target = model.object(ref)
    if not isinstance(target, Adso | DataSource):
        raise LineageError(f"{obj} — не ADSO и не DataSource: lineage строится по полям ADSO и DataSource")
    if target.field(field_name) is None:
        names = ", ".join(f.name for f in target.fields)
        raise LineageError(f"У {obj} нет поля {field_name}. Поля: {names}")
    root = _node(model, ref, field_name)
    seen = frozenset({(ref.key, field_name)})
    if direction == DOWN:
        _down(model, root, ref, seen)
        if not root.children:
            root.note = "источник: входящих трансформаций нет" if isinstance(target, DataSource) or not any(
                _same(t.target, ref) for t in model.transformations.values()) else "нет правила для поля"
    else:
        _up(model, root, ref, seen)
        if not root.children:
            root.note = "дальше поле никуда не идёт"
    return root


def render(root: Node, direction: str = DOWN) -> str:
    """Дерево lineage для консоли."""
    arrow = "←" if direction == DOWN else "→"
    lines: list[str] = []

    def line(node: Node, prefix: str, connector: str) -> None:
        parts = []
        if node.via:
            parts.append(f"{arrow} {node.via.describe()}")
            if node.via.maybe_changed:
                parts.append("[возможно изменено рутиной]")
        parts.append(node.title)
        if node.location:
            parts.append(f"[{node.location}]")
        if node.note:
            parts.append(f"— {node.note}")
        lines.append(prefix + connector + " ".join(parts))

    def walk(node: Node, prefix: str) -> None:
        for i, child in enumerate(node.children):
            last = i == len(node.children) - 1
            line(child, prefix, "└─ " if last else "├─ ")
            walk(child, prefix + ("   " if last else "│  "))

    line(root, "", "")
    walk(root, "")
    return "\n".join(lines)
