"""Граф потоков хранилища и состав переноса (ТЗ, ФП-3.5)."""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from dbtransfer.model import Adso, DataSource, Model, ObjectRef, ObjectType, OtherObject


def node(ref: ObjectRef) -> str:
    return f"{ref.type.value}:{ref.key}"


def build_graph(model: Model) -> nx.DiGraph:
    """Узлы — объекты (DataSource, ADSO, CompositeProvider, запросы), рёбра — потоки данных
    «источник → цель» с трансформацией или участием в CompositeProvider."""
    g = nx.DiGraph()
    for ds in model.datasources.values():
        g.add_node(node(ds.ref), ref=ds.ref)
    for adso in model.adsos.values():
        g.add_node(node(adso.ref), ref=adso.ref)
    for t in model.transformations.values():
        g.add_edge(node(t.source), node(t.target), kind="transformation", transformation=t.id)
        g.nodes[node(t.source)].setdefault("ref", t.source)
        g.nodes[node(t.target)].setdefault("ref", t.target)
    for o in model.others:
        ref = ObjectRef(type=o.type, name=o.name)
        if o.type in (ObjectType.COMPOSITE_PROVIDER, ObjectType.QUERY):
            g.add_node(node(ref), ref=ref)
            for r in o.related:
                g.add_edge(node(r), node(ref), kind="uses")
                g.nodes[node(r)].setdefault("ref", r)
    return g


@dataclass
class Scope:
    root: str
    objects: list[ObjectRef] = field(default_factory=list)  # в порядке потока: от источников к root
    transformations: list[str] = field(default_factory=list)
    dtps: list[str] = field(default_factory=list)
    info_objects: list[str] = field(default_factory=list)


class ScopeError(Exception):
    pass


def resolve_ref(model: Model, name: str) -> ObjectRef:
    obj = model.find(name)
    if isinstance(obj, Adso | DataSource):
        return obj.ref
    if isinstance(obj, OtherObject):
        return ObjectRef(type=obj.type, name=obj.name)
    raise ScopeError(f"Объект {name} не найден в модели. Имена ADSO и DataSource: "
                     f"{', '.join(sorted(list(model.adsos) + [d.name for d in model.datasources.values()]))}")


def scope_from(model: Model, name: str) -> Scope:
    """Поток от объекта вниз до источников: сам объект и всё, из чего в него идут данные."""
    root = resolve_ref(model, name)
    g = build_graph(model)
    start = node(root)
    if start not in g:
        raise ScopeError(f"Объект {name} не участвует ни в одном потоке")
    nodes = nx.ancestors(g, start) | {start}
    sub = g.subgraph(nodes)
    order = list(nx.lexicographical_topological_sort(sub))
    scope = Scope(root=root.key, objects=[g.nodes[n]["ref"] for n in order])
    keys = {(r.type, r.key) for r in scope.objects}
    scope.transformations = sorted(t.id for t in model.transformations.values()
                                   if (t.source.type, t.source.key) in keys and (t.target.type, t.target.key) in keys)
    scope.dtps = sorted(d.name for d in model.dtps.values()
                        if (d.source.type, d.source.key) in keys and (d.target.type, d.target.key) in keys)
    iobjs: set[str] = set()
    for ref in scope.objects:
        obj = model.object(ref)
        if isinstance(obj, Adso | DataSource):
            iobjs |= {f.info_object for f in obj.fields if f.info_object}
    for name_ in list(iobjs):
        iobj = model.info_objects.get(name_)
        if iobj:
            iobjs |= set(iobj.attributes)
            if iobj.unit_info_object:
                iobjs.add(iobj.unit_info_object)
    scope.info_objects = sorted(i for i in iobjs if i in model.info_objects)
    return scope
