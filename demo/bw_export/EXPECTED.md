# Контрольный список демо-выгрузки

По этому списку проверяется импорт и отчёт о сложности (ТЗ, испытания ПИ-2 и ПИ-4).
Набор создаётся скриптом `demo/tools/make_bw_export.py`; если меняешь скрипт, обнови и этот файл.

## Особенности формата

- Разделитель `;`, первая строка с техническими именами полей, переводы строк CRLF.
- `RSDIOBJT.csv` в кодировке Windows-1251, остальные файлы в UTF-8.
- Имя DataSource в `RSTRAN` и `RSBKDTP` записано как в BW: имя, дополненное пробелами до 30 символов, затем исходная система (`ZSALES_FF` + `FLATFILE`).
- Колонки `RSOADSO` (флаги вида ADSO), `RSTRANRULE`, `RSTRANFIELD`, `RSAABAP` и `RSOHCPR` — гипотеза, см. пометки `hypothesis` в `dbtransfer/rules/bw_tables.yaml`.

## Число активных объектов по типам

| Тип | Число | Объекты |
|---|---|---|
| InfoObject: признаки | 7 | 0DOC_NUMBER, 0S_ORD_ITEM, 0SOLD_TO, 0COUNTRY, 0MATERIAL, 0MATL_GROUP, 0DISTR_CHAN |
| InfoObject: время | 2 | 0CALDAY, 0CALMONTH |
| InfoObject: единицы и валюты | 2 | 0DOC_CURRCY, 0SALES_UNIT |
| InfoObject: показатели | 2 | 0NETVAL_INV, 0INV_QTY |
| ADSO standard | 2 | ZSD_A01, ZSD_A03 |
| ADSO cumulative | 2 | ZSD_A02, ZSD_A04 |
| ADSO staging | 1 | ZSD_S01 |
| ADSO direct update | 1 | ZSD_D01 |
| DataSource (flat file) | 1 | ZSALES_FF / FLATFILE |
| Трансформации | 4 | TR_00, TR_01, TR_02, TR_03 |
| DTP | 4 | 3 дельта (оба DTP демо-потока и DTP в ZSD_A03), 1 полная (ZSD_A01 → ZSD_A04) |
| CompositeProvider | 1 | ZSD_CP01 (участники ZSD_A02, ZSD_D01) |
| BEx-запросы | 1 | ZSD_Q001 на ZSD_CP01 |
| Иерархии | 1 | ZMAT_PRODUCT_TREE на 0MATERIAL |
| Цепочки процессов | 1 | ZSD_PC01 (5 шагов) |
| Классические DSO | 1 | ZOLD_DSO |

Неактивные объекты, которые импорт должен пропустить и перечислить в отчёте о проверке: `ZSD_A99` (ADSO, только версия M).

## Трансформации

| Имя в ТЗ | TRANID | Источник → цель | Уровень | Причина |
|---|---|---|---|---|
| TR_00 | 0B6F3C1A9D2E4F5A8B7C6D5E4F3A2B10 | ZSALES_FF → ZSD_A01 | 1 | Прямые присвоения, константа `'10'`, формула `NETVAL - DISCOUNT` |
| TR_01 | 1C7A4D2B0E3F5A6B9C8D7E6F5A4B3C21 | ZSD_A01 → ZSD_A02 | 1 | Прямые присвоения, преобразование времени 0CALDAY → 0CALMONTH |
| TR_02 | 2D8B5E3C1F4A6B7C0D9E8F7A6B5C4D32 | ZSD_A01 → ZSD_A03 | 3 | Прямые присвоения и конечная рутина ABAP (10 строк) |
| TR_03 | 3E9C6F4D2A5B7C8D1E0F9A8B7C6D5E43 | ZSD_A01 → ZSD_A04 | 2 | Чтение атрибута 0MATL_GROUP справочника 0MATERIAL |

## Уровни прочих объектов

| Объект | Уровень | Причина |
|---|---|---|
| Все InfoObject, ADSO, DataSource, DTP | 1 | Генерируются автоматически |
| ZSD_CP01 | 2 | CompositeProvider не генерируется в прототипе |
| ZOLD_DSO | 2 | Классический DSO |
| ZSD_Q001 | 3 | BEx-запрос |
| ZMAT_PRODUCT_TREE | 3 | Иерархия |
| ZSD_PC01 | 3 | Цепочка процессов |

## Рутины

| Трансформация | Вид | CODEID | Строк кода |
|---|---|---|---|
| TR_02 | Конечная (ENDROUTINE) | 9A0B1C2D3E4F5A6B7C8D9E0F1A2B3C4D | 10 |

## Lineage полей (ПИ-16)

| Поле и направление | Ожидаемый путь |
|---|---|
| `ZSD_A02.0NETVAL_INV`, откуда пришло | ← прямое (TR_01) ← `ZSD_A01.0NETVAL_INV` ← формула `NETVAL - DISCOUNT` (TR_00) ← `ZSALES_FF.NETVAL` и `ZSALES_FF.DISCOUNT` |
| `ZSD_A02.0CALMONTH`, откуда пришло | ← время (TR_01) ← `ZSD_A01.0CALDAY` ← прямое (TR_00) ← `ZSALES_FF.CALDAY` |
| `ZSALES_FF.NETVAL`, куда идёт | → формула (TR_00) → `ZSD_A01.0NETVAL_INV` → прямое (TR_01) → `ZSD_A02.0NETVAL_INV` → `ZSD_CP01` (на уровне объекта); ветки → прямое (TR_02, возможно изменено рутиной) → `ZSD_A03.0NETVAL_INV` и → прямое (TR_03) → `ZSD_A04.0NETVAL_INV` |
| `ZSD_A01.0DISTR_CHAN`, откуда пришло | ← константа `'10'` (TR_00), путь завершён |
| `ZSD_A04.0MATL_GROUP`, откуда пришло | ← чтение атрибута (TR_03) ← `ZSD_A01.0MATERIAL` (← прямое (TR_00) ← `ZSALES_FF.MATERIAL`) и справочник `md.material_attr.matl_group` |
| `ZSD_A03.0NETVAL_INV`, откуда пришло | ← прямое (TR_02), шаг помечен «возможно изменено рутиной» ← `ZSD_A01.0NETVAL_INV` ← … как в первой строке |
