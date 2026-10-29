"""Импорт метаданных SAP BW из папки CSV-выгрузок SE16 в модель (ТЗ, ФП-2).

Состав таблиц и колонок берётся из dbtransfer/rules/bw_tables.yaml. Учитывается только
активная версия объектов (OBJVERS = A, для DD03L AS4LOCAL = A); объекты без активной
версии перечисляются в Model.inactive. Ошибка в одном файле не прерывает импорт:
всё, что не удалось разобрать, попадает в Model.issues с файлом и строкой.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dbtransfer.config import load_bw_tables
from dbtransfer.importers.csv_reader import Row, TableFile, read_table
from dbtransfer.model import (
    Adso,
    AdsoKind,
    DataSource,
    Dtp,
    FieldDef,
    InactiveObject,
    InfoObject,
    InfoObjectKind,
    Model,
    ObjectRef,
    ObjectType,
    OtherObject,
    Routine,
    RoutineKind,
    Rule,
    RuleKind,
    Severity,
    Transformation,
)

# Инструкция по выгрузке для командной строки и интерфейса (ТЗ, ФП-2.1)
SE16_HELP = """\
Как выгрузить таблицу через SE16:
  1. Транзакция SE16 (или SE16N), введите имя таблицы.
  2. Включите технические имена полей: Настройки → Параметры пользователя → «Имя поля».
  3. В «Макс. число совпадений» оставьте пусто, чтобы выгрузить все строки. Для DD03L
     ограничьте TABNAME таблицами ADSO и справочников (/BIC/A*, /BI0/P*, /BI0/T*, /BIC/P*, /BIC/T*).
  4. Выполните (F8), затем Система → Список → Сохранить → Локальный файл,
     формат «Электронная таблица» или «Текст с табуляцией».
  5. Сохраните файл под именем таблицы: RSDIOBJ.csv или RSDIOBJ.txt, все файлы в одну папку.
Кодировка (UTF-8 или Windows-1251) и разделитель (; , или табуляция) определяются автоматически.
Подробнее: docs/bw-export.md"""


class ImportFailed(Exception):
    """Папку нельзя импортировать: её нет или в ней нет ни одной таблицы."""


# Таблицы-заголовки объектов: по ним ищутся объекты без активной версии
HEADER_TABLES = {
    "RSDIOBJ": ("InfoObject", ["IOBJNM"]),
    "RSOADSO": ("ADSO", ["ADSONM"]),
    "RSDS": ("DataSource", ["DATASOURCE", "LOGSYS"]),
    "RSTRAN": ("Трансформация", ["TRANID"]),
    "RSBKDTP": ("DTP", ["DTP"]),
    "RSOHCPR": ("CompositeProvider", ["HCPRNM"]),
    "RSDODSO": ("Классический DSO", ["ODSOBJECT"]),
    "RSZCOMPDIR": ("Запрос", ["COMPID"]),
    "RSHIEDIR": ("Иерархия", ["HIEID"]),
    "RSPCCHAIN": ("Цепочка процессов", ["CHAIN_ID"]),
}

ADSO_TABLE_ROLES = {"1": "inbound", "2": "active", "3": "change_log"}
TECHNICAL_COLUMNS = {"REQTSN", "DATAPAKID", "RECORD", "RECORDMODE"}


class _Context:
    """Прочитанные таблицы и модель, которую наполняем."""

    def __init__(self, files: dict[str, TableFile], model: Model):
        self.files = files
        self.model = model

    def rows(self, table: str) -> list[Row]:
        """Активные строки таблицы."""
        f = self.files[table]
        if not f.usable:
            return []
        column = load_bw_tables()[table].version_column
        return [r for r in f.rows if r.get(column, "A") == "A"]

    def issue(self, severity: Severity, message: str, table: str | None = None, row: Row | None = None,
              obj: str | None = None, skipped: bool = False) -> None:
        file = self.files[table].file_name if table else None
        self.model.issue(severity, message, file=file, line=row.line if row else None, obj=obj, skipped=skipped)


def _int(value: str) -> int:
    value = value.strip()
    try:
        return int(value) if value else 0
    except ValueError:
        return 0


def _flag(value: str) -> bool:
    return value.strip().upper() == "X"


def iobj_from_rollname(rollname: str) -> str | None:
    """/BIC/OIZCUST -> ZCUST, /BI0/OICALDAY -> 0CALDAY, прочее -> None."""
    if rollname.startswith("/BIC/OI"):
        return rollname[len("/BIC/OI"):]
    if rollname.startswith("/BI0/OI"):
        return "0" + rollname[len("/BI0/OI"):]
    return None


def parse_ref(obj_type: str, name: str) -> ObjectRef:
    """Источник или цель из RSTRAN/RSBKDTP. У DataSource это имя до 30 символов, затем исходная система."""
    obj_type = obj_type.strip()
    if obj_type == ObjectType.DATASOURCE.value:
        if len(name) > 30:
            ds, logsys = name[:30].strip(), name[30:].strip()
        else:
            parts = name.split()
            ds, logsys = (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")
        return ObjectRef(type=ObjectType.DATASOURCE, name=ds, source_system=logsys or None)
    return ObjectRef(type=ObjectType(obj_type), name=name.strip())


def import_bw_export(folder: Path | str, language: str = "R", encoding: str | None = None,
                     delimiter: str | None = None) -> Model:
    """Построить модель по папке с CSV-выгрузками BW.

    language: ключ языка SAP для текстов (R русский, E английский).
    encoding, delimiter: задать вручную вместо автоопределения.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise ImportFailed(f"Папка не найдена: {folder}")
    specs = load_bw_tables()
    files = {name: read_table(folder, spec, encoding, delimiter) for name, spec in specs.items()}
    if not any(f.path for f in files.values()):
        raise ImportFailed(f"В папке {folder} нет ни одной выгрузки BW (ожидаются файлы вида RSDIOBJ.csv)")

    model = Model(source=str(folder), imported_at=datetime.now().replace(microsecond=0), language=language)
    for f in files.values():
        model.issues.extend(f.issues)
        model.files[f.table] = {
            "file": f.path.name if f.path else "",
            "encoding": f.encoding,
            "delimiter": {";": ";", "\t": "табуляция", ",": ","}.get(f.delimiter, f.delimiter),
            "rows": len(f.rows),
            "status": "прочитан" if f.usable else ("нет файла" if f.path is None else "ошибка"),
        }

    ctx = _Context(files, model)
    _collect_inactive(ctx)
    dd03l = _dd03l(ctx)
    _info_objects(ctx, language)
    _adsos(ctx, dd03l, language)
    _datasources(ctx, language)
    _transformations(ctx)
    _dtps(ctx)
    _others(ctx)
    return model


def _collect_inactive(ctx: _Context) -> None:
    for table, (label, key_columns) in HEADER_TABLES.items():
        f = ctx.files[table]
        if not f.usable:
            continue
        versions: dict[tuple[str, ...], set[str]] = defaultdict(set)
        first: dict[tuple[str, ...], Row] = {}
        for r in f.rows:
            key = tuple(r[c] for c in key_columns)
            versions[key].add(r["OBJVERS"])
            first.setdefault(key, r)
        for key, vers in versions.items():
            name = "/".join(k for k in key if k)
            if "A" not in vers:
                ctx.model.inactive.append(InactiveObject(type=label, name=name, versions=sorted(vers),
                                                         file=f.file_name))
                ctx.issue(Severity.INFO, f"{label} {name} пропущен: нет активной версии "
                                         f"(есть {', '.join(sorted(vers))})", table, first[key], obj=name)


def _texts(ctx: _Context, table: str, key_columns: list[str], language: str) -> dict[tuple[str, ...], Row]:
    """Тексты на нужном языке, иначе на английском, иначе на любом."""
    by_key: dict[tuple[str, ...], dict[str, Row]] = defaultdict(dict)
    for r in ctx.rows(table):
        by_key[tuple(r[c] for c in key_columns)][r["LANGU"]] = r
    return {k: langs.get(language) or langs.get("E") or next(iter(langs.values())) for k, langs in by_key.items()}


def _dd03l(ctx: _Context) -> dict[str, list[Row]]:
    tables: dict[str, list[Row]] = defaultdict(list)
    for r in ctx.rows("DD03L"):
        tables[r["TABNAME"]].append(r)
    for rows in tables.values():
        rows.sort(key=lambda r: _int(r["POSITION"]))
    return tables


def _info_objects(ctx: _Context, language: str) -> None:
    texts = _texts(ctx, "RSDIOBJT", ["IOBJNM"], language)
    chabas = {r["CHABASNM"]: r for r in ctx.rows("RSDCHABAS")}
    kyf = {r["KYFNM"]: r for r in ctx.rows("RSDKYF")}
    attrs: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in ctx.rows("RSDBCHATR"):
        attrs[r["CHABASNM"]].append((_int(r["POSIT"]), r["ATTRINM"]))

    for r in ctx.rows("RSDIOBJ"):
        name = r["IOBJNM"]
        try:
            kind = InfoObjectKind(r["IOBJTP"])
        except ValueError:
            ctx.issue(Severity.WARNING, f"Неизвестный вид InfoObject «{r['IOBJTP']}», объект пропущен. "
                                        "Проверьте поле IOBJTP: ожидается CHA, KYF, TIM или UNI",
                      "RSDIOBJ", r, name, skipped=True)
            continue
        text = texts.get((name,))
        iobj = InfoObject(name=name, kind=kind, short_text=text["TXTSH"] if text else "",
                          description=text["TXTLG"] if text else "")
        if kind == InfoObjectKind.KEY_FIGURE:
            k = kyf.get(name)
            if k is None:
                ctx.issue(Severity.WARNING, f"Показатель {name} не описан в RSDKYF: тип и агрегация неизвестны",
                          "RSDIOBJ", r, name)
            else:
                iobj.datatype = k["DATATP"]
                iobj.key_figure_type = k["KYFTP"] or None
                iobj.aggregation = k["AGGREGATE"] or None
                iobj.unit_info_object = k.get("UNINM") or None
                iobj.fixed_currency = k.get("FIXCUKY") or None
                iobj.fixed_unit = k.get("FIXUNIT") or None
        else:
            c = chabas.get(name)
            if c is None:
                ctx.issue(Severity.WARNING, f"Признак {name} не описан в RSDCHABAS: тип и длина неизвестны",
                          "RSDIOBJ", r, name)
            else:
                iobj.datatype = c["DATATP"]
                iobj.length = _int(c["INTLEN"])
                iobj.conversion_exit = c.get("CONVEXIT")
                iobj.has_master_data = _flag(c["ATTRIBFL"])
                iobj.has_texts = _flag(c["TXTTABFL"])
                iobj.has_hierarchies = _flag(c.get("HIETABFL"))
            iobj.attributes = [a for _, a in sorted(attrs.get(name, []))]
        ctx.model.info_objects[name] = iobj

    # Длина показателя есть только в DD03L
    for rows in _dd03l(ctx).values():
        for r in rows:
            iobj = ctx.model.info_objects.get(iobj_from_rollname(r["ROLLNAME"]) or "")
            if iobj and iobj.kind == InfoObjectKind.KEY_FIGURE and not iobj.length:
                iobj.length, iobj.decimals = _int(r["LENG"]), _int(r["DECIMALS"])

    for iobj in ctx.model.info_objects.values():
        for attr in iobj.attributes:
            if attr not in ctx.model.info_objects:
                ctx.issue(Severity.WARNING, f"Атрибут {attr} признака {iobj.name} не найден среди InfoObject",
                          obj=iobj.name)
        if iobj.unit_info_object and iobj.unit_info_object not in ctx.model.info_objects:
            ctx.issue(Severity.WARNING, f"InfoObject единицы или валюты {iobj.unit_info_object} "
                                        f"показателя {iobj.name} не найден", obj=iobj.name)


def adso_kind(r: Row) -> AdsoKind:
    if _flag(r["DIRECTUPDATE"]):
        return AdsoKind.DIRECT_UPDATE
    if _flag(r["ACTIVATE"]):
        return AdsoKind.CUMULATIVE if _flag(r["ALLCHARKEY"]) else AdsoKind.STANDARD
    return AdsoKind.STAGING


def _adsos(ctx: _Context, dd03l: dict[str, list[Row]], language: str) -> None:
    texts = _texts(ctx, "RSOADSOT", ["ADSONM"], language)
    for r in ctx.rows("RSOADSO"):
        name = r["ADSONM"]
        text = texts.get((name,))
        adso = Adso(name=name, kind=adso_kind(r), description=text["TXTLG"] if text else "",
                    info_area=r.get("INFOAREA"))
        for digit, role in ADSO_TABLE_ROLES.items():
            if f"/BIC/A{name}{digit}" in dd03l:
                adso.bw_tables[role] = f"/BIC/A{name}{digit}"
        main = adso.bw_tables.get("active") or adso.bw_tables.get("inbound")
        if main is None:
            ctx.issue(Severity.ERROR, f"В DD03L нет таблиц ADSO {name} (/BIC/A{name}1, /BIC/A{name}2): "
                                      "поля неизвестны. Выгрузите DD03L по этим таблицам", "RSOADSO", r, name)
        else:
            for col in dd03l[main]:
                if col["FIELDNAME"] in TECHNICAL_COLUMNS:
                    continue
                iobj_name = iobj_from_rollname(col["ROLLNAME"])
                iobj = ctx.model.info_objects.get(iobj_name or "")
                if iobj_name is None:
                    ctx.issue(Severity.WARNING, f"Поле {col['FIELDNAME']} ADSO {name} не связано с InfoObject",
                              "DD03L", col, name)
                elif iobj is None:
                    ctx.issue(Severity.WARNING, f"Поле {col['FIELDNAME']} ADSO {name}: InfoObject {iobj_name} "
                                                "не найден в RSDIOBJ", "DD03L", col, name)
                is_key = _flag(col["KEYFLAG"]) and main == adso.bw_tables.get("active")
                adso.fields.append(FieldDef(
                    name=iobj_name or col["FIELDNAME"], position=len(adso.fields) + 1, info_object=iobj_name,
                    bw_column=col["FIELDNAME"], datatype=col["DATATYPE"], length=_int(col["LENG"]),
                    decimals=_int(col["DECIMALS"]), is_key=is_key, nullable=not is_key,
                    comment=iobj.description if iobj else "",
                ))
        expected = {AdsoKind.STAGING: {"inbound"}, AdsoKind.STANDARD: {"inbound", "active", "change_log"},
                    AdsoKind.CUMULATIVE: {"inbound", "active"}, AdsoKind.DIRECT_UPDATE: {"active"}}[adso.kind]
        lacking = expected - set(adso.bw_tables)
        if main and lacking:
            ctx.issue(Severity.WARNING, f"ADSO {name} ({adso.kind.value}): в DD03L нет таблиц "
                                        f"{', '.join(sorted(lacking))}", "RSOADSO", r, name)
        ctx.model.adsos[name] = adso


def _datasources(ctx: _Context, language: str) -> None:
    texts = _texts(ctx, "RSDST", ["DATASOURCE", "LOGSYS"], language)
    fields: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in ctx.rows("RSDSSEGFD"):
        fields[(r["DATASOURCE"], r["LOGSYS"])].append(r)
    for r in ctx.rows("RSDS"):
        key = (r["DATASOURCE"], r["LOGSYS"])
        text = texts.get(key)
        ds = DataSource(name=key[0], source_system=key[1], source_type=r["SRCTYPE"],
                        description=text["TXTLG"] if text else "", delta_process=r.get("DELTAPROCESS"))
        for f in sorted(fields.get(key, []), key=lambda f: _int(f["POSIT"])):
            ds.fields.append(FieldDef(
                name=f["FIELDNM"], position=_int(f["POSIT"]), info_object=f.get("IOBJNM") or None,
                datatype=f["DATATYPE"], length=_int(f["LENG"]), decimals=_int(f["DECIMALS"]),
                is_key=_flag(f.get("KEYFLAG")),
            ))
        if not ds.fields:
            ctx.issue(Severity.WARNING, f"Нет полей DataSource {ds.ref.key} в RSDSSEGFD", "RSDS", r, ds.ref.key)
        ctx.model.datasources[ds.ref.key] = ds


def _transformations(ctx: _Context) -> None:
    code: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in ctx.rows("RSAABAP"):
        code[r["CODEID"]].append((_int(r["LINE_NO"]), r.get("LINE")))
    sources = {cid: "\n".join(line for _, line in sorted(lines)) for cid, lines in code.items()}

    def routine(kind: RoutineKind, code_id: str, tran_id: str) -> Routine:
        if code_id not in sources:
            ctx.issue(Severity.WARNING, f"Код рутины {code_id} не найден в RSAABAP", obj=tran_id)
        return Routine(kind=kind, code_id=code_id, code=sources.get(code_id))

    params: dict[tuple[str, str], dict[str, list[tuple[str, str]]]] = defaultdict(lambda: {"0": [], "1": []})
    for r in ctx.rows("RSTRANFIELD"):
        params[(r["TRANID"], r["RULEID"])].setdefault(r["PARAMTYPE"], []).append((r.get("PARAMNM"), r["FIELDNM"]))

    rules: dict[str, list[Rule]] = defaultdict(list)
    for r in ctx.rows("RSTRANRULE"):
        tran_id = r["TRANID"]
        try:
            kind = RuleKind(r["RULETYPE"])
        except ValueError:
            kind = RuleKind.UNKNOWN
            ctx.issue(Severity.WARNING, f"Правило {r['RULEID']}: неизвестный вид «{r['RULETYPE']}», "
                                        "правило не разобрано", "RSTRANRULE", r, tran_id)
        p = params.get((tran_id, r["RULEID"]), {"0": [], "1": []})
        rule = Rule(
            rule_id=_int(r["RULEID"]), kind=kind, raw_kind=r["RULETYPE"],
            source_fields=[f for _, f in sorted(p.get("0", []))], target_fields=[f for _, f in sorted(p.get("1", []))],
            param=r.get("PARAM") or None,
        )
        if kind == RuleKind.ROUTINE:
            if r.get("CODEID"):
                rule.routine = routine(RoutineKind.FIELD, r["CODEID"], tran_id)
            else:
                ctx.issue(Severity.WARNING, f"Правило {r['RULEID']} — рутина без CODEID", "RSTRANRULE", r, tran_id)
        if not rule.target_fields:
            ctx.issue(Severity.WARNING, f"Правило {r['RULEID']}: в RSTRANFIELD нет поля цели",
                      "RSTRANRULE", r, tran_id)
        rules[tran_id].append(rule)

    for r in ctx.rows("RSTRAN"):
        tran_id = r["TRANID"]
        try:
            source = parse_ref(r["SOURCETYPE"], r["SOURCENAME"])
            target = parse_ref(r["TARGETTYPE"], r["TARGETNAME"])
        except ValueError:
            ctx.issue(Severity.WARNING, f"Трансформация {tran_id}: тип источника «{r['SOURCETYPE']}» или цели "
                                        f"«{r['TARGETTYPE']}» не поддерживается, трансформация не разобрана. "
                                        "Проверьте SOURCETYPE и TARGETTYPE", "RSTRAN", r, tran_id, skipped=True)
            continue
        if not source.name or not target.name:
            ctx.issue(Severity.WARNING, f"Трансформация {tran_id}: пустое имя источника или цели, "
                                        "трансформация не разобрана", "RSTRAN", r, tran_id, skipped=True)
            continue
        tran = Transformation(id=tran_id, source=source, target=target,
                              rules=sorted(rules.pop(tran_id, []), key=lambda x: x.rule_id))
        for column, kind in (("STARTROUTINE", RoutineKind.START), ("ENDROUTINE", RoutineKind.END),
                             ("EXPERT", RoutineKind.EXPERT)):
            if r.get(column):
                tran.routines.append(routine(kind, r[column], tran_id))
        _check_ref(ctx, tran_id, source, "RSTRAN", r)
        _check_ref(ctx, tran_id, target, "RSTRAN", r)
        _check_rule_fields(ctx, tran)
        ctx.model.transformations[tran_id] = tran
    for tran_id in rules:
        ctx.issue(Severity.WARNING, f"Правила трансформации {tran_id} есть в RSTRANRULE, "
                                    "а самой трансформации нет в RSTRAN", obj=tran_id)


def _fields_of(model: Model, ref: ObjectRef) -> set[str] | None:
    obj = model.object(ref)
    if isinstance(obj, Adso | DataSource):
        return {f.name for f in obj.fields}
    return None


def _check_rule_fields(ctx: _Context, tran: Transformation) -> None:
    src, tgt = _fields_of(ctx.model, tran.source), _fields_of(ctx.model, tran.target)
    for rule in tran.rules:
        for f in rule.source_fields:
            if src is not None and f not in src:
                ctx.issue(Severity.WARNING, f"Правило {rule.rule_id}: поля {f} нет в источнике {tran.source}",
                          obj=tran.id)
        for f in rule.target_fields:
            if tgt is not None and f not in tgt:
                ctx.issue(Severity.WARNING, f"Правило {rule.rule_id}: поля {f} нет в цели {tran.target}",
                          obj=tran.id)


def _check_ref(ctx: _Context, owner: str, ref: ObjectRef, table: str, row: Row) -> None:
    if ref.type in (ObjectType.ADSO, ObjectType.DATASOURCE, ObjectType.INFOOBJECT) and ctx.model.object(ref) is None:
        ctx.issue(Severity.WARNING, f"{owner}: ссылка на {ref.type.value} {ref}, которого нет в выгрузке",
                  table, row, owner)


def _dtps(ctx: _Context) -> None:
    for r in ctx.rows("RSBKDTP"):
        name = r["DTP"]
        try:
            dtp = Dtp(name=name, source=parse_ref(r["SRCTP"], r["SRC"]), target=parse_ref(r["TGTTP"], r["TGT"]),
                      mode=r["UPDMODE"].upper())
        except ValueError:
            ctx.issue(Severity.WARNING, f"DTP {name}: тип источника «{r['SRCTP']}» или цели «{r['TGTTP']}» "
                                        "не поддерживается, DTP не разобран", "RSBKDTP", r, name, skipped=True)
            continue
        if dtp.mode not in ("FULL", "DELTA"):
            ctx.issue(Severity.WARNING, f"DTP {name}: неизвестный режим «{dtp.mode}», ожидается FULL или DELTA",
                      "RSBKDTP", r, name)
        _check_ref(ctx, name, dtp.source, "RSBKDTP", r)
        _check_ref(ctx, name, dtp.target, "RSBKDTP", r)
        ctx.model.dtps[name] = dtp


def _guess_ref(ctx: _Context, name: str, cps: set[str]) -> ObjectRef:
    if name in ctx.model.adsos:
        return ObjectRef(type=ObjectType.ADSO, name=name)
    if name in cps:
        return ObjectRef(type=ObjectType.COMPOSITE_PROVIDER, name=name)
    return ObjectRef(type=ObjectType.CLASSIC_DSO, name=name)


def _others(ctx: _Context) -> None:
    cps: dict[str, list[str]] = defaultdict(list)
    for r in ctx.rows("RSOHCPR"):
        cps[r["HCPRNM"]].append(r["PARTPROV"])
    for name, parts in cps.items():
        ctx.model.others.append(OtherObject(
            type=ObjectType.COMPOSITE_PROVIDER, name=name,
            related=[_guess_ref(ctx, p, set(cps)) for p in parts], info=f"участники: {', '.join(parts)}"))
    for r in ctx.rows("RSDODSO"):
        ctx.model.others.append(OtherObject(type=ObjectType.CLASSIC_DSO, name=r["ODSOBJECT"]))
    for r in ctx.rows("RSZCOMPDIR"):
        ctx.model.others.append(OtherObject(type=ObjectType.QUERY, name=r["COMPID"],
                                            related=[_guess_ref(ctx, r["INFOCUBE"], set(cps))],
                                            info=f"на {r['INFOCUBE']}"))
    for r in ctx.rows("RSHIEDIR"):
        ctx.model.others.append(OtherObject(
            type=ObjectType.HIERARCHY, name=r.get("HIENM") or r["HIEID"],
            related=[ObjectRef(type=ObjectType.INFOOBJECT, name=r["IOBJNM"])], info=f"на {r['IOBJNM']}"))
    chains: dict[str, list[Row]] = defaultdict(list)
    for r in ctx.rows("RSPCCHAIN"):
        chains[r["CHAIN_ID"]].append(r)
    for name, steps in chains.items():
        related = []
        for s in steps:
            if s["TYPE"] == "DTP_LOAD":
                related.append(ObjectRef(type=ObjectType.DTP, name=s["VARIANTE"]))
            elif s["TYPE"] == "ADSOACT":
                related.append(ObjectRef(type=ObjectType.ADSO, name=s["VARIANTE"]))
        ctx.model.others.append(OtherObject(type=ObjectType.PROCESS_CHAIN, name=name, related=related,
                                            info=f"шагов: {len(steps)}"))
