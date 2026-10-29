"""Тестовые данные и эталоны для потока «Продажи» (ТЗ, разделы 4.3.5 и 4.3.7).

Создаёт в demo/data:
    md_0SOLD_TO_attr.csv, md_0SOLD_TO_text.csv      20 клиентов, атрибут страна
    md_0MATERIAL_attr.csv, md_0MATERIAL_text.csv    30 материалов, атрибут группа материалов
    ZSALES_FF_load1.csv     первичная загрузка: около 500 позиций за сентябрь и октябрь 2026,
                            пустые даты и 3 повтора ключа внутри загрузки
    ZSALES_FF_load2.csv     корректировки: 50 изменённых позиций (по 10 каждого вида из ТЗ 4.3.7) и 50 новых
    load2_cases.csv         вид каждой корректировки загрузки 2 (колонка _case)
    etalon_ZSD_A01.csv      ожидаемые активные данные ZSD_A01 после двух загрузок
    etalon_ZSD_A02.csv      ожидаемые активные данные ZSD_A02 после двух загрузок
    etalon_ZSD_A02_broken.csv  тот же эталон с 3 намеренно испорченными значениями

Эталоны считаются на pandas по правилам ТЗ 4.3.5 (перезапись в standard ADSO, журнал изменений
с парой X / ' ', суммирование в cumulative ADSO с сохранением нулевых строк) по содержимому самих
файлов загрузок, независимо от SQL приложения. Правильность самого расчёта проверяет тест на примере
из приложения Д ТЗ, посчитанном вручную (demo/data/micro).

Данные синтетические и детерминированные (фиксированное зерно генератора случайных чисел).
Перегенерировать: uv run python demo/tools/make_demo_data.py
"""

from __future__ import annotations

import io
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "data"
SEED = 20261029

COUNTRIES = ["RU", "RU", "RU", "KZ", "BY", "AM", "UZ"]
CUSTOMERS = [
    "Ромашка", "Василёк", "Северный ветер", "Альфа-Трейд", "Берёзка", "Восток-Снаб", "Гранит", "Дельта",
    "Енисей", "Жемчуг", "Заря", "Истра", "Кедр", "Лотос", "Меридиан", "Нева", "Орбита", "Полюс",
    "Радуга", "Сириус",
]
MATERIAL_GROUPS = {"ZFOOD": "Продукты", "ZBEV": "Напитки", "ZHOME": "Товары для дома"}
MATERIALS = [
    ("Чай чёрный 100 г", "ZBEV", "ST"), ("Кофе молотый 250 г", "ZBEV", "ST"), ("Сок яблочный 1 л", "ZBEV", "ST"),
    ("Вода минеральная 0,5 л", "ZBEV", "ST"), ("Морс клюквенный 1 л", "ZBEV", "ST"),
    ("Квас 1,5 л", "ZBEV", "ST"), ("Какао 200 г", "ZBEV", "ST"), ("Компот 1 л", "ZBEV", "ST"),
    ("Мука пшеничная", "ZFOOD", "KG"), ("Сахар", "ZFOOD", "KG"), ("Гречка", "ZFOOD", "KG"),
    ("Рис длиннозёрный", "ZFOOD", "KG"), ("Овсяные хлопья", "ZFOOD", "KG"), ("Макароны", "ZFOOD", "KG"),
    ("Сыр российский", "ZFOOD", "KG"), ("Масло сливочное", "ZFOOD", "KG"), ("Мёд цветочный", "ZFOOD", "KG"),
    ("Печенье овсяное", "ZFOOD", "KG"), ("Пряники тульские", "ZFOOD", "KG"), ("Сушки", "ZFOOD", "KG"),
    ("Губка для посуды", "ZHOME", "ST"), ("Средство для стекла", "ZHOME", "ST"), ("Салфетки", "ZHOME", "ST"),
    ("Пакеты для мусора", "ZHOME", "ST"), ("Свеча декоративная", "ZHOME", "ST"), ("Плед", "ZHOME", "ST"),
    ("Кружка керамическая", "ZHOME", "ST"), ("Полотенце кухонное", "ZHOME", "ST"), ("Фольга", "ZHOME", "ST"),
    ("Лампа настольная", "ZHOME", "ST"),
]

FF_COLUMNS = ["DOC_NUMBER", "S_ORD_ITEM", "SOLD_TO", "MATERIAL", "CALDAY", "NETVAL", "DISCOUNT", "CURRENCY",
              "QTY", "UNIT"]
A01_KEY = ["0DOC_NUMBER", "0S_ORD_ITEM"]
A02_KEY = ["0SOLD_TO", "0CALMONTH", "0DOC_CURRCY", "0SALES_UNIT"]
KEY_FIGURES = {"0NETVAL_INV": 2, "0INV_QTY": 3}

CENT = Decimal("0.01")
MILLI = Decimal("0.001")


# --- форматирование как в выгрузках SAP: «;», десятичная запятая ---

def fmt_dec(value: Decimal, places: int) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def to_csv(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, sep=";", index=False, lineterminator="\r\n")
    return buf.getvalue().encode("utf-8")


# --- справочники ---

def customer_id(i: int) -> str:
    return f"{100000 + i:010d}"  # ALPHA: числовой номер с ведущими нулями до 10 знаков


def material_id(i: int) -> str:
    return f"MAT-{i:04d}"  # нечисловой номер ALPHA не дополняет


def master_data(rnd: random.Random) -> dict[str, pd.DataFrame]:
    sold_to = [customer_id(i) for i in range(1, len(CUSTOMERS) + 1)]
    materials = [material_id(i) for i in range(1, len(MATERIALS) + 1)]
    return {
        "md_0SOLD_TO_attr": pd.DataFrame({"0SOLD_TO": sold_to, "0COUNTRY": [rnd.choice(COUNTRIES) for _ in sold_to]}),
        "md_0SOLD_TO_text": pd.DataFrame({
            "0SOLD_TO": sold_to, "LANGU": "R",
            "TXTSH": [n[:20] for n in CUSTOMERS], "TXTLG": [f"ООО «{n}»" for n in CUSTOMERS],
        }),
        "md_0MATERIAL_attr": pd.DataFrame({"0MATERIAL": materials, "0MATL_GROUP": [m[1] for m in MATERIALS]}),
        "md_0MATERIAL_text": pd.DataFrame({
            "0MATERIAL": materials, "LANGU": "R",
            "TXTSH": [m[0][:20] for m in MATERIALS], "TXTLG": [m[0] for m in MATERIALS],
        }),
    }


# --- загрузки плоского файла ---

# Виды корректировок в загрузке 2 (ТЗ 4.3.7), по 10 позиций каждого вида
CASES = ["(а) сумма", "(б) месяц", "(в) клиент", "(г) валюта", "(д) повтор"]
NEW_CASE = "новая"

# Клиент, у которого единственная позиция в EUR: в загрузке 2 её валюта меняется на RUB,
# и строка ZSD_A02 (клиент, месяц, EUR, единица) обнуляется
ZERO_CUSTOMER = len(CUSTOMERS)


def random_item(rnd: random.Random, currency: str | None = None) -> dict:
    m = rnd.randrange(len(MATERIALS))
    unit = MATERIALS[m][2]
    customer = rnd.randint(1, len(CUSTOMERS) - 1)
    currency = currency or ("EUR" if rnd.random() < 0.15 else "RUB")
    netval = Decimal(rnd.randint(50_00, 2_000_000_00 if currency == "RUB" else 20_000_00)) / 100
    discount = (netval * Decimal(rnd.choice([0, 0, 0, 2, 5, 10])) / 100).quantize(CENT, ROUND_HALF_UP)
    qty = Decimal(rnd.randint(1, 200)) if unit == "ST" else Decimal(rnd.randint(500, 500_000)) / 1000
    day = date(2026, 9, 1) + timedelta(days=rnd.randrange(61))  # сентябрь и октябрь 2026
    return {
        "SOLD_TO": customer_id(customer), "MATERIAL": material_id(m + 1),
        "CALDAY": day, "NETVAL": netval, "DISCOUNT": discount, "CURRENCY": currency,
        "QTY": qty.quantize(MILLI), "UNIT": unit,
    }


def change_amount(rnd: random.Random, row: dict) -> None:
    row["NETVAL"] = (row["NETVAL"] * Decimal(rnd.choice(["0.5", "0.9", "1.1", "1.25"]))).quantize(CENT)
    row["DISCOUNT"] = (row["NETVAL"] * Decimal(rnd.choice([0, 3, 7])) / 100).quantize(CENT, ROUND_HALF_UP)
    row["QTY"] = (row["QTY"] + 1).quantize(MILLI)


def make_loads(rnd: random.Random) -> tuple[list[dict], list[dict], list[dict]]:
    """Загрузка 1, загрузка 2 и контрольный список видов корректировок загрузки 2."""
    key = ("DOC_NUMBER", "S_ORD_ITEM")
    load1: list[dict] = []
    doc = 4_500_000
    while len(load1) < 497:
        doc += 1
        for item in range(1, rnd.randint(1, 6) + 1):
            load1.append({"DOC_NUMBER": f"{doc:010d}", "S_ORD_ITEM": f"{item * 10:06d}", **random_item(rnd)})
    for row in rnd.sample(load1, 5):
        row["CALDAY"] = None  # пустая дата
    doc += 1
    special = {"DOC_NUMBER": f"{doc:010d}", "S_ORD_ITEM": "000010", **random_item(rnd, currency="EUR"),
               "SOLD_TO": customer_id(ZERO_CUSTOMER)}
    load1.append(special)
    # Три повтора ключа внутри загрузки: побеждает последняя запись (ТЗ 4.3.5, правило 3)
    for old in rnd.sample(load1[:-1], 3):
        dup = dict(old)
        change_amount(rnd, dup)
        load1.append(dup)

    effective = {tuple(r[k] for k in key): r for r in load1}  # последняя запись по ключу
    candidates = [r for r in effective.values() if r is not special]
    picked = rnd.sample(candidates, 49)
    load2: list[dict] = []
    cases: list[dict] = []
    for i, old in enumerate([special, *picked]):
        # special и ещё 9 позиций меняют валюту, остальные виды по 10 позиций
        if old is special or 31 <= i <= 39:
            case = CASES[3]
        else:
            case = CASES[4] if i >= 40 else CASES[(i - 1) // 10]
        row = dict(old)
        if case == CASES[0]:
            change_amount(rnd, row)
        elif case == CASES[1]:
            row["CALDAY"] = (row["CALDAY"] or date(2026, 9, 15)) + timedelta(days=31)
        elif case == CASES[2]:
            row["SOLD_TO"] = customer_id(rnd.choice([c for c in range(1, ZERO_CUSTOMER)
                                                     if customer_id(c) != row["SOLD_TO"]]))
        elif case == CASES[3]:
            row["CURRENCY"] = "RUB" if row["CURRENCY"] == "EUR" else "EUR"
        load2.append(row)
        cases.append({"DOC_NUMBER": row["DOC_NUMBER"], "S_ORD_ITEM": row["S_ORD_ITEM"], "_case": case})
    for _ in range(50):
        doc += 1
        row = {"DOC_NUMBER": f"{doc:010d}", "S_ORD_ITEM": "000010", **random_item(rnd)}
        load2.append(row)
        cases.append({"DOC_NUMBER": row["DOC_NUMBER"], "S_ORD_ITEM": row["S_ORD_ITEM"], "_case": NEW_CASE})
    for row in rnd.sample(load2[50:], 2):
        row["CALDAY"] = None
    order = list(range(len(load2)))
    rnd.shuffle(order)
    return load1, [load2[i] for i in order], [cases[i] for i in order]


def load_file(rows: list[dict], day_format: str, empty_day: str) -> pd.DataFrame:
    """Файл загрузки: загрузка 1 с датами ГГГГММДД и пустой датой 00000000,
    загрузка 2 с датами ДД.ММ.ГГГГ (как после Excel) и пустой строкой вместо даты."""
    return pd.DataFrame([
        {
            **r,
            "CALDAY": r["CALDAY"].strftime(day_format) if r["CALDAY"] else empty_day,
            "NETVAL": fmt_dec(r["NETVAL"], 2), "DISCOUNT": fmt_dec(r["DISCOUNT"], 2), "QTY": fmt_dec(r["QTY"], 3),
        }
        for r in rows
    ], columns=FF_COLUMNS)


def parse_load(content: bytes) -> list[dict]:
    """Прочитать файл загрузки так, как его увидит BW: десятичная запятая, даты ГГГГММДД или ДД.ММ.ГГГГ,
    00000000 и пусто — пустая дата. record_no — номер записи в файле, начиная с 1."""
    df = pd.read_csv(io.BytesIO(content), sep=";", dtype=str, keep_default_na=False)

    def day(v: str) -> date | None:
        if v in ("", "00000000"):
            return None
        if "." in v:
            d, m, y = v.split(".")
            return date(int(y), int(m), int(d))
        return date(int(v[:4]), int(v[4:6]), int(v[6:]))

    return [
        {
            **r, "record_no": i, "CALDAY": day(r["CALDAY"]),
            "NETVAL": Decimal(r["NETVAL"].replace(",", ".")), "DISCOUNT": Decimal(r["DISCOUNT"].replace(",", ".")),
            "QTY": Decimal(r["QTY"].replace(",", ".")),
        }
        for i, r in enumerate(df.to_dict("records"), start=1)
    ]


# --- эталоны: независимый расчёт потока на pandas по правилам ТЗ 4.3.5 ---

A01_COLUMNS = ["0DOC_NUMBER", "0S_ORD_ITEM", "0SOLD_TO", "0MATERIAL", "0DISTR_CHAN", "0CALDAY",
               "0NETVAL_INV", "0DOC_CURRCY", "0INV_QTY", "0SALES_UNIT"]
RECORDMODE_ORDER = {"X": 0, " ": 1, "N": 2}


def tr_00(rows: list[dict]) -> pd.DataFrame:
    """ZSALES_FF -> ZSD_A01: прямые присвоения, константа 0DISTR_CHAN = '10', 0NETVAL_INV = NETVAL - DISCOUNT."""
    return pd.DataFrame([
        {
            "record_no": r["record_no"],
            "0DOC_NUMBER": r["DOC_NUMBER"], "0S_ORD_ITEM": r["S_ORD_ITEM"], "0SOLD_TO": r["SOLD_TO"],
            "0MATERIAL": r["MATERIAL"], "0DISTR_CHAN": "10", "0CALDAY": r["CALDAY"],
            "0NETVAL_INV": r["NETVAL"] - r["DISCOUNT"], "0DOC_CURRCY": r["CURRENCY"],
            "0INV_QTY": r["QTY"], "0SALES_UNIT": r["UNIT"],
        }
        for r in rows
    ], columns=["record_no", *A01_COLUMNS])


def activate_standard(active: pd.DataFrame, inbound: pd.DataFrame, load_id: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Активация standard ADSO (правила 3 и 4). Возвращает активные данные и журнал изменений загрузки."""
    # Правило 3: из дублей ключа остаётся запись с наибольшим record_no
    new = (inbound.sort_values("record_no").drop_duplicates(A01_KEY, keep="last")
           .drop(columns="record_no").reset_index(drop=True))
    known = new.merge(active[A01_KEY], on=A01_KEY, how="left", indicator=True)["_merge"] == "both"

    before = active.merge(new[A01_KEY], on=A01_KEY)  # старые значения изменённых ключей
    for kf in KEY_FIGURES:
        before[kf] = before[kf].map(lambda v: -v)
    before["recordmode"] = "X"
    after = new.copy()
    after["recordmode"] = known.map({True: " ", False: "N"})
    changelog = pd.concat([before, after], ignore_index=True)
    changelog["load_id"] = load_id
    changelog = (changelog.assign(_order=changelog["recordmode"].map(RECORDMODE_ORDER))
                 .sort_values([*A01_KEY, "_order"]).drop(columns="_order")
                 [["load_id", "recordmode", *A01_COLUMNS]].reset_index(drop=True))

    untouched = active.merge(new[A01_KEY], on=A01_KEY, how="left", indicator=True)
    untouched = untouched[untouched["_merge"] == "left_only"].drop(columns="_merge")
    result = pd.concat([untouched, new], ignore_index=True).sort_values(A01_KEY, ignore_index=True)
    return result[A01_COLUMNS], changelog


def tr_01(changelog: pd.DataFrame) -> pd.DataFrame:
    """ZSD_A01 -> ZSD_A02: 0CALMONTH из 0CALDAY (пустая дата -> '000000'), прочие поля прямо."""
    out = changelog[["0SOLD_TO", "0DOC_CURRCY", "0SALES_UNIT", "0NETVAL_INV", "0INV_QTY"]].copy()
    out["0CALMONTH"] = changelog["0CALDAY"].map(lambda d: d.strftime("%Y%m") if not pd.isna(d) else "000000")
    return out


def activate_cumulative(active: pd.DataFrame, inbound: pd.DataFrame) -> pd.DataFrame:
    """Активация cumulative ADSO (правило 5): суммирование по ключу; нулевые строки остаются."""
    combined = pd.concat([active, inbound[A02_KEY + list(KEY_FIGURES)]], ignore_index=True)
    return (combined.groupby(A02_KEY, as_index=False)[list(KEY_FIGURES)]
            .agg(lambda s: sum(s, Decimal(0)))
            .sort_values(A02_KEY, ignore_index=True))


def run_flow(loads: list[list[dict]]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Прогнать загрузки по порядку: активные ZSD_A01, журнал изменений ZSD_A01, активные ZSD_A02."""
    a01 = pd.DataFrame(columns=A01_COLUMNS)
    a02 = pd.DataFrame(columns=A02_KEY + list(KEY_FIGURES))
    changelogs = []
    for load_id, rows in enumerate(loads, start=1):
        a01, changelog = activate_standard(a01, tr_00(rows), load_id)
        changelogs.append(changelog)
        a02 = activate_cumulative(a02, tr_01(changelog))
    return a01, pd.concat(changelogs, ignore_index=True), a02


def etalon_file(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df[columns].copy()
    for kf, places in KEY_FIGURES.items():
        out[kf] = out[kf].map(lambda v, p=places: fmt_dec(v, p))
    if "0CALDAY" in out:
        out["0CALDAY"] = out["0CALDAY"].map(lambda d: d.strftime("%Y%m%d") if not pd.isna(d) else "")
    return out


def flow_files(loads: list[bytes]) -> dict[str, pd.DataFrame]:
    """Эталонные файлы по содержимому файлов загрузок."""
    a01, changelog, a02 = run_flow([parse_load(c) for c in loads])
    return {
        "ZSD_A01": etalon_file(a01, A01_COLUMNS),
        "ZSD_A01_changelog": etalon_file(changelog, ["load_id", "recordmode", *A01_COLUMNS]),
        "ZSD_A02": etalon_file(a02, A02_KEY + list(KEY_FIGURES)),
    }


def break_etalon(etalon: pd.DataFrame, rnd: random.Random) -> pd.DataFrame:
    """Три намеренных расхождения: две суммы и одно количество."""
    broken = etalon.copy()
    rows = rnd.sample([i for i in range(len(broken)) if broken.at[i, "0NETVAL_INV"] != "0,00"], 3)
    for i, kf, delta in [(rows[0], "0NETVAL_INV", "100.00"), (rows[1], "0NETVAL_INV", "-0.50"),
                         (rows[2], "0INV_QTY", "1.000")]:
        places = KEY_FIGURES[kf]
        broken.at[i, kf] = fmt_dec(Decimal(broken.at[i, kf].replace(",", ".")) + Decimal(delta), places)
    return broken


def build() -> dict[str, bytes]:
    rnd = random.Random(SEED)
    files = {f"{name}.csv": to_csv(df) for name, df in master_data(rnd).items()}
    load1, load2, cases = make_loads(rnd)
    files["ZSALES_FF_load1.csv"] = to_csv(load_file(load1, "%Y%m%d", "00000000"))
    files["ZSALES_FF_load2.csv"] = to_csv(load_file(load2, "%d.%m.%Y", ""))
    files["load2_cases.csv"] = to_csv(pd.DataFrame(cases))

    etalons = flow_files([files["ZSALES_FF_load1.csv"], files["ZSALES_FF_load2.csv"]])
    files["etalon_ZSD_A01.csv"] = to_csv(etalons["ZSD_A01"])
    files["etalon_ZSD_A02.csv"] = to_csv(etalons["ZSD_A02"])
    files["etalon_ZSD_A02_broken.csv"] = to_csv(break_etalon(etalons["ZSD_A02"], rnd))
    return files


def write(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, content in build().items():
        path = out / name
        path.write_bytes(content)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in write():
        print(p)
