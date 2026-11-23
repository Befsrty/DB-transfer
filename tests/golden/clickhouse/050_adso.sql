-- Таблицы ADSO
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект clickhouse.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a01_inbound
(
    load_id    Int64 COMMENT 'Служебная: номер загрузки (meta.load_log)',
    record_no  Int64 COMMENT 'Служебная: номер записи в загрузке',
    recordmode String COMMENT 'Служебная: N новая запись, X before image, \' \' after image',
    doc_number String COMMENT 'BW: ZSD_A01.0DOC_NUMBER (CHAR 10). Документ сбыта',
    s_ord_item String COMMENT 'BW: ZSD_A01.0S_ORD_ITEM (NUMC 6). Позиция документа сбыта',
    sold_to    Nullable(String) COMMENT 'BW: ZSD_A01.0SOLD_TO (CHAR 10). Заказчик',
    material   Nullable(String) COMMENT 'BW: ZSD_A01.0MATERIAL (CHAR 40). Материал',
    distr_chan Nullable(String) COMMENT 'BW: ZSD_A01.0DISTR_CHAN (CHAR 2). Канал сбыта',
    calday     Nullable(Date32) COMMENT 'BW: ZSD_A01.0CALDAY (DATS 8). Календарный день',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A01.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    doc_currcy Nullable(String) COMMENT 'BW: ZSD_A01.0DOC_CURRCY (CUKY 5). Валюта документа',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_A01.0INV_QTY (QUAN 17,3). Фактурированное количество',
    sales_unit Nullable(String) COMMENT 'BW: ZSD_A01.0SALES_UNIT (UNIT 3). Единица продажи'
)
ENGINE = MergeTree
ORDER BY (load_id, record_no)
COMMENT 'BW: ADSO ZSD_A01 (standard), входящая таблица, таблица BW /BIC/AZSD_A011. Продажи: позиции документов';

CREATE TABLE IF NOT EXISTS dbt_dwh.zsd_a01_active
(
    doc_number String COMMENT 'BW: ZSD_A01.0DOC_NUMBER (CHAR 10). Документ сбыта',
    s_ord_item String COMMENT 'BW: ZSD_A01.0S_ORD_ITEM (NUMC 6). Позиция документа сбыта',
    sold_to    Nullable(String) COMMENT 'BW: ZSD_A01.0SOLD_TO (CHAR 10). Заказчик',
    material   Nullable(String) COMMENT 'BW: ZSD_A01.0MATERIAL (CHAR 40). Материал',
    distr_chan Nullable(String) COMMENT 'BW: ZSD_A01.0DISTR_CHAN (CHAR 2). Канал сбыта',
    calday     Nullable(Date32) COMMENT 'BW: ZSD_A01.0CALDAY (DATS 8). Календарный день',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A01.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    doc_currcy Nullable(String) COMMENT 'BW: ZSD_A01.0DOC_CURRCY (CUKY 5). Валюта документа',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_A01.0INV_QTY (QUAN 17,3). Фактурированное количество',
    sales_unit Nullable(String) COMMENT 'BW: ZSD_A01.0SALES_UNIT (UNIT 3). Единица продажи'
)
ENGINE = MergeTree
ORDER BY (doc_number, s_ord_item)
COMMENT 'BW: ADSO ZSD_A01 (standard), активные данные, таблица BW /BIC/AZSD_A012. Продажи: позиции документов';

CREATE TABLE IF NOT EXISTS dbt_dwh.zsd_a01_changelog
(
    load_id    Int64 COMMENT 'Служебная: номер загрузки (meta.load_log)',
    record_no  Int64 COMMENT 'Служебная: номер записи в загрузке',
    recordmode String COMMENT 'Служебная: N новая запись, X before image, \' \' after image',
    doc_number String COMMENT 'BW: ZSD_A01.0DOC_NUMBER (CHAR 10). Документ сбыта',
    s_ord_item String COMMENT 'BW: ZSD_A01.0S_ORD_ITEM (NUMC 6). Позиция документа сбыта',
    sold_to    Nullable(String) COMMENT 'BW: ZSD_A01.0SOLD_TO (CHAR 10). Заказчик',
    material   Nullable(String) COMMENT 'BW: ZSD_A01.0MATERIAL (CHAR 40). Материал',
    distr_chan Nullable(String) COMMENT 'BW: ZSD_A01.0DISTR_CHAN (CHAR 2). Канал сбыта',
    calday     Nullable(Date32) COMMENT 'BW: ZSD_A01.0CALDAY (DATS 8). Календарный день',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A01.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    doc_currcy Nullable(String) COMMENT 'BW: ZSD_A01.0DOC_CURRCY (CUKY 5). Валюта документа',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_A01.0INV_QTY (QUAN 17,3). Фактурированное количество',
    sales_unit Nullable(String) COMMENT 'BW: ZSD_A01.0SALES_UNIT (UNIT 3). Единица продажи'
)
ENGINE = MergeTree
ORDER BY (load_id, record_no)
COMMENT 'BW: ADSO ZSD_A01 (standard), журнал изменений, таблица BW /BIC/AZSD_A013. Продажи: позиции документов';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a02_inbound
(
    load_id    Int64 COMMENT 'Служебная: номер загрузки (meta.load_log)',
    record_no  Int64 COMMENT 'Служебная: номер записи в загрузке',
    recordmode String COMMENT 'Служебная: N новая запись, X before image, \' \' after image',
    sold_to    String COMMENT 'BW: ZSD_A02.0SOLD_TO (CHAR 10). Заказчик',
    calmonth   String COMMENT 'BW: ZSD_A02.0CALMONTH (NUMC 6). Календарный год/месяц',
    doc_currcy String COMMENT 'BW: ZSD_A02.0DOC_CURRCY (CUKY 5). Валюта документа',
    sales_unit String COMMENT 'BW: ZSD_A02.0SALES_UNIT (UNIT 3). Единица продажи',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A02.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_A02.0INV_QTY (QUAN 17,3). Фактурированное количество'
)
ENGINE = MergeTree
ORDER BY (load_id, record_no)
COMMENT 'BW: ADSO ZSD_A02 (cumulative), входящая таблица, таблица BW /BIC/AZSD_A021. Продажи по клиентам и месяцам';

CREATE TABLE IF NOT EXISTS dbt_mart.zsd_a02_active
(
    sold_to    String COMMENT 'BW: ZSD_A02.0SOLD_TO (CHAR 10). Заказчик',
    calmonth   String COMMENT 'BW: ZSD_A02.0CALMONTH (NUMC 6). Календарный год/месяц',
    doc_currcy String COMMENT 'BW: ZSD_A02.0DOC_CURRCY (CUKY 5). Валюта документа',
    sales_unit String COMMENT 'BW: ZSD_A02.0SALES_UNIT (UNIT 3). Единица продажи',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A02.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_A02.0INV_QTY (QUAN 17,3). Фактурированное количество'
)
ENGINE = MergeTree
ORDER BY (sold_to, calmonth, doc_currcy, sales_unit)
COMMENT 'BW: ADSO ZSD_A02 (cumulative), активные данные, таблица BW /BIC/AZSD_A022. Продажи по клиентам и месяцам';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a03_inbound
(
    load_id    Int64 COMMENT 'Служебная: номер загрузки (meta.load_log)',
    record_no  Int64 COMMENT 'Служебная: номер записи в загрузке',
    recordmode String COMMENT 'Служебная: N новая запись, X before image, \' \' after image',
    sold_to    String COMMENT 'BW: ZSD_A03.0SOLD_TO (CHAR 10). Заказчик',
    material   String COMMENT 'BW: ZSD_A03.0MATERIAL (CHAR 40). Материал',
    calday     Date32 COMMENT 'BW: ZSD_A03.0CALDAY (DATS 8). Календарный день',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A03.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    doc_currcy Nullable(String) COMMENT 'BW: ZSD_A03.0DOC_CURRCY (CUKY 5). Валюта документа'
)
ENGINE = MergeTree
ORDER BY (load_id, record_no)
COMMENT 'BW: ADSO ZSD_A03 (standard), входящая таблица, таблица BW /BIC/AZSD_A031. Продажи по материалам (с рутиной)';

CREATE TABLE IF NOT EXISTS dbt_dwh.zsd_a03_active
(
    sold_to    String COMMENT 'BW: ZSD_A03.0SOLD_TO (CHAR 10). Заказчик',
    material   String COMMENT 'BW: ZSD_A03.0MATERIAL (CHAR 40). Материал',
    calday     Date32 COMMENT 'BW: ZSD_A03.0CALDAY (DATS 8). Календарный день',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A03.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    doc_currcy Nullable(String) COMMENT 'BW: ZSD_A03.0DOC_CURRCY (CUKY 5). Валюта документа'
)
ENGINE = MergeTree
ORDER BY (sold_to, material, calday)
COMMENT 'BW: ADSO ZSD_A03 (standard), активные данные, таблица BW /BIC/AZSD_A032. Продажи по материалам (с рутиной)';

CREATE TABLE IF NOT EXISTS dbt_dwh.zsd_a03_changelog
(
    load_id    Int64 COMMENT 'Служебная: номер загрузки (meta.load_log)',
    record_no  Int64 COMMENT 'Служебная: номер записи в загрузке',
    recordmode String COMMENT 'Служебная: N новая запись, X before image, \' \' after image',
    sold_to    String COMMENT 'BW: ZSD_A03.0SOLD_TO (CHAR 10). Заказчик',
    material   String COMMENT 'BW: ZSD_A03.0MATERIAL (CHAR 40). Материал',
    calday     Date32 COMMENT 'BW: ZSD_A03.0CALDAY (DATS 8). Календарный день',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A03.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    doc_currcy Nullable(String) COMMENT 'BW: ZSD_A03.0DOC_CURRCY (CUKY 5). Валюта документа'
)
ENGINE = MergeTree
ORDER BY (load_id, record_no)
COMMENT 'BW: ADSO ZSD_A03 (standard), журнал изменений, таблица BW /BIC/AZSD_A033. Продажи по материалам (с рутиной)';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a04_inbound
(
    load_id    Int64 COMMENT 'Служебная: номер загрузки (meta.load_log)',
    record_no  Int64 COMMENT 'Служебная: номер записи в загрузке',
    recordmode String COMMENT 'Служебная: N новая запись, X before image, \' \' after image',
    matl_group String COMMENT 'BW: ZSD_A04.0MATL_GROUP (CHAR 9). Группа материалов',
    calmonth   String COMMENT 'BW: ZSD_A04.0CALMONTH (NUMC 6). Календарный год/месяц',
    doc_currcy String COMMENT 'BW: ZSD_A04.0DOC_CURRCY (CUKY 5). Валюта документа',
    sales_unit String COMMENT 'BW: ZSD_A04.0SALES_UNIT (UNIT 3). Единица продажи',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A04.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_A04.0INV_QTY (QUAN 17,3). Фактурированное количество'
)
ENGINE = MergeTree
ORDER BY (load_id, record_no)
COMMENT 'BW: ADSO ZSD_A04 (cumulative), входящая таблица, таблица BW /BIC/AZSD_A041. Продажи по группам материалов';

CREATE TABLE IF NOT EXISTS dbt_mart.zsd_a04_active
(
    matl_group String COMMENT 'BW: ZSD_A04.0MATL_GROUP (CHAR 9). Группа материалов',
    calmonth   String COMMENT 'BW: ZSD_A04.0CALMONTH (NUMC 6). Календарный год/месяц',
    doc_currcy String COMMENT 'BW: ZSD_A04.0DOC_CURRCY (CUKY 5). Валюта документа',
    sales_unit String COMMENT 'BW: ZSD_A04.0SALES_UNIT (UNIT 3). Единица продажи',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A04.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_A04.0INV_QTY (QUAN 17,3). Фактурированное количество'
)
ENGINE = MergeTree
ORDER BY (matl_group, calmonth, doc_currcy, sales_unit)
COMMENT 'BW: ADSO ZSD_A04 (cumulative), активные данные, таблица BW /BIC/AZSD_A042. Продажи по группам материалов';

CREATE TABLE IF NOT EXISTS dbt_mart.zsd_d01_active
(
    sold_to    String COMMENT 'BW: ZSD_D01.0SOLD_TO (CHAR 10). Заказчик',
    calmonth   String COMMENT 'BW: ZSD_D01.0CALMONTH (NUMC 6). Календарный год/месяц',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_D01.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    doc_currcy Nullable(String) COMMENT 'BW: ZSD_D01.0DOC_CURRCY (CUKY 5). Валюта документа'
)
ENGINE = MergeTree
ORDER BY (sold_to, calmonth)
COMMENT 'BW: ADSO ZSD_D01 (direct update), активные данные, таблица BW /BIC/AZSD_D012. План продаж';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_s01_inbound
(
    load_id    Int64 COMMENT 'Служебная: номер загрузки (meta.load_log)',
    record_no  Int64 COMMENT 'Служебная: номер записи в загрузке',
    recordmode String COMMENT 'Служебная: N новая запись, X before image, \' \' after image',
    doc_number Nullable(String) COMMENT 'BW: ZSD_S01.0DOC_NUMBER (CHAR 10). Документ сбыта',
    s_ord_item Nullable(String) COMMENT 'BW: ZSD_S01.0S_ORD_ITEM (NUMC 6). Позиция документа сбыта',
    material   Nullable(String) COMMENT 'BW: ZSD_S01.0MATERIAL (CHAR 40). Материал',
    matl_group Nullable(String) COMMENT 'BW: ZSD_S01.0MATL_GROUP (CHAR 9). Группа материалов',
    calday     Nullable(Date32) COMMENT 'BW: ZSD_S01.0CALDAY (DATS 8). Календарный день',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_S01.0INV_QTY (QUAN 17,3). Фактурированное количество',
    sales_unit Nullable(String) COMMENT 'BW: ZSD_S01.0SALES_UNIT (UNIT 3). Единица продажи'
)
ENGINE = MergeTree
ORDER BY (load_id, record_no)
COMMENT 'BW: ADSO ZSD_S01 (staging), входящая таблица, таблица BW /BIC/AZSD_S011. Продажи: промежуточный слой';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a02_work
(
    _hit       Int16 COMMENT 'Служебная: 1 — ключ уже был в активных данных, 0 — новый',
    sold_to    String COMMENT 'BW: ZSD_A02.0SOLD_TO (CHAR 10). Заказчик',
    calmonth   String COMMENT 'BW: ZSD_A02.0CALMONTH (NUMC 6). Календарный год/месяц',
    doc_currcy String COMMENT 'BW: ZSD_A02.0DOC_CURRCY (CUKY 5). Валюта документа',
    sales_unit String COMMENT 'BW: ZSD_A02.0SALES_UNIT (UNIT 3). Единица продажи',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A02.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_A02.0INV_QTY (QUAN 17,3). Фактурированное количество'
)
ENGINE = MergeTree
ORDER BY (sold_to, calmonth, doc_currcy, sales_unit)
COMMENT 'Рабочая таблица активации ADSO ZSD_A02 (cumulative), заполняется при прогоне';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a04_work
(
    _hit       Int16 COMMENT 'Служебная: 1 — ключ уже был в активных данных, 0 — новый',
    matl_group String COMMENT 'BW: ZSD_A04.0MATL_GROUP (CHAR 9). Группа материалов',
    calmonth   String COMMENT 'BW: ZSD_A04.0CALMONTH (NUMC 6). Календарный год/месяц',
    doc_currcy String COMMENT 'BW: ZSD_A04.0DOC_CURRCY (CUKY 5). Валюта документа',
    sales_unit String COMMENT 'BW: ZSD_A04.0SALES_UNIT (UNIT 3). Единица продажи',
    netval_inv Nullable(Decimal(17,2)) COMMENT 'BW: ZSD_A04.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры',
    inv_qty    Nullable(Decimal(17,3)) COMMENT 'BW: ZSD_A04.0INV_QTY (QUAN 17,3). Фактурированное количество'
)
ENGINE = MergeTree
ORDER BY (matl_group, calmonth, doc_currcy, sales_unit)
COMMENT 'Рабочая таблица активации ADSO ZSD_A04 (cumulative), заполняется при прогоне';
