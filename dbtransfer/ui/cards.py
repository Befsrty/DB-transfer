"""Данные карточек объекта и трансформации и шагов lineage (ТЗ, ФП-4.3, ФП-4.4, ФП-4.6) — без Streamlit."""

from __future__ import annotations

import io

import pandas as pd

from dbtransfer import naming
from dbtransfer.analysis.assessment import Report, transformation_label
from dbtransfer.analysis.lineage import DOWN, RULE_LABELS, Node
from dbtransfer.generator.tables import ROLE_TEXT, TableBuilder, type_text
from dbtransfer.generator.types import TypeRules, is_key_figure
from dbtransfer.model import Adso, DataSource, Model, Transformation

STORAGE_TEXT = {"heap": "heap", "ao_column": "AO колоночная, zstd"}


def assessment_of(report: Report, name: str) -> tuple[int, str, float] | None:
    a = next((a for a in report.objects if a.name == name), None)
    return (a.level, a.reason, a.hours) if a else None


def object_fields(model: Model, rules: TypeRules, obj: Adso | DataSource) -> pd.DataFrame:
    """Поля объекта: InfoObject, тип BW и ADB, ключ, показатель, колонка в базе."""
    builder = TableBuilder(model, rules)
    adso = isinstance(obj, Adso)
    keys = {f.name for f in builder.adso_keys(obj)} if adso else set()
    rows = []
    for f in sorted(obj.fields, key=lambda x: x.position):
        iobj = model.info_objects.get(f.info_object or f.name)
        rows.append({
            "Поле": f.name,
            "Описание": f.comment or (iobj.description if iobj else ""),
            "Тип BW": type_text(f),
            "Тип ADB": rules.column_type(f, obj.name),
            "Ключ": "да" if f.name in keys else "",
            "Показатель": "да" if adso and is_key_figure(model, f) else "",
            "Колонка ADB": naming.column(f.name, f.info_object if adso else None),
        })
    return pd.DataFrame(rows)


def object_tables(model: Model, rules: TypeRules, obj: Adso | DataSource) -> pd.DataFrame:
    """Таблицы объекта в ADB: роль, таблица BW, таблица ADB, хранение, ключ распределения, PK."""
    builder = TableBuilder(model, rules)
    if isinstance(obj, DataSource):
        tables = [("datasource", None, builder.datasource_table(obj))]
    else:
        tables = [(role, obj.bw_tables.get(role), builder.adso_table(obj, role)) for role in builder.adso_roles(obj)]
    rows = []
    for role, bw, t in tables:
        rows.append({
            "Роль": ROLE_TEXT.get(role, "входная таблица"),
            "Таблица BW": bw or "",
            "Таблица ADB": t.qname,
            "Хранение": STORAGE_TEXT.get(t.storage, t.storage),
            "Распределение": ", ".join(t.distributed_by) or "случайное",
            "Первичный ключ": ", ".join(t.primary_key),
        })
    return pd.DataFrame(rows)


def transformation_rules(model: Model, t: Transformation) -> pd.DataFrame:
    """Таблица «поле источника → правило → поле цели» (ФП-4.4)."""
    rows = []
    for r in sorted(t.rules, key=lambda r: r.rule_id):
        param = r.param or ""
        if r.routine:
            param = f"рутина {r.routine.code_id}" + (f", {r.routine.lines} строк" if r.routine.code else "")
        rows.append({
            "Поле источника": ", ".join(r.source_fields) or "—",
            "Правило": RULE_LABELS[r.kind] if r.kind in RULE_LABELS else r.raw_kind,
            "Параметр": param,
            "Поле цели": ", ".join(r.target_fields),
        })
    return pd.DataFrame(rows)


def transformation_title(model: Model, t: Transformation) -> str:
    return f"Трансформация {transformation_label(model, t)}"


def lineage_steps(root: Node, direction: str = DOWN) -> pd.DataFrame:
    """Шаги lineage одной таблицей: глубина, поле, как получено, где лежит в ADB, примечание."""
    rows = []
    for depth, n in root.walk():
        how = ""
        if n.via:
            how = n.via.describe()
            if n.via.maybe_changed:
                how += " — возможно изменено рутиной"
        rows.append({
            "Шаг": depth,
            "Поле": ("  " * depth) + n.title,
            "Как получено" if direction == DOWN else "Как передаётся": how,
            "Таблица и колонка ADB": n.location or "",
            "Примечание": n.note or "",
        })
    return pd.DataFrame(rows)


def lineage_excel(root: Node, direction: str = DOWN) -> bytes:
    """Lineage поля в Excel, лист «Шаги»: поле, направление, глубина, объект, правило (ФП-4.7)."""
    rows = [{
        "Поле": n.title,
        "Направление": "откуда пришло" if direction == DOWN else "куда идёт",
        "Глубина": depth,
        "Объект": n.object,
        "Правило": n.via.describe() if n.via else "",
        "Таблица и колонка ADB": n.location or "",
        "Примечание": n.note or "",
    } for depth, n in root.walk()]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Шаги", index=False)
        sheet = writer.sheets["Шаги"]
        for col, width in zip("ABCDEFG", (34, 16, 9, 14, 60, 44, 30), strict=True):
            sheet.column_dimensions[col].width = width
    return buf.getvalue()
