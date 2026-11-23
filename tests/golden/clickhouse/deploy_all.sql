-- DDL одним файлом: clickhouse-client --multiquery --queries-file deploy_all.sql
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект clickhouse.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

-- ===== 010_schemas.sql =====


CREATE DATABASE IF NOT EXISTS dbt_stg;

CREATE DATABASE IF NOT EXISTS dbt_md;

CREATE DATABASE IF NOT EXISTS dbt_dwh;

CREATE DATABASE IF NOT EXISTS dbt_mart;

CREATE DATABASE IF NOT EXISTS dbt_meta;

-- ===== 020_meta.sql =====


-- В ClickHouse нет последовательностей: номера загрузок, развёртываний и сверок (load_id_seq, deploy_id_seq, reconcile_run_seq
-- в Greengage) выдаёт приложение как max(номер) + 1 по служебной таблице. Прогоны не должны идти параллельно.
CREATE TABLE IF NOT EXISTS dbt_meta.load_log
(
    load_id      Int64 COMMENT 'Номер загрузки (последовательность load_id_seq)',
    object_name  String COMMENT 'Объект BW, в который загружен файл',
    file_name    Nullable(String) COMMENT 'Имя загруженного файла',
    file_hash    Nullable(String) COMMENT 'SHA-256 файла',
    rows_loaded  Nullable(Int64) COMMENT 'Число загруженных строк',
    status       String COMMENT 'loaded, activated или failed',
    loaded_by    Nullable(String) COMMENT 'Пользователь ОС',
    loaded_at    Nullable(DateTime64(6)) COMMENT 'Время загрузки',
    activated_at Nullable(DateTime64(6)) COMMENT 'Время активации',
    message      Nullable(String) COMMENT 'Итоги шагов прогона или текст ошибки'
)
ENGINE = MergeTree
ORDER BY load_id
COMMENT 'Журнал загрузок (ТЗ, 4.3.6)';

CREATE TABLE IF NOT EXISTS dbt_meta.deploy_log
(
    deploy_id   Int64 COMMENT 'Номер развёртывания (последовательность deploy_id_seq)',
    model_hash  Nullable(String) COMMENT 'Хеш модели',
    object_name Nullable(String) COMMENT 'Объект базы',
    action      Nullable(String) COMMENT 'create, recreate, skip',
    status      Nullable(String) COMMENT 'ok или error',
    message     Nullable(String) COMMENT 'Текст ошибки',
    deployed_by Nullable(String) COMMENT 'Пользователь ОС',
    deployed_at Nullable(DateTime64(6)) COMMENT 'Время развёртывания'
)
ENGINE = MergeTree
ORDER BY deploy_id
COMMENT 'Журнал развёртываний (ТЗ, 4.3.6)';

CREATE TABLE IF NOT EXISTS dbt_meta.reconcile_result
(
    run_id      Int64 COMMENT 'Номер сверки (последовательность reconcile_run_seq)',
    object_name Nullable(String) COMMENT 'Сверяемый объект BW',
    etalon_file Nullable(String) COMMENT 'Файл эталона',
    key_values  Nullable(String) COMMENT 'Значения ключа строки',
    measure     Nullable(String) COMMENT 'Показатель',
    value_bw    Nullable(Decimal(38,10)) COMMENT 'Значение в эталоне',
    value_adb   Nullable(Decimal(38,10)) COMMENT 'Значение в базе',
    diff        Nullable(Decimal(38,10)) COMMENT 'Разница',
    status      Nullable(String) COMMENT 'совпало, расхождение, нет в ADB, нет в эталоне',
    run_at      Nullable(DateTime64(6)) COMMENT 'Время сверки'
)
ENGINE = MergeTree
ORDER BY run_id
COMMENT 'Результаты сверок с эталоном (ТЗ, 4.3.6)';

-- ===== 030_md.sql =====


CREATE TABLE IF NOT EXISTS dbt_md.country_text
(
    country String COMMENT 'BW: 0COUNTRY.0COUNTRY (CHAR 3). Страна',
    langu   String COMMENT 'Язык текста (R русский, E английский)',
    txtsh   Nullable(String) COMMENT 'Краткий текст',
    txtmd   Nullable(String) COMMENT 'Средний текст',
    txtlg   Nullable(String) COMMENT 'Длинный текст'
)
ENGINE = ReplacingMergeTree
ORDER BY (country, langu)
COMMENT 'BW: тексты признака 0COUNTRY. Страна';

CREATE TABLE IF NOT EXISTS dbt_md.material_attr
(
    material   String COMMENT 'BW: 0MATERIAL.0MATERIAL (CHAR 40). Материал',
    matl_group Nullable(String) COMMENT 'BW: атрибут 0MATL_GROUP признака 0MATERIAL. Группа материалов'
)
ENGINE = ReplacingMergeTree
ORDER BY material
COMMENT 'BW: атрибуты признака 0MATERIAL. Материал';

CREATE TABLE IF NOT EXISTS dbt_md.material_text
(
    material String COMMENT 'BW: 0MATERIAL.0MATERIAL (CHAR 40). Материал',
    langu    String COMMENT 'Язык текста (R русский, E английский)',
    txtsh    Nullable(String) COMMENT 'Краткий текст',
    txtmd    Nullable(String) COMMENT 'Средний текст',
    txtlg    Nullable(String) COMMENT 'Длинный текст'
)
ENGINE = ReplacingMergeTree
ORDER BY (material, langu)
COMMENT 'BW: тексты признака 0MATERIAL. Материал';

CREATE TABLE IF NOT EXISTS dbt_md.matl_group_text
(
    matl_group String COMMENT 'BW: 0MATL_GROUP.0MATL_GROUP (CHAR 9). Группа материалов',
    langu      String COMMENT 'Язык текста (R русский, E английский)',
    txtsh      Nullable(String) COMMENT 'Краткий текст',
    txtmd      Nullable(String) COMMENT 'Средний текст',
    txtlg      Nullable(String) COMMENT 'Длинный текст'
)
ENGINE = ReplacingMergeTree
ORDER BY (matl_group, langu)
COMMENT 'BW: тексты признака 0MATL_GROUP. Группа материалов';

CREATE TABLE IF NOT EXISTS dbt_md.sold_to_attr
(
    sold_to String COMMENT 'BW: 0SOLD_TO.0SOLD_TO (CHAR 10). Заказчик',
    country Nullable(String) COMMENT 'BW: атрибут 0COUNTRY признака 0SOLD_TO. Страна'
)
ENGINE = ReplacingMergeTree
ORDER BY sold_to
COMMENT 'BW: атрибуты признака 0SOLD_TO. Заказчик';

CREATE TABLE IF NOT EXISTS dbt_md.sold_to_text
(
    sold_to String COMMENT 'BW: 0SOLD_TO.0SOLD_TO (CHAR 10). Заказчик',
    langu   String COMMENT 'Язык текста (R русский, E английский)',
    txtsh   Nullable(String) COMMENT 'Краткий текст',
    txtmd   Nullable(String) COMMENT 'Средний текст',
    txtlg   Nullable(String) COMMENT 'Длинный текст'
)
ENGINE = ReplacingMergeTree
ORDER BY (sold_to, langu)
COMMENT 'BW: тексты признака 0SOLD_TO. Заказчик';

-- ===== 040_datasources.sql =====


CREATE TABLE IF NOT EXISTS dbt_stg.zsales_ff
(
    load_id    Int64 COMMENT 'Служебная: номер загрузки (meta.load_log)',
    record_no  Int64 COMMENT 'Служебная: номер записи в загрузке',
    doc_number Nullable(String) COMMENT 'BW: ZSALES_FF.DOC_NUMBER (CHAR 10). Документ сбыта',
    s_ord_item Nullable(String) COMMENT 'BW: ZSALES_FF.S_ORD_ITEM (NUMC 6). Позиция документа сбыта',
    sold_to    Nullable(String) COMMENT 'BW: ZSALES_FF.SOLD_TO (CHAR 10). Заказчик',
    material   Nullable(String) COMMENT 'BW: ZSALES_FF.MATERIAL (CHAR 40). Материал',
    calday     Nullable(Date32) COMMENT 'BW: ZSALES_FF.CALDAY (DATS 8). Календарный день',
    netval     Nullable(Decimal(17,2)) COMMENT 'BW: ZSALES_FF.NETVAL (CURR 17,2)',
    discount   Nullable(Decimal(17,2)) COMMENT 'BW: ZSALES_FF.DISCOUNT (CURR 17,2)',
    currency   Nullable(String) COMMENT 'BW: ZSALES_FF.CURRENCY (CUKY 5). Валюта документа',
    qty        Nullable(Decimal(17,3)) COMMENT 'BW: ZSALES_FF.QTY (QUAN 17,3). Фактурированное количество',
    unit       Nullable(String) COMMENT 'BW: ZSALES_FF.UNIT (UNIT 3). Единица продажи'
)
ENGINE = MergeTree
ORDER BY (load_id, record_no)
COMMENT 'BW: DataSource ZSALES_FF (плоский файл, система FLATFILE), входная таблица. Продажи из файла';

-- ===== 050_adso.sql =====


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
