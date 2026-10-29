"""Трансформации BW -> функции PL/pgSQL с INSERT … SELECT (ТЗ, ФП-5.8, ФП-5.9, ФП-5.13)."""

from __future__ import annotations

from dataclasses import dataclass, field

from dbtransfer import naming
from dbtransfer.generator.formula_sql import FormulaError, translate
from dbtransfer.generator.sql import ident, literal, qualified
from dbtransfer.generator.tables import TableBuilder, TableDef
from dbtransfer.model import (
    Adso,
    AdsoKind,
    DataSource,
    Dtp,
    FieldDef,
    Model,
    RoutineKind,
    Rule,
    RuleKind,
    Transformation,
)

# Преобразование времени: InfoObject цели -> формат to_char и начальное значение (ТЗ, ФП-5.8, приложение Б.3)
TIME_FORMATS = {
    "0CALMONTH": ("YYYYMM", "000000"),
    "0CALYEAR": ("YYYY", "0000"),
    "0CALQUARTER": ("YYYYQ", "00000"),
    "0CALMONTH2": ("MM", "00"),
    "0CALWEEK": ("IYYYIW", "000000"),
}
ROUTINE_TITLE = {RoutineKind.START: "стартовая рутина", RoutineKind.END: "конечная рутина",
                 RoutineKind.EXPERT: "экспертная рутина", RoutineKind.FIELD: "рутина поля"}
RULE_TITLE = {RuleKind.DIRECT: "прямое присвоение", RuleKind.CONSTANT: "константа", RuleKind.FORMULA: "формула",
              RuleKind.TIME: "преобразование времени", RuleKind.READ_MD: "чтение атрибута справочника",
              RuleKind.ROUTINE: "рутина", RuleKind.UNKNOWN: "неизвестное правило"}
TEXT_LIKE = {"CHAR", "NUMC", "CUKY", "UNIT", "LANG", "STRING", "SSTRING"}


@dataclass
class RoutineText:
    title: str
    code: str


@dataclass
class TransformSql:
    tr_id: str
    source_name: str
    target_name: str
    schema: str
    fn_name: str
    mode_text: str = ""
    dtp: str | None = None
    insert: str = ""
    notes: list[str] = field(default_factory=list)
    stub: list[str] = field(default_factory=list)
    routines: list[RoutineText] = field(default_factory=list)

    @property
    def fn(self) -> str:
        return qualified(self.schema, self.fn_name)

    @property
    def message(self) -> str:
        # Сообщение RAISE: % — спецсимвол формата
        text = f"Трансформация {self.source_name} → {self.target_name} требует ручной доработки: {'; '.join(self.stub)}"
        return text.replace("%", "%%")

    @property
    def comment(self) -> str:
        text = f"BW: трансформация {self.source_name} → {self.target_name} ({self.tr_id})"
        if self.dtp:
            text += f", DTP {self.dtp}"
        if self.stub:
            text += ". ЗАГОТОВКА: требует ручной доработки: " + "; ".join(self.stub)
        return text


@dataclass
class _Source:
    table: str
    fields: list[FieldDef]
    adso: bool
    where: list[str]
    record_no: str
    recordmode: str


def convert(expr: str, src: FieldDef | None, tgt: FieldDef) -> str:
    """Приведение значения между датой BW и строковым представлением ГГГГММДД."""
    if src is None or src.datatype == tgt.datatype:
        return expr
    if src.datatype == "DATS" and tgt.datatype in TEXT_LIKE:
        return f"coalesce(to_char({expr}, 'YYYYMMDD'), '00000000')"
    if src.datatype in TEXT_LIKE and tgt.datatype == "DATS":
        return f"to_date(nullif(nullif({expr}, ''), '00000000'), 'YYYYMMDD')"
    return expr


def constant(value: str, tgt: FieldDef) -> str:
    if tgt.datatype == "DATS" and len(value) == 8 and value.isdigit():
        return "NULL" if value == "00000000" else f"DATE '{value[:4]}-{value[4:6]}-{value[6:]}'"
    if tgt.datatype in {"CURR", "QUAN", "DEC", "INT1", "INT2", "INT4", "INT8", "FLTP"}:
        try:
            float(value.replace(",", "."))
            return value.replace(",", ".")
        except ValueError:
            pass
    return literal(value)


class TransformBuilder:
    def __init__(self, model: Model, tables: TableBuilder):
        self.model = model
        self.tables = tables

    def dtp_for(self, tr: Transformation) -> Dtp | None:
        found = [d for d in self.model.dtps.values()
                 if d.source.key == tr.source.key and d.target.key == tr.target.key]
        return sorted(found, key=lambda d: d.name)[0] if found else None

    def build(self, tr: Transformation, fn_name: str) -> TransformSql:
        target = self.model.object(tr.target)
        source = self.model.object(tr.source)
        schema_name = naming.schema("stg", self.model.schema_prefix)
        if isinstance(target, Adso) and target.kind != AdsoKind.STAGING:
            schema_name = naming.table_schema(self.model, target, "active")
        result = TransformSql(tr_id=tr.id, source_name=tr.source.name, target_name=tr.target.name,
                              schema=schema_name, fn_name=fn_name)
        dtp = self.dtp_for(tr)
        mode = dtp.mode.upper() if dtp else "DELTA"
        result.dtp = dtp.name if dtp else None
        if dtp is None:
            result.notes.append("DTP не найден в выгрузке: используется дельта по номеру загрузки")

        for r in tr.routines:
            result.stub.append(ROUTINE_TITLE[r.kind] + " ABAP")
            result.routines.append(RoutineText(ROUTINE_TITLE[r.kind], r.code or "(кода нет в выгрузке RSAABAP)"))

        if not isinstance(target, Adso):
            result.stub.append(f"цель {tr.target.type} {tr.target.name} не поддерживается, только ADSO")
            result.mode_text = "Цель не поддерживается"
            return result
        if target.kind == AdsoKind.DIRECT_UPDATE:
            result.stub.append(f"ADSO {target.name} вида direct update не загружается трансформацией")
            result.mode_text = "Цель не поддерживается"
            return result
        if not isinstance(source, Adso | DataSource):
            result.stub.append(f"источник {tr.source.type} {tr.source.name} не поддерживается, "
                               "только ADSO и DataSource")
            result.mode_text = "Источник не поддерживается"
            return result

        src = self._source(source, target, mode, result)
        target_table = self.tables.adso_table(target, "inbound")
        result.insert = self._insert(tr, src, target, target_table, dtp, result)
        return result

    # --- источник ---
    def _source(self, source: Adso | DataSource, target: Adso, mode: str, result: TransformSql) -> _Source:
        full = mode == "FULL"
        result.mode_text = ("Полная загрузка: все данные источника пишутся одной новой загрузкой p_load_id"
                            if full else "Дельта: записи источника с номером загрузки p_load_id")
        if isinstance(source, DataSource):
            table = qualified(naming.schema("stg", self.model.schema_prefix), naming.table("datasource", source.name))
            return _Source(table, source.fields, False, [] if full else ["s.load_id = p_load_id"],
                           "row_number() OVER (ORDER BY s.load_id, s.record_no)" if full else "s.record_no", "' '")

        keys = [ident(naming.column(f.name, f.info_object)) for f in self.tables.adso_keys(source)]
        by_key = "row_number() OVER (ORDER BY " + ", ".join(f"s.{k}" for k in keys) + ")" if keys else \
            "row_number() OVER ()"
        if source.kind == AdsoKind.DIRECT_UPDATE and not full:
            result.stub.append(f"дельта из ADSO direct update {source.name} не поддерживается, нужна полная загрузка")
        if full and source.kind != AdsoKind.STAGING:
            table = self._adso_qname(source, "active")
            return _Source(table, source.fields, True, [], by_key, "' '")
        if source.kind == AdsoKind.STANDARD:
            table = self._adso_qname(source, "change_log")
            where = ["s.load_id = p_load_id"]
            if target.kind == AdsoKind.STANDARD:
                where.append("s.recordmode <> 'X'")
                result.notes.append("Цель с перезаписью: из журнала изменений берутся новые записи и after image, "
                                    "before image (X) пропускаются")
            else:
                result.notes.append("Источник — журнал изменений: before image (X) с минусом снимает старые суммы")
            return _Source(table, source.fields, True, where, "s.record_no", "s.recordmode")
        table = self._adso_qname(source, "inbound")
        where = [] if full else ["s.load_id = p_load_id"]
        record_no = "row_number() OVER (ORDER BY s.load_id, s.record_no)" if full else "s.record_no"
        return _Source(table, source.fields, True, where, record_no, "' '" if full else "s.recordmode")

    def _adso_qname(self, adso: Adso, role: str) -> str:
        return qualified(naming.table_schema(self.model, adso, role), naming.table(role, adso.name))

    @staticmethod
    def _find(fields: list[FieldDef], name: str) -> FieldDef | None:
        upper = name.upper()
        return next((f for f in fields if f.name.upper() == upper or (f.info_object or "").upper() == upper), None)

    def _resolver(self, src: _Source):
        def resolve(name: str) -> tuple[str, FieldDef] | None:
            f = self._find(src.fields, name)
            if f is None:
                return None
            return f"s.{ident(naming.column(f.name, f.info_object if src.adso else None))}", f
        return resolve

    # --- правила ---
    def _rule_expr(self, rule: Rule, tgt: FieldDef, src: _Source, joins: list[str],
                   result: TransformSql) -> tuple[str | None, str]:
        """Выражение для поля цели и пояснение; None — правило не переводится (причина в result.stub)."""
        resolve = self._resolver(src)
        title = RULE_TITLE.get(rule.kind, rule.kind)
        sources = ", ".join(rule.source_fields)
        if rule.kind == RuleKind.DIRECT:
            index = rule.target_fields.index(tgt.name) if tgt.name in rule.target_fields else 0
            name = rule.source_fields[min(index, len(rule.source_fields) - 1)] if rule.source_fields else ""
            resolved = resolve(name) if name else None
            if resolved is None:
                result.stub.append(f"{tgt.name}: поле источника {name or '(не указано)'} не найдено")
                return None, title
            return convert(resolved[0], resolved[1], tgt), f"{title} {name}"
        if rule.kind == RuleKind.CONSTANT:
            return constant(rule.param or "", tgt), f"{title} {literal(rule.param or '')}"
        if rule.kind == RuleKind.FORMULA:
            try:
                return translate(rule.param or "", resolve), f"{title} {rule.param}"
            except FormulaError as e:
                result.stub.append(f"{tgt.name}: формула «{rule.param}» не переводится: {e}")
                return None, title
        if rule.kind == RuleKind.TIME:
            resolved = resolve(rule.source_fields[0]) if rule.source_fields else None
            if resolved is None:
                result.stub.append(f"{tgt.name}: нет поля источника для преобразования времени")
                return None, title
            expr, f = resolved
            if f.datatype != "DATS":
                expr = convert(expr, f, FieldDef(name="", position=0, datatype="DATS", length=8))
            target_iobj = tgt.info_object or tgt.name
            if target_iobj in TIME_FORMATS:
                fmt, initial = TIME_FORMATS[target_iobj]
                return f"coalesce(to_char({expr}, '{fmt}'), '{initial}')", f"{title} {sources} → {target_iobj}"
            if tgt.datatype == "DATS":
                return expr, f"{title} {sources}"
            result.stub.append(f"{tgt.name}: преобразование времени в {target_iobj} не поддерживается")
            return None, title
        if rule.kind == RuleKind.READ_MD:
            return self._read_md(rule, tgt, resolve, joins, result)
        if rule.kind == RuleKind.ROUTINE:
            result.stub.append(f"{tgt.name}: рутина поля ABAP")
            if rule.routine:
                result.routines.append(RoutineText(f"рутина поля {tgt.name}",
                                                   rule.routine.code or "(кода нет в выгрузке RSAABAP)"))
            return None, title
        result.stub.append(f"{tgt.name}: правило {rule.raw_kind or rule.kind} не поддерживается")
        return None, title

    def _read_md(self, rule: Rule, tgt: FieldDef, resolve, joins: list[str],
                 result: TransformSql) -> tuple[str | None, str]:
        param = rule.param or ""
        characteristic, _, attribute = param.partition("-")
        iobj = self.model.info_objects.get(characteristic)
        resolved = resolve(rule.source_fields[0] if rule.source_fields else characteristic)
        if iobj is None or attribute not in iobj.attributes or resolved is None:
            result.stub.append(f"{tgt.name}: чтение атрибута {param} — нет справочника или поля в источнике")
            return None, "чтение атрибута справочника"
        alias = f"m{len(joins) + 1}"
        joins.append(f"LEFT JOIN {naming.md_attr_table(self.model, characteristic)} {alias}\n"
                     f"  ON {alias}.{ident(naming.column(characteristic))} = {resolved[0]}")
        result.notes.append(f"Атрибут {attribute} читается из справочника {characteristic} на момент прогона, "
                            "а не на момент загрузки")
        return f"{alias}.{ident(naming.column(attribute))}", f"чтение атрибута {param}"

    def _insert(self, tr: Transformation, src: _Source, target: Adso, table: TableDef, dtp: Dtp | None,
                result: TransformSql) -> str:
        by_target: dict[str, Rule] = {}
        for rule in tr.rules:
            for name in rule.target_fields:
                if name in by_target:
                    result.notes.append(f"Поле {name} задано несколькими правилами, взято первое")
                    continue
                by_target[name] = rule

        joins: list[str] = []
        items = [("load_id", "p_load_id", ""), ("record_no", src.record_no, ""),
                 ("recordmode", src.recordmode, "")]
        for col in table.columns:
            tgt = col.field
            if tgt is None:
                continue
            rule = by_target.get(tgt.name)
            if rule is None:
                if col.not_null:
                    items.append((col.sql_name, self.tables.rules.initial_value(tgt),
                                  f"{tgt.name}: правила нет, начальное значение ключа"))
                continue
            expr, note = self._rule_expr(rule, tgt, src, joins, result)
            if expr is None:
                expr = "NULL"
                note += " — требует доработки"
            elif col.not_null and not expr.startswith(("'", "coalesce(", "DATE ")):
                expr = f"coalesce({expr}, {self.tables.rules.initial_value(tgt)})"
            items.append((col.sql_name, expr, f"{tgt.name} ← {note}"))

        where = list(src.where)
        if dtp and dtp.filter:
            try:
                where.append(f"({translate(dtp.filter, self._resolver(src))})")
            except FormulaError as e:
                result.stub.append(f"фильтр DTP «{dtp.filter}» не переводится: {e}")

        lines = [f"INSERT INTO {table.qname} ({', '.join(c for c, _, _ in items)})", "SELECT"]
        for i, (_, expr, note) in enumerate(items):
            comma = "," if i < len(items) - 1 else ""
            lines.append(f"    {expr}{comma}" + (f"  -- {note}" if note else ""))
        lines.append(f"FROM {src.table} s")
        lines += joins
        if where:
            lines.append("WHERE " + "\n  AND ".join(where))
        return "\n".join(lines) + ";"
