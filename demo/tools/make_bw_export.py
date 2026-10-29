"""Генератор синтетической выгрузки метаданных SAP BW/4HANA (ТЗ, приложение Б).

Поток «Продажи»:
    ZSALES_FF (плоский файл) -> TR_00 -> ZSD_A01 (standard) -> TR_01 -> ZSD_A02 (cumulative)
Плюс объекты вне потока для отчёта о сложности и тестов генерации (приложение Б.4).

Результат лежит в demo/bw_export и закоммичен. После правки этого файла перегенерируй:
    uv run python demo/tools/make_bw_export.py

Колонки каждого файла берутся из dbtransfer/rules/bw_tables.yaml, поэтому набор
всегда совпадает с тем, что ожидает импорт. Формат как у выгрузки SE16
«в электронную таблицу» с техническими именами полей: разделитель «;»,
UTF-8, а RSDIOBJT.csv в Windows-1251, как это часто бывает при выгрузке из SAP GUI.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from dbtransfer.config import load_bw_tables

OUT = Path(__file__).resolve().parents[1] / "bw_export"

# Файлы не в UTF-8: так импорт проверяется на реальных особенностях выгрузок
ENCODING = {"RSDIOBJT": "cp1251"}

RU, EN = "R", "E"  # ключи языков SAP (SPRAS)

# --- InfoObject (приложение Б.1) ---
# имя, вид, тип, длина, конвертация, атрибуты, есть тексты, есть иерархии, текст ru, текст en
CHARACTERISTICS = [
    ("0DOC_NUMBER", "CHA", "CHAR", 10, "ALPHA", [], False, False, "Документ сбыта", "Sales document"),
    ("0S_ORD_ITEM", "CHA", "NUMC", 6, "", [], False, False, "Позиция документа сбыта", "Sales document item"),
    ("0SOLD_TO", "CHA", "CHAR", 10, "ALPHA", ["0COUNTRY"], True, False, "Заказчик", "Sold-to party"),
    ("0COUNTRY", "CHA", "CHAR", 3, "", [], True, False, "Страна", "Country"),
    ("0MATERIAL", "CHA", "CHAR", 40, "ALPHA", ["0MATL_GROUP"], True, True, "Материал", "Material"),
    ("0MATL_GROUP", "CHA", "CHAR", 9, "", [], True, False, "Группа материалов", "Material group"),
    ("0DISTR_CHAN", "CHA", "CHAR", 2, "", [], False, False, "Канал сбыта", "Distribution channel"),
    ("0CALDAY", "TIM", "DATS", 8, "", [], False, False, "Календарный день", "Calendar day"),
    ("0CALMONTH", "TIM", "NUMC", 6, "PERI6", [], False, False, "Календарный год/месяц", "Calendar year/month"),
    ("0DOC_CURRCY", "UNI", "CUKY", 5, "", [], False, False, "Валюта документа", "Document currency"),
    ("0SALES_UNIT", "UNI", "UNIT", 3, "CUNIT", [], False, False, "Единица продажи", "Sales unit"),
]

# имя, тип показателя, тип данных, длина, знаки, агрегация, InfoObject валюты/единицы, текст ru, текст en
KEY_FIGURES = [
    ("0NETVAL_INV", "AMO", "CURR", 17, 2, "SUM", "0DOC_CURRCY", "Нетто-стоимость фактуры", "Net invoice value"),
    ("0INV_QTY", "QUA", "QUAN", 17, 3, "SUM", "0SALES_UNIT", "Фактурированное количество", "Invoiced quantity"),
]

# --- ADSO (приложения Б.2 и Б.4) ---
# имя, вид, текст ru, текст en, ключевые InfoObject, прочие InfoObject
DATASTORES = [
    ("ZSD_A01", "standard", "Продажи: позиции документов", "Sales: document items",
     ["0DOC_NUMBER", "0S_ORD_ITEM"],
     ["0SOLD_TO", "0MATERIAL", "0DISTR_CHAN", "0CALDAY", "0NETVAL_INV", "0DOC_CURRCY", "0INV_QTY", "0SALES_UNIT"]),
    ("ZSD_A02", "cumulative", "Продажи по клиентам и месяцам", "Sales by customer and month",
     ["0SOLD_TO", "0CALMONTH", "0DOC_CURRCY", "0SALES_UNIT"],
     ["0NETVAL_INV", "0INV_QTY"]),
    ("ZSD_S01", "staging", "Продажи: промежуточный слой", "Sales: staging",
     [],
     ["0DOC_NUMBER", "0S_ORD_ITEM", "0MATERIAL", "0MATL_GROUP", "0CALDAY", "0INV_QTY", "0SALES_UNIT"]),
    ("ZSD_D01", "direct", "План продаж", "Sales plan",
     ["0SOLD_TO", "0CALMONTH"],
     ["0NETVAL_INV", "0DOC_CURRCY"]),
    ("ZSD_A03", "standard", "Продажи по материалам (с рутиной)", "Sales by material (with routine)",
     ["0SOLD_TO", "0MATERIAL", "0CALDAY"],
     ["0NETVAL_INV", "0DOC_CURRCY"]),
    ("ZSD_A04", "cumulative", "Продажи по группам материалов", "Sales by material group",
     ["0MATL_GROUP", "0CALMONTH", "0DOC_CURRCY", "0SALES_UNIT"],
     ["0NETVAL_INV", "0INV_QTY"]),
]
# Неактивный ADSO: есть только версия M, импорт должен его пропустить
INACTIVE_ADSO = "ZSD_A99"

ADSO_FLAGS = {
    # ACTIVATE, CHANGELOG, ALLCHARKEY, DIRECTUPDATE
    "staging": ("", "", "", ""),
    "standard": ("X", "X", "", ""),
    "cumulative": ("X", "", "X", ""),
    "direct": ("", "", "", "X"),
}
# Какие таблицы BW генерирует для вида ADSO: 1 входящая, 2 активная, 3 журнал изменений
ADSO_TABLES = {"staging": "1", "standard": "123", "cumulative": "12", "direct": "2"}

# --- DataSource (приложение Б.2) ---
DATASOURCE, LOGSYS = "ZSALES_FF", "FLATFILE"
# поле, InfoObject, тип, длина, знаки
DATASOURCE_FIELDS = [
    ("DOC_NUMBER", "0DOC_NUMBER", "CHAR", 10, 0),
    ("S_ORD_ITEM", "0S_ORD_ITEM", "NUMC", 6, 0),
    ("SOLD_TO", "0SOLD_TO", "CHAR", 10, 0),
    ("MATERIAL", "0MATERIAL", "CHAR", 40, 0),
    ("CALDAY", "0CALDAY", "DATS", 8, 0),
    ("NETVAL", "", "CURR", 17, 2),
    ("DISCOUNT", "", "CURR", 17, 2),
    ("CURRENCY", "0DOC_CURRCY", "CUKY", 5, 0),
    ("QTY", "0INV_QTY", "QUAN", 17, 3),
    ("UNIT", "0SALES_UNIT", "UNIT", 3, 0),
]

# --- Трансформации (приложение Б.3 и Б.4) ---
# Имена TR_00…TR_03 из ТЗ живут только в комментариях и EXPECTED.md: в BW это 32-символьные ID
TR_00 = "0B6F3C1A9D2E4F5A8B7C6D5E4F3A2B10"
TR_01 = "1C7A4D2B0E3F5A6B9C8D7E6F5A4B3C21"
TR_02 = "2D8B5E3C1F4A6B7C0D9E8F7A6B5C4D32"
TR_03 = "3E9C6F4D2A5B7C8D1E0F9A8B7C6D5E43"
END_ROUTINE_TR_02 = "9A0B1C2D3E4F5A6B7C8D9E0F1A2B3C4D"


def ds_name(name: str = DATASOURCE, logsys: str = LOGSYS) -> str:
    """Имя DataSource в RSTRAN и RSBKDTP: имя, дополненное пробелами до 30 символов, затем система."""
    return f"{name:<30}{logsys}"


# id, тип и имя источника, тип и имя цели, конечная рутина
TRANSFORMATIONS = [
    (TR_00, "RSDS", ds_name(), "ADSO", "ZSD_A01", ""),
    (TR_01, "ADSO", "ZSD_A01", "ADSO", "ZSD_A02", ""),
    (TR_02, "ADSO", "ZSD_A01", "ADSO", "ZSD_A03", END_ROUTINE_TR_02),
    (TR_03, "ADSO", "ZSD_A01", "ADSO", "ZSD_A04", ""),
]

# Правила: вид, поля источника, поля цели, параметр (константа, формула, атрибут)
RULES = {
    TR_00: [
        ("DIRECT", ["DOC_NUMBER"], ["0DOC_NUMBER"], ""),
        ("DIRECT", ["S_ORD_ITEM"], ["0S_ORD_ITEM"], ""),
        ("DIRECT", ["SOLD_TO"], ["0SOLD_TO"], ""),
        ("DIRECT", ["MATERIAL"], ["0MATERIAL"], ""),
        ("CONSTANT", [], ["0DISTR_CHAN"], "10"),
        ("DIRECT", ["CALDAY"], ["0CALDAY"], ""),
        ("FORMULA", ["NETVAL", "DISCOUNT"], ["0NETVAL_INV"], "NETVAL - DISCOUNT"),
        ("DIRECT", ["CURRENCY"], ["0DOC_CURRCY"], ""),
        ("DIRECT", ["QTY"], ["0INV_QTY"], ""),
        ("DIRECT", ["UNIT"], ["0SALES_UNIT"], ""),
    ],
    TR_01: [
        ("DIRECT", ["0SOLD_TO"], ["0SOLD_TO"], ""),
        ("TIME", ["0CALDAY"], ["0CALMONTH"], ""),
        ("DIRECT", ["0DOC_CURRCY"], ["0DOC_CURRCY"], ""),
        ("DIRECT", ["0SALES_UNIT"], ["0SALES_UNIT"], ""),
        ("DIRECT", ["0NETVAL_INV"], ["0NETVAL_INV"], ""),
        ("DIRECT", ["0INV_QTY"], ["0INV_QTY"], ""),
    ],
    TR_02: [
        ("DIRECT", ["0SOLD_TO"], ["0SOLD_TO"], ""),
        ("DIRECT", ["0MATERIAL"], ["0MATERIAL"], ""),
        ("DIRECT", ["0CALDAY"], ["0CALDAY"], ""),
        ("DIRECT", ["0NETVAL_INV"], ["0NETVAL_INV"], ""),
        ("DIRECT", ["0DOC_CURRCY"], ["0DOC_CURRCY"], ""),
    ],
    TR_03: [
        ("READ_MD", ["0MATERIAL"], ["0MATL_GROUP"], "0MATERIAL-0MATL_GROUP"),
        ("TIME", ["0CALDAY"], ["0CALMONTH"], ""),
        ("DIRECT", ["0DOC_CURRCY"], ["0DOC_CURRCY"], ""),
        ("DIRECT", ["0SALES_UNIT"], ["0SALES_UNIT"], ""),
        ("DIRECT", ["0NETVAL_INV"], ["0NETVAL_INV"], ""),
        ("DIRECT", ["0INV_QTY"], ["0INV_QTY"], ""),
    ],
}

ROUTINES = {
    END_ROUTINE_TR_02: [
        "METHOD end_routine.",
        "* Убираем строки с нулевой выручкой и пересчитываем EUR в RUB по плановому курсу",
        "  DELETE RESULT_PACKAGE WHERE netval_inv = 0.",
        "  LOOP AT RESULT_PACKAGE ASSIGNING <result_fields>.",
        "    IF <result_fields>-doc_currcy = 'EUR'.",
        "      <result_fields>-netval_inv = <result_fields>-netval_inv * '95.50'.",
        "      <result_fields>-doc_currcy = 'RUB'.",
        "    ENDIF.",
        "  ENDLOOP.",
        "ENDMETHOD.",
    ],
}

# --- DTP ---
# имя, тип и имя источника, тип и имя цели, режим.
# Оба DTP демо-потока дельта; полная загрузка есть только вне потока (для golden-теста SQL)
DTPS = [
    ("DTP_00ZSALESFF0ZSDA01000000", "RSDS", ds_name(), "ADSO", "ZSD_A01", "DELTA"),
    ("DTP_01ZSDA010ZSDA020000000", "ADSO", "ZSD_A01", "ADSO", "ZSD_A02", "DELTA"),
    ("DTP_02ZSDA010ZSDA030000000", "ADSO", "ZSD_A01", "ADSO", "ZSD_A03", "DELTA"),
    ("DTP_03ZSDA010ZSDA040000000", "ADSO", "ZSD_A01", "ADSO", "ZSD_A04", "FULL"),
]

# --- Объекты только для отчёта о сложности (приложение Б.4) ---
COMPOSITE_PROVIDERS = [("ZSD_CP01", ["ZSD_A02", "ZSD_D01"])]
QUERIES = [("ZSD_Q001", "ZSD_CP01")]
HIERARCHIES = [("5B6C7D8E9F0A1B2C3D4E5F6A7B", "0MATERIAL", "ZMAT_PRODUCT_TREE")]
CLASSIC_DSO = [("ZOLD_DSO", "")]
PROCESS_CHAINS = [
    ("ZSD_PC01", [
        ("TRIGGER", "ZSD_PC01_START"),
        ("DTP_LOAD", DTPS[0][0]),
        ("ADSOACT", "ZSD_A01"),
        ("DTP_LOAD", DTPS[1][0]),
        ("ADSOACT", "ZSD_A02"),
    ]),
]

INT_TYPE = {
    "CHAR": "C", "NUMC": "N", "DATS": "D", "CUKY": "C", "UNIT": "C",
    "CURR": "P", "QUAN": "P", "INT4": "X", "LANG": "C",
}


def col_name(iobj: str) -> str:
    """Поле InfoObject в сгенерированных таблицах BW: 0CALDAY -> CALDAY, ZCUST -> /BIC/ZCUST."""
    return iobj[1:] if iobj.startswith("0") else f"/BIC/{iobj}"


def rollname(iobj: str) -> str:
    """Элемент данных InfoObject: /BI0/OICALDAY, /BIC/OIZCUST."""
    return f"/BI0/OI{iobj[1:]}" if iobj.startswith("0") else f"/BIC/OI{iobj}"


def md_table(prefix: str, iobj: str) -> str:
    """Таблица справочника: P атрибуты, T тексты. 0SOLD_TO -> /BI0/PSOLD_TO."""
    return f"/BI0/{prefix}{iobj[1:]}" if iobj.startswith("0") else f"/BIC/{prefix}{iobj}"


def iobj_types() -> dict[str, tuple[str, int, int]]:
    types = {c[0]: (c[2], c[3], 0) for c in CHARACTERISTICS}
    types.update({k[0]: (k[2], k[3], k[4]) for k in KEY_FIGURES})
    return types


def unit_of(iobj: str) -> str:
    return next((k[6] for k in KEY_FIGURES if k[0] == iobj), "")


def dd03l(tab: str, field: str, pos: int, key: bool, roll: str, dtype: str, length: int, dec: int = 0,
          reffield: str = "") -> dict[str, str]:
    if INT_TYPE[dtype] == "P":
        intlen = (length + 2) // 2
    elif dtype == "INT4":
        intlen = 4
    else:
        intlen = length
    return {
        "TABNAME": tab, "FIELDNAME": field, "AS4LOCAL": "A", "AS4VERS": "0000", "POSITION": f"{pos:04d}",
        "KEYFLAG": "X" if key else "", "ROLLNAME": roll, "INTTYPE": INT_TYPE[dtype], "INTLEN": f"{intlen:06d}",
        "DATATYPE": dtype, "LENG": f"{length:06d}", "DECIMALS": f"{dec:06d}",
        "REFTABLE": tab if reffield else "", "REFFIELD": reffield,
    }


TECHNICAL_KEY = [("REQTSN", "RSPM_REQUEST_TSN", "CHAR", 23), ("DATAPAKID", "RSDATAPID", "NUMC", 6),
                 ("RECORD", "RSDATARID", "INT4", 10)]


def adso_table(tab: str, keys: list[str], fields: list[str], technical_key: bool) -> list[dict[str, str]]:
    """Входящая таблица и журнал изменений имеют технический ключ запроса, активная — ключ ADSO."""
    types = iobj_types()
    rows = []
    if technical_key:
        for name, roll, dtype, length in TECHNICAL_KEY:
            rows.append(dd03l(tab, name, len(rows) + 1, True, roll, dtype, length))
        rows.append(dd03l(tab, "RECORDMODE", len(rows) + 1, False, "RODMUPDMOD", "CHAR", 1))
    for iobj in keys + fields:
        dtype, length, dec = types[iobj]
        unit = unit_of(iobj)
        rows.append(dd03l(tab, col_name(iobj), len(rows) + 1, iobj in keys and not technical_key,
                          rollname(iobj), dtype, length, dec, reffield=col_name(unit) if unit else ""))
    return rows


def build() -> dict[str, list[dict[str, str]]]:
    t: dict[str, list[dict[str, str]]] = {}
    types = iobj_types()

    t["RSDIOBJ"] = [{"IOBJNM": c[0], "OBJVERS": "A", "OBJSTAT": "ACT", "IOBJTP": c[1]} for c in CHARACTERISTICS]
    t["RSDIOBJ"] += [{"IOBJNM": k[0], "OBJVERS": "A", "OBJSTAT": "ACT", "IOBJTP": "KYF"} for k in KEY_FIGURES]

    t["RSDIOBJT"] = []
    for name, ru, en in [(c[0], c[8], c[9]) for c in CHARACTERISTICS] + [(k[0], k[7], k[8]) for k in KEY_FIGURES]:
        t["RSDIOBJT"].append({"LANGU": RU, "IOBJNM": name, "OBJVERS": "A", "TXTSH": ru[:20], "TXTLG": ru})
        t["RSDIOBJT"].append({"LANGU": EN, "IOBJNM": name, "OBJVERS": "A", "TXTSH": en[:20], "TXTLG": en})

    t["RSDCHABAS"] = [
        {
            "CHABASNM": c[0], "OBJVERS": "A", "DATATP": c[2], "INTLEN": f"{c[3]:06d}", "OUTPUTLEN": f"{c[3]:06d}",
            "CONVEXIT": c[4], "ATTRIBFL": "X" if c[5] else "", "TXTTABFL": "X" if c[6] else "",
            "HIETABFL": "X" if c[7] else "",
        }
        for c in CHARACTERISTICS
    ]
    t["RSDKYF"] = [
        {"KYFNM": k[0], "OBJVERS": "A", "KYFTP": k[1], "DATATP": k[2], "AGGREGATE": k[5], "UNINM": k[6]}
        for k in KEY_FIGURES
    ]
    t["RSDBCHATR"] = [
        {"CHABASNM": c[0], "ATTRINM": attr, "OBJVERS": "A", "POSIT": f"{i:04d}", "TIMDEPFL": ""}
        for c in CHARACTERISTICS
        for i, attr in enumerate(c[5], start=1)
    ]

    t["RSOADSO"] = []
    t["RSOADSOT"] = []
    for name, kind, ru, en, _keys, _fields in DATASTORES:
        activate, changelog, allcharkey, direct = ADSO_FLAGS[kind]
        t["RSOADSO"].append({
            "ADSONM": name, "OBJVERS": "A", "OBJSTAT": "ACT", "INFOAREA": "ZSD",
            "ACTIVATE": activate, "CHANGELOG": changelog, "ALLCHARKEY": allcharkey, "DIRECTUPDATE": direct,
        })
        t["RSOADSOT"].append({"LANGU": RU, "ADSONM": name, "OBJVERS": "A", "TXTLG": ru})
        t["RSOADSOT"].append({"LANGU": EN, "ADSONM": name, "OBJVERS": "A", "TXTLG": en})
    t["RSOADSO"].append({
        "ADSONM": INACTIVE_ADSO, "OBJVERS": "M", "OBJSTAT": "INA", "INFOAREA": "ZSD",
        "ACTIVATE": "X", "CHANGELOG": "X", "ALLCHARKEY": "", "DIRECTUPDATE": "",
    })

    rows: list[dict[str, str]] = []
    for name, kind, _ru, _en, keys, fields in DATASTORES:
        for digit in ADSO_TABLES[kind]:
            rows += adso_table(f"/BIC/A{name}{digit}", keys, fields, technical_key=digit in "13")
    for c in CHARACTERISTICS:
        name, attrs, has_texts = c[0], c[5], c[6]
        dtype, length, _ = types[name]
        if attrs:
            tab = md_table("P", name)
            rows.append(dd03l(tab, col_name(name), 1, True, rollname(name), dtype, length))
            rows.append(dd03l(tab, "OBJVERS", 2, True, "RSOBJVERS", "CHAR", 1))
            rows.append(dd03l(tab, "CHANGED", 3, False, "RSCHAVL", "CHAR", 1))
            for i, attr in enumerate(attrs, start=4):
                a_type, a_len, _ = types[attr]
                rows.append(dd03l(tab, col_name(attr), i, False, rollname(attr), a_type, a_len))
        if has_texts:
            tab = md_table("T", name)
            rows.append(dd03l(tab, "LANGU", 1, True, "LANGU", "LANG", 1))
            rows.append(dd03l(tab, col_name(name), 2, True, rollname(name), dtype, length))
            rows.append(dd03l(tab, "TXTSH", 3, False, "RSTXTSH", "CHAR", 20))
            rows.append(dd03l(tab, "TXTMD", 4, False, "RSTXTMD", "CHAR", 40))
            rows.append(dd03l(tab, "TXTLG", 5, False, "RSTXTLG", "CHAR", 60))
    t["DD03L"] = rows

    t["RSDS"] = [{"DATASOURCE": DATASOURCE, "LOGSYS": LOGSYS, "OBJVERS": "A", "OBJSTAT": "ACT",
                  "SRCTYPE": "FILE", "DELTAPROCESS": "FULL"}]
    t["RSDST"] = [
        {"LANGU": RU, "DATASOURCE": DATASOURCE, "LOGSYS": LOGSYS, "OBJVERS": "A", "TXTLG": "Продажи из файла"},
        {"LANGU": EN, "DATASOURCE": DATASOURCE, "LOGSYS": LOGSYS, "OBJVERS": "A", "TXTLG": "Sales flat file"},
    ]
    t["RSDSSEGFD"] = [
        {
            "DATASOURCE": DATASOURCE, "LOGSYS": LOGSYS, "OBJVERS": "A", "SEGID": "0001", "POSIT": f"{i:04d}",
            "FIELDNM": f[0], "IOBJNM": f[1], "KEYFLAG": "", "DATATYPE": f[2],
            "LENG": f"{f[3]:06d}", "DECIMALS": f"{f[4]:06d}",
        }
        for i, f in enumerate(DATASOURCE_FIELDS, start=1)
    ]

    t["RSTRAN"] = [
        {
            "TRANID": tid, "OBJVERS": "A", "OBJSTAT": "ACT", "SOURCETYPE": stype, "SOURCENAME": sname,
            "TARGETTYPE": ttype, "TARGETNAME": tname, "ENDROUTINE": end,
        }
        for tid, stype, sname, ttype, tname, end in TRANSFORMATIONS
    ]
    t["RSTRANRULE"] = []
    t["RSTRANFIELD"] = []
    for tid, rules in RULES.items():
        for rule_no, (rtype, sources, targets, param) in enumerate(rules, start=1):
            t["RSTRANRULE"].append({"TRANID": tid, "OBJVERS": "A", "RULEID": f"{rule_no:04d}", "SEGID": "0001",
                                    "RULETYPE": rtype, "PARAM": param})
            fields = [("0", f) for f in sources] + [("1", f) for f in targets]
            for i, (ptype, field) in enumerate(fields, start=1):
                t["RSTRANFIELD"].append({"TRANID": tid, "OBJVERS": "A", "RULEID": f"{rule_no:04d}", "SEGID": "0001",
                                         "PARAMTYPE": ptype, "PARAMNM": f"P{i:03d}", "FIELDNM": field})
    t["RSAABAP"] = [
        {"CODEID": code_id, "OBJVERS": "A", "LINE_NO": f"{i:06d}", "LINE": line}
        for code_id, lines in ROUTINES.items()
        for i, line in enumerate(lines, start=1)
    ]

    t["RSBKDTP"] = [
        {"DTP": d[0], "OBJVERS": "A", "OBJSTAT": "ACT", "SRCTP": d[1], "SRC": d[2], "TGTTP": d[3], "TGT": d[4],
         "UPDMODE": d[5]}
        for d in DTPS
    ]

    t["RSOHCPR"] = [{"HCPRNM": cp, "OBJVERS": "A", "PARTPROV": p} for cp, parts in COMPOSITE_PROVIDERS for p in parts]
    t["RSDODSO"] = [{"ODSOBJECT": name, "OBJVERS": "A", "ODSOTYPE": tp} for name, tp in CLASSIC_DSO]
    t["RSZCOMPDIR"] = [{"COMPID": q, "OBJVERS": "A", "DEFTP": "REP", "INFOCUBE": prov} for q, prov in QUERIES]
    t["RSHIEDIR"] = [{"HIEID": hid, "OBJVERS": "A", "IOBJNM": iobj, "HIENM": hnm} for hid, iobj, hnm in HIERARCHIES]
    t["RSPCCHAIN"] = [
        {"CHAIN_ID": chain, "OBJVERS": "A", "TYPE": tp, "VARIANTE": var}
        for chain, steps in PROCESS_CHAINS
        for tp, var in steps
    ]
    return t


def render() -> dict[str, bytes]:
    """Содержимое файлов выгрузки: имя файла -> байты. Колонки строго по bw_tables.yaml."""
    spec = load_bw_tables()
    tables = build()
    unknown = set(tables) - set(spec)
    if unknown:
        raise ValueError(f"Таблиц нет в bw_tables.yaml: {sorted(unknown)}")
    files = {}
    for name, table in spec.items():
        columns = list(table.columns)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, delimiter=";", lineterminator="\r\n", restval="")
        writer.writeheader()
        for row in tables.get(name, []):
            extra = set(row) - set(columns)
            if extra:
                raise ValueError(f"{name}: колонок {sorted(extra)} нет в bw_tables.yaml")
            writer.writerow(row)
        files[f"{name}.csv"] = buf.getvalue().encode(ENCODING.get(name, "utf-8"))
    return files


def write(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for file_name, content in render().items():
        path = out / file_name
        path.write_bytes(content)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in write():
        print(p)
