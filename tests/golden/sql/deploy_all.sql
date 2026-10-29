-- Весь пакет одним файлом: psql -f deploy_all.sql
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект greenplum.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.
\set ON_ERROR_STOP on

-- ===== 010_schemas.sql =====


CREATE SCHEMA IF NOT EXISTS dbt_stg;

CREATE SCHEMA IF NOT EXISTS dbt_md;

CREATE SCHEMA IF NOT EXISTS dbt_dwh;

CREATE SCHEMA IF NOT EXISTS dbt_mart;

CREATE SCHEMA IF NOT EXISTS dbt_meta;

-- ===== 020_meta.sql =====


-- Последовательности номеров: загрузки, развёртывания, сверки. В Greengage 6 нет CREATE SEQUENCE IF NOT EXISTS.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'dbt_meta' AND c.relname = 'load_id_seq') THEN
        CREATE SEQUENCE dbt_meta.load_id_seq;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'dbt_meta' AND c.relname = 'deploy_id_seq') THEN
        CREATE SEQUENCE dbt_meta.deploy_id_seq;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'dbt_meta' AND c.relname = 'reconcile_run_seq') THEN
        CREATE SEQUENCE dbt_meta.reconcile_run_seq;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS dbt_meta.load_log (
    load_id      bigint NOT NULL,
    object_name  varchar(60) NOT NULL,
    file_name    text,
    file_hash    varchar(64),
    rows_loaded  bigint,
    status       varchar(20) NOT NULL,
    loaded_by    varchar(128),
    loaded_at    timestamp,
    activated_at timestamp,
    message      text,
    PRIMARY KEY (load_id)
)
DISTRIBUTED BY (load_id);
COMMENT ON TABLE dbt_meta.load_log IS 'Журнал загрузок (ТЗ, 4.3.6)';
COMMENT ON COLUMN dbt_meta.load_log.load_id IS 'Номер загрузки (последовательность load_id_seq)';
COMMENT ON COLUMN dbt_meta.load_log.object_name IS 'Объект BW, в который загружен файл';
COMMENT ON COLUMN dbt_meta.load_log.file_name IS 'Имя загруженного файла';
COMMENT ON COLUMN dbt_meta.load_log.file_hash IS 'SHA-256 файла';
COMMENT ON COLUMN dbt_meta.load_log.rows_loaded IS 'Число загруженных строк';
COMMENT ON COLUMN dbt_meta.load_log.status IS 'loaded, activated или failed';
COMMENT ON COLUMN dbt_meta.load_log.loaded_by IS 'Пользователь ОС';
COMMENT ON COLUMN dbt_meta.load_log.loaded_at IS 'Время загрузки';
COMMENT ON COLUMN dbt_meta.load_log.activated_at IS 'Время активации';
COMMENT ON COLUMN dbt_meta.load_log.message IS 'Итоги шагов прогона или текст ошибки';

CREATE TABLE IF NOT EXISTS dbt_meta.deploy_log (
    deploy_id   bigint NOT NULL,
    model_hash  varchar(64),
    object_name text,
    action      varchar(20),
    status      varchar(20),
    message     text,
    deployed_by varchar(128),
    deployed_at timestamp
)
DISTRIBUTED BY (deploy_id);
COMMENT ON TABLE dbt_meta.deploy_log IS 'Журнал развёртываний (ТЗ, 4.3.6)';
COMMENT ON COLUMN dbt_meta.deploy_log.deploy_id IS 'Номер развёртывания (последовательность deploy_id_seq)';
COMMENT ON COLUMN dbt_meta.deploy_log.model_hash IS 'Хеш модели';
COMMENT ON COLUMN dbt_meta.deploy_log.object_name IS 'Объект базы';
COMMENT ON COLUMN dbt_meta.deploy_log.action IS 'create, recreate, skip';
COMMENT ON COLUMN dbt_meta.deploy_log.status IS 'ok или error';
COMMENT ON COLUMN dbt_meta.deploy_log.message IS 'Текст ошибки';
COMMENT ON COLUMN dbt_meta.deploy_log.deployed_by IS 'Пользователь ОС';
COMMENT ON COLUMN dbt_meta.deploy_log.deployed_at IS 'Время развёртывания';

CREATE TABLE IF NOT EXISTS dbt_meta.reconcile_result (
    run_id      bigint NOT NULL,
    object_name varchar(60),
    etalon_file text,
    key_values  jsonb,
    measure     varchar(60),
    value_bw    numeric,
    value_adb   numeric,
    diff        numeric,
    status      varchar(30),
    run_at      timestamp
)
DISTRIBUTED BY (run_id);
COMMENT ON TABLE dbt_meta.reconcile_result IS 'Результаты сверок с эталоном (ТЗ, 4.3.6)';
COMMENT ON COLUMN dbt_meta.reconcile_result.run_id IS 'Номер сверки (последовательность reconcile_run_seq)';
COMMENT ON COLUMN dbt_meta.reconcile_result.object_name IS 'Сверяемый объект BW';
COMMENT ON COLUMN dbt_meta.reconcile_result.etalon_file IS 'Файл эталона';
COMMENT ON COLUMN dbt_meta.reconcile_result.key_values IS 'Значения ключа строки';
COMMENT ON COLUMN dbt_meta.reconcile_result.measure IS 'Показатель';
COMMENT ON COLUMN dbt_meta.reconcile_result.value_bw IS 'Значение в эталоне';
COMMENT ON COLUMN dbt_meta.reconcile_result.value_adb IS 'Значение в базе';
COMMENT ON COLUMN dbt_meta.reconcile_result.diff IS 'Разница';
COMMENT ON COLUMN dbt_meta.reconcile_result.status IS 'совпало, расхождение, нет в ADB, нет в эталоне';
COMMENT ON COLUMN dbt_meta.reconcile_result.run_at IS 'Время сверки';

-- ===== 030_md.sql =====


CREATE TABLE IF NOT EXISTS dbt_md.country_text (
    country varchar(3) NOT NULL,
    langu   char(1) NOT NULL,
    txtsh   varchar(20),
    txtmd   varchar(40),
    txtlg   varchar(60),
    PRIMARY KEY (country, langu)
)
DISTRIBUTED BY (country);
COMMENT ON TABLE dbt_md.country_text IS 'BW: тексты признака 0COUNTRY. Страна';
COMMENT ON COLUMN dbt_md.country_text.country IS 'BW: 0COUNTRY.0COUNTRY (CHAR 3). Страна';
COMMENT ON COLUMN dbt_md.country_text.langu IS 'Язык текста (R русский, E английский)';
COMMENT ON COLUMN dbt_md.country_text.txtsh IS 'Краткий текст';
COMMENT ON COLUMN dbt_md.country_text.txtmd IS 'Средний текст';
COMMENT ON COLUMN dbt_md.country_text.txtlg IS 'Длинный текст';

CREATE TABLE IF NOT EXISTS dbt_md.material_attr (
    material   varchar(40) NOT NULL,
    matl_group varchar(9),
    PRIMARY KEY (material)
)
DISTRIBUTED BY (material);
COMMENT ON TABLE dbt_md.material_attr IS 'BW: атрибуты признака 0MATERIAL. Материал';
COMMENT ON COLUMN dbt_md.material_attr.material IS 'BW: 0MATERIAL.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_md.material_attr.matl_group IS 'BW: атрибут 0MATL_GROUP признака 0MATERIAL. Группа материалов';

CREATE TABLE IF NOT EXISTS dbt_md.material_text (
    material varchar(40) NOT NULL,
    langu    char(1) NOT NULL,
    txtsh    varchar(20),
    txtmd    varchar(40),
    txtlg    varchar(60),
    PRIMARY KEY (material, langu)
)
DISTRIBUTED BY (material);
COMMENT ON TABLE dbt_md.material_text IS 'BW: тексты признака 0MATERIAL. Материал';
COMMENT ON COLUMN dbt_md.material_text.material IS 'BW: 0MATERIAL.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_md.material_text.langu IS 'Язык текста (R русский, E английский)';
COMMENT ON COLUMN dbt_md.material_text.txtsh IS 'Краткий текст';
COMMENT ON COLUMN dbt_md.material_text.txtmd IS 'Средний текст';
COMMENT ON COLUMN dbt_md.material_text.txtlg IS 'Длинный текст';

CREATE TABLE IF NOT EXISTS dbt_md.matl_group_text (
    matl_group varchar(9) NOT NULL,
    langu      char(1) NOT NULL,
    txtsh      varchar(20),
    txtmd      varchar(40),
    txtlg      varchar(60),
    PRIMARY KEY (matl_group, langu)
)
DISTRIBUTED BY (matl_group);
COMMENT ON TABLE dbt_md.matl_group_text IS 'BW: тексты признака 0MATL_GROUP. Группа материалов';
COMMENT ON COLUMN dbt_md.matl_group_text.matl_group IS 'BW: 0MATL_GROUP.0MATL_GROUP (CHAR 9). Группа материалов';
COMMENT ON COLUMN dbt_md.matl_group_text.langu IS 'Язык текста (R русский, E английский)';
COMMENT ON COLUMN dbt_md.matl_group_text.txtsh IS 'Краткий текст';
COMMENT ON COLUMN dbt_md.matl_group_text.txtmd IS 'Средний текст';
COMMENT ON COLUMN dbt_md.matl_group_text.txtlg IS 'Длинный текст';

CREATE TABLE IF NOT EXISTS dbt_md.sold_to_attr (
    sold_to varchar(10) NOT NULL,
    country varchar(3),
    PRIMARY KEY (sold_to)
)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_md.sold_to_attr IS 'BW: атрибуты признака 0SOLD_TO. Заказчик';
COMMENT ON COLUMN dbt_md.sold_to_attr.sold_to IS 'BW: 0SOLD_TO.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_md.sold_to_attr.country IS 'BW: атрибут 0COUNTRY признака 0SOLD_TO. Страна';

CREATE TABLE IF NOT EXISTS dbt_md.sold_to_text (
    sold_to varchar(10) NOT NULL,
    langu   char(1) NOT NULL,
    txtsh   varchar(20),
    txtmd   varchar(40),
    txtlg   varchar(60),
    PRIMARY KEY (sold_to, langu)
)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_md.sold_to_text IS 'BW: тексты признака 0SOLD_TO. Заказчик';
COMMENT ON COLUMN dbt_md.sold_to_text.sold_to IS 'BW: 0SOLD_TO.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_md.sold_to_text.langu IS 'Язык текста (R русский, E английский)';
COMMENT ON COLUMN dbt_md.sold_to_text.txtsh IS 'Краткий текст';
COMMENT ON COLUMN dbt_md.sold_to_text.txtmd IS 'Средний текст';
COMMENT ON COLUMN dbt_md.sold_to_text.txtlg IS 'Длинный текст';

-- ===== 040_datasources.sql =====


CREATE TABLE IF NOT EXISTS dbt_stg.zsales_ff (
    load_id    bigint NOT NULL,
    record_no  bigint NOT NULL,
    doc_number varchar(10),
    s_ord_item varchar(6),
    sold_to    varchar(10),
    material   varchar(40),
    calday     date,
    netval     numeric(17,2),
    discount   numeric(17,2),
    currency   varchar(5),
    qty        numeric(17,3),
    unit       varchar(3)
)
WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)
DISTRIBUTED RANDOMLY;
COMMENT ON TABLE dbt_stg.zsales_ff IS 'BW: DataSource ZSALES_FF (плоский файл, система FLATFILE), входная таблица. Продажи из файла';
COMMENT ON COLUMN dbt_stg.zsales_ff.load_id IS 'Служебная: номер загрузки (meta.load_log)';
COMMENT ON COLUMN dbt_stg.zsales_ff.record_no IS 'Служебная: номер записи в загрузке';
COMMENT ON COLUMN dbt_stg.zsales_ff.doc_number IS 'BW: ZSALES_FF.DOC_NUMBER (CHAR 10). Документ сбыта';
COMMENT ON COLUMN dbt_stg.zsales_ff.s_ord_item IS 'BW: ZSALES_FF.S_ORD_ITEM (NUMC 6). Позиция документа сбыта';
COMMENT ON COLUMN dbt_stg.zsales_ff.sold_to IS 'BW: ZSALES_FF.SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_stg.zsales_ff.material IS 'BW: ZSALES_FF.MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_stg.zsales_ff.calday IS 'BW: ZSALES_FF.CALDAY (DATS 8). Календарный день';
COMMENT ON COLUMN dbt_stg.zsales_ff.netval IS 'BW: ZSALES_FF.NETVAL (CURR 17,2)';
COMMENT ON COLUMN dbt_stg.zsales_ff.discount IS 'BW: ZSALES_FF.DISCOUNT (CURR 17,2)';
COMMENT ON COLUMN dbt_stg.zsales_ff.currency IS 'BW: ZSALES_FF.CURRENCY (CUKY 5). Валюта документа';
COMMENT ON COLUMN dbt_stg.zsales_ff.qty IS 'BW: ZSALES_FF.QTY (QUAN 17,3). Фактурированное количество';
COMMENT ON COLUMN dbt_stg.zsales_ff.unit IS 'BW: ZSALES_FF.UNIT (UNIT 3). Единица продажи';

-- ===== 050_adso.sql =====


CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a01_inbound (
    load_id    bigint NOT NULL,
    record_no  bigint NOT NULL,
    recordmode char(1) NOT NULL,
    doc_number varchar(10) NOT NULL,
    s_ord_item varchar(6) NOT NULL,
    sold_to    varchar(10),
    material   varchar(40),
    distr_chan varchar(2),
    calday     date,
    netval_inv numeric(17,2),
    doc_currcy varchar(5),
    inv_qty    numeric(17,3),
    sales_unit varchar(3)
)
WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)
DISTRIBUTED BY (doc_number);
COMMENT ON TABLE dbt_stg.zsd_a01_inbound IS 'BW: ADSO ZSD_A01 (standard), входящая таблица, таблица BW /BIC/AZSD_A011. Продажи: позиции документов';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.load_id IS 'Служебная: номер загрузки (meta.load_log)';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.record_no IS 'Служебная: номер записи в загрузке';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.recordmode IS 'Служебная: N новая запись, X before image, '' '' after image';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.doc_number IS 'BW: ZSD_A01.0DOC_NUMBER (CHAR 10). Документ сбыта';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.s_ord_item IS 'BW: ZSD_A01.0S_ORD_ITEM (NUMC 6). Позиция документа сбыта';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.sold_to IS 'BW: ZSD_A01.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.material IS 'BW: ZSD_A01.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.distr_chan IS 'BW: ZSD_A01.0DISTR_CHAN (CHAR 2). Канал сбыта';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.calday IS 'BW: ZSD_A01.0CALDAY (DATS 8). Календарный день';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.netval_inv IS 'BW: ZSD_A01.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.doc_currcy IS 'BW: ZSD_A01.0DOC_CURRCY (CUKY 5). Валюта документа';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.inv_qty IS 'BW: ZSD_A01.0INV_QTY (QUAN 17,3). Фактурированное количество';
COMMENT ON COLUMN dbt_stg.zsd_a01_inbound.sales_unit IS 'BW: ZSD_A01.0SALES_UNIT (UNIT 3). Единица продажи';

CREATE TABLE IF NOT EXISTS dbt_dwh.zsd_a01_active (
    doc_number varchar(10) NOT NULL,
    s_ord_item varchar(6) NOT NULL,
    sold_to    varchar(10),
    material   varchar(40),
    distr_chan varchar(2),
    calday     date,
    netval_inv numeric(17,2),
    doc_currcy varchar(5),
    inv_qty    numeric(17,3),
    sales_unit varchar(3),
    PRIMARY KEY (doc_number, s_ord_item)
)
DISTRIBUTED BY (doc_number);
COMMENT ON TABLE dbt_dwh.zsd_a01_active IS 'BW: ADSO ZSD_A01 (standard), активные данные, таблица BW /BIC/AZSD_A012. Продажи: позиции документов';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.doc_number IS 'BW: ZSD_A01.0DOC_NUMBER (CHAR 10). Документ сбыта';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.s_ord_item IS 'BW: ZSD_A01.0S_ORD_ITEM (NUMC 6). Позиция документа сбыта';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.sold_to IS 'BW: ZSD_A01.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.material IS 'BW: ZSD_A01.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.distr_chan IS 'BW: ZSD_A01.0DISTR_CHAN (CHAR 2). Канал сбыта';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.calday IS 'BW: ZSD_A01.0CALDAY (DATS 8). Календарный день';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.netval_inv IS 'BW: ZSD_A01.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.doc_currcy IS 'BW: ZSD_A01.0DOC_CURRCY (CUKY 5). Валюта документа';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.inv_qty IS 'BW: ZSD_A01.0INV_QTY (QUAN 17,3). Фактурированное количество';
COMMENT ON COLUMN dbt_dwh.zsd_a01_active.sales_unit IS 'BW: ZSD_A01.0SALES_UNIT (UNIT 3). Единица продажи';

CREATE TABLE IF NOT EXISTS dbt_dwh.zsd_a01_changelog (
    load_id    bigint NOT NULL,
    record_no  bigint NOT NULL,
    recordmode char(1) NOT NULL,
    doc_number varchar(10) NOT NULL,
    s_ord_item varchar(6) NOT NULL,
    sold_to    varchar(10),
    material   varchar(40),
    distr_chan varchar(2),
    calday     date,
    netval_inv numeric(17,2),
    doc_currcy varchar(5),
    inv_qty    numeric(17,3),
    sales_unit varchar(3)
)
WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)
DISTRIBUTED BY (doc_number);
COMMENT ON TABLE dbt_dwh.zsd_a01_changelog IS 'BW: ADSO ZSD_A01 (standard), журнал изменений, таблица BW /BIC/AZSD_A013. Продажи: позиции документов';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.load_id IS 'Служебная: номер загрузки (meta.load_log)';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.record_no IS 'Служебная: номер записи в загрузке';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.recordmode IS 'Служебная: N новая запись, X before image, '' '' after image';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.doc_number IS 'BW: ZSD_A01.0DOC_NUMBER (CHAR 10). Документ сбыта';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.s_ord_item IS 'BW: ZSD_A01.0S_ORD_ITEM (NUMC 6). Позиция документа сбыта';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.sold_to IS 'BW: ZSD_A01.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.material IS 'BW: ZSD_A01.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.distr_chan IS 'BW: ZSD_A01.0DISTR_CHAN (CHAR 2). Канал сбыта';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.calday IS 'BW: ZSD_A01.0CALDAY (DATS 8). Календарный день';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.netval_inv IS 'BW: ZSD_A01.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.doc_currcy IS 'BW: ZSD_A01.0DOC_CURRCY (CUKY 5). Валюта документа';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.inv_qty IS 'BW: ZSD_A01.0INV_QTY (QUAN 17,3). Фактурированное количество';
COMMENT ON COLUMN dbt_dwh.zsd_a01_changelog.sales_unit IS 'BW: ZSD_A01.0SALES_UNIT (UNIT 3). Единица продажи';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a02_inbound (
    load_id    bigint NOT NULL,
    record_no  bigint NOT NULL,
    recordmode char(1) NOT NULL,
    sold_to    varchar(10) NOT NULL,
    calmonth   varchar(6) NOT NULL,
    doc_currcy varchar(5) NOT NULL,
    sales_unit varchar(3) NOT NULL,
    netval_inv numeric(17,2),
    inv_qty    numeric(17,3)
)
WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_stg.zsd_a02_inbound IS 'BW: ADSO ZSD_A02 (cumulative), входящая таблица, таблица BW /BIC/AZSD_A021. Продажи по клиентам и месяцам';
COMMENT ON COLUMN dbt_stg.zsd_a02_inbound.load_id IS 'Служебная: номер загрузки (meta.load_log)';
COMMENT ON COLUMN dbt_stg.zsd_a02_inbound.record_no IS 'Служебная: номер записи в загрузке';
COMMENT ON COLUMN dbt_stg.zsd_a02_inbound.recordmode IS 'Служебная: N новая запись, X before image, '' '' after image';
COMMENT ON COLUMN dbt_stg.zsd_a02_inbound.sold_to IS 'BW: ZSD_A02.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_stg.zsd_a02_inbound.calmonth IS 'BW: ZSD_A02.0CALMONTH (NUMC 6). Календарный год/месяц';
COMMENT ON COLUMN dbt_stg.zsd_a02_inbound.doc_currcy IS 'BW: ZSD_A02.0DOC_CURRCY (CUKY 5). Валюта документа';
COMMENT ON COLUMN dbt_stg.zsd_a02_inbound.sales_unit IS 'BW: ZSD_A02.0SALES_UNIT (UNIT 3). Единица продажи';
COMMENT ON COLUMN dbt_stg.zsd_a02_inbound.netval_inv IS 'BW: ZSD_A02.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_stg.zsd_a02_inbound.inv_qty IS 'BW: ZSD_A02.0INV_QTY (QUAN 17,3). Фактурированное количество';

CREATE TABLE IF NOT EXISTS dbt_mart.zsd_a02_active (
    sold_to    varchar(10) NOT NULL,
    calmonth   varchar(6) NOT NULL,
    doc_currcy varchar(5) NOT NULL,
    sales_unit varchar(3) NOT NULL,
    netval_inv numeric(17,2),
    inv_qty    numeric(17,3),
    PRIMARY KEY (sold_to, calmonth, doc_currcy, sales_unit)
)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_mart.zsd_a02_active IS 'BW: ADSO ZSD_A02 (cumulative), активные данные, таблица BW /BIC/AZSD_A022. Продажи по клиентам и месяцам';
COMMENT ON COLUMN dbt_mart.zsd_a02_active.sold_to IS 'BW: ZSD_A02.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_mart.zsd_a02_active.calmonth IS 'BW: ZSD_A02.0CALMONTH (NUMC 6). Календарный год/месяц';
COMMENT ON COLUMN dbt_mart.zsd_a02_active.doc_currcy IS 'BW: ZSD_A02.0DOC_CURRCY (CUKY 5). Валюта документа';
COMMENT ON COLUMN dbt_mart.zsd_a02_active.sales_unit IS 'BW: ZSD_A02.0SALES_UNIT (UNIT 3). Единица продажи';
COMMENT ON COLUMN dbt_mart.zsd_a02_active.netval_inv IS 'BW: ZSD_A02.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_mart.zsd_a02_active.inv_qty IS 'BW: ZSD_A02.0INV_QTY (QUAN 17,3). Фактурированное количество';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a03_inbound (
    load_id    bigint NOT NULL,
    record_no  bigint NOT NULL,
    recordmode char(1) NOT NULL,
    sold_to    varchar(10) NOT NULL,
    material   varchar(40) NOT NULL,
    calday     date NOT NULL,
    netval_inv numeric(17,2),
    doc_currcy varchar(5)
)
WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_stg.zsd_a03_inbound IS 'BW: ADSO ZSD_A03 (standard), входящая таблица, таблица BW /BIC/AZSD_A031. Продажи по материалам (с рутиной)';
COMMENT ON COLUMN dbt_stg.zsd_a03_inbound.load_id IS 'Служебная: номер загрузки (meta.load_log)';
COMMENT ON COLUMN dbt_stg.zsd_a03_inbound.record_no IS 'Служебная: номер записи в загрузке';
COMMENT ON COLUMN dbt_stg.zsd_a03_inbound.recordmode IS 'Служебная: N новая запись, X before image, '' '' after image';
COMMENT ON COLUMN dbt_stg.zsd_a03_inbound.sold_to IS 'BW: ZSD_A03.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_stg.zsd_a03_inbound.material IS 'BW: ZSD_A03.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_stg.zsd_a03_inbound.calday IS 'BW: ZSD_A03.0CALDAY (DATS 8). Календарный день';
COMMENT ON COLUMN dbt_stg.zsd_a03_inbound.netval_inv IS 'BW: ZSD_A03.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_stg.zsd_a03_inbound.doc_currcy IS 'BW: ZSD_A03.0DOC_CURRCY (CUKY 5). Валюта документа';

CREATE TABLE IF NOT EXISTS dbt_dwh.zsd_a03_active (
    sold_to    varchar(10) NOT NULL,
    material   varchar(40) NOT NULL,
    calday     date NOT NULL,
    netval_inv numeric(17,2),
    doc_currcy varchar(5),
    PRIMARY KEY (sold_to, material, calday)
)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_dwh.zsd_a03_active IS 'BW: ADSO ZSD_A03 (standard), активные данные, таблица BW /BIC/AZSD_A032. Продажи по материалам (с рутиной)';
COMMENT ON COLUMN dbt_dwh.zsd_a03_active.sold_to IS 'BW: ZSD_A03.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_dwh.zsd_a03_active.material IS 'BW: ZSD_A03.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_dwh.zsd_a03_active.calday IS 'BW: ZSD_A03.0CALDAY (DATS 8). Календарный день';
COMMENT ON COLUMN dbt_dwh.zsd_a03_active.netval_inv IS 'BW: ZSD_A03.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_dwh.zsd_a03_active.doc_currcy IS 'BW: ZSD_A03.0DOC_CURRCY (CUKY 5). Валюта документа';

CREATE TABLE IF NOT EXISTS dbt_dwh.zsd_a03_changelog (
    load_id    bigint NOT NULL,
    record_no  bigint NOT NULL,
    recordmode char(1) NOT NULL,
    sold_to    varchar(10) NOT NULL,
    material   varchar(40) NOT NULL,
    calday     date NOT NULL,
    netval_inv numeric(17,2),
    doc_currcy varchar(5)
)
WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_dwh.zsd_a03_changelog IS 'BW: ADSO ZSD_A03 (standard), журнал изменений, таблица BW /BIC/AZSD_A033. Продажи по материалам (с рутиной)';
COMMENT ON COLUMN dbt_dwh.zsd_a03_changelog.load_id IS 'Служебная: номер загрузки (meta.load_log)';
COMMENT ON COLUMN dbt_dwh.zsd_a03_changelog.record_no IS 'Служебная: номер записи в загрузке';
COMMENT ON COLUMN dbt_dwh.zsd_a03_changelog.recordmode IS 'Служебная: N новая запись, X before image, '' '' after image';
COMMENT ON COLUMN dbt_dwh.zsd_a03_changelog.sold_to IS 'BW: ZSD_A03.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_dwh.zsd_a03_changelog.material IS 'BW: ZSD_A03.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_dwh.zsd_a03_changelog.calday IS 'BW: ZSD_A03.0CALDAY (DATS 8). Календарный день';
COMMENT ON COLUMN dbt_dwh.zsd_a03_changelog.netval_inv IS 'BW: ZSD_A03.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_dwh.zsd_a03_changelog.doc_currcy IS 'BW: ZSD_A03.0DOC_CURRCY (CUKY 5). Валюта документа';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_a04_inbound (
    load_id    bigint NOT NULL,
    record_no  bigint NOT NULL,
    recordmode char(1) NOT NULL,
    matl_group varchar(9) NOT NULL,
    calmonth   varchar(6) NOT NULL,
    doc_currcy varchar(5) NOT NULL,
    sales_unit varchar(3) NOT NULL,
    netval_inv numeric(17,2),
    inv_qty    numeric(17,3)
)
WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)
DISTRIBUTED BY (matl_group);
COMMENT ON TABLE dbt_stg.zsd_a04_inbound IS 'BW: ADSO ZSD_A04 (cumulative), входящая таблица, таблица BW /BIC/AZSD_A041. Продажи по группам материалов';
COMMENT ON COLUMN dbt_stg.zsd_a04_inbound.load_id IS 'Служебная: номер загрузки (meta.load_log)';
COMMENT ON COLUMN dbt_stg.zsd_a04_inbound.record_no IS 'Служебная: номер записи в загрузке';
COMMENT ON COLUMN dbt_stg.zsd_a04_inbound.recordmode IS 'Служебная: N новая запись, X before image, '' '' after image';
COMMENT ON COLUMN dbt_stg.zsd_a04_inbound.matl_group IS 'BW: ZSD_A04.0MATL_GROUP (CHAR 9). Группа материалов';
COMMENT ON COLUMN dbt_stg.zsd_a04_inbound.calmonth IS 'BW: ZSD_A04.0CALMONTH (NUMC 6). Календарный год/месяц';
COMMENT ON COLUMN dbt_stg.zsd_a04_inbound.doc_currcy IS 'BW: ZSD_A04.0DOC_CURRCY (CUKY 5). Валюта документа';
COMMENT ON COLUMN dbt_stg.zsd_a04_inbound.sales_unit IS 'BW: ZSD_A04.0SALES_UNIT (UNIT 3). Единица продажи';
COMMENT ON COLUMN dbt_stg.zsd_a04_inbound.netval_inv IS 'BW: ZSD_A04.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_stg.zsd_a04_inbound.inv_qty IS 'BW: ZSD_A04.0INV_QTY (QUAN 17,3). Фактурированное количество';

CREATE TABLE IF NOT EXISTS dbt_mart.zsd_a04_active (
    matl_group varchar(9) NOT NULL,
    calmonth   varchar(6) NOT NULL,
    doc_currcy varchar(5) NOT NULL,
    sales_unit varchar(3) NOT NULL,
    netval_inv numeric(17,2),
    inv_qty    numeric(17,3),
    PRIMARY KEY (matl_group, calmonth, doc_currcy, sales_unit)
)
DISTRIBUTED BY (matl_group);
COMMENT ON TABLE dbt_mart.zsd_a04_active IS 'BW: ADSO ZSD_A04 (cumulative), активные данные, таблица BW /BIC/AZSD_A042. Продажи по группам материалов';
COMMENT ON COLUMN dbt_mart.zsd_a04_active.matl_group IS 'BW: ZSD_A04.0MATL_GROUP (CHAR 9). Группа материалов';
COMMENT ON COLUMN dbt_mart.zsd_a04_active.calmonth IS 'BW: ZSD_A04.0CALMONTH (NUMC 6). Календарный год/месяц';
COMMENT ON COLUMN dbt_mart.zsd_a04_active.doc_currcy IS 'BW: ZSD_A04.0DOC_CURRCY (CUKY 5). Валюта документа';
COMMENT ON COLUMN dbt_mart.zsd_a04_active.sales_unit IS 'BW: ZSD_A04.0SALES_UNIT (UNIT 3). Единица продажи';
COMMENT ON COLUMN dbt_mart.zsd_a04_active.netval_inv IS 'BW: ZSD_A04.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_mart.zsd_a04_active.inv_qty IS 'BW: ZSD_A04.0INV_QTY (QUAN 17,3). Фактурированное количество';

CREATE TABLE IF NOT EXISTS dbt_mart.zsd_d01_active (
    sold_to    varchar(10) NOT NULL,
    calmonth   varchar(6) NOT NULL,
    netval_inv numeric(17,2),
    doc_currcy varchar(5),
    PRIMARY KEY (sold_to, calmonth)
)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_mart.zsd_d01_active IS 'BW: ADSO ZSD_D01 (direct update), активные данные, таблица BW /BIC/AZSD_D012. План продаж';
COMMENT ON COLUMN dbt_mart.zsd_d01_active.sold_to IS 'BW: ZSD_D01.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_mart.zsd_d01_active.calmonth IS 'BW: ZSD_D01.0CALMONTH (NUMC 6). Календарный год/месяц';
COMMENT ON COLUMN dbt_mart.zsd_d01_active.netval_inv IS 'BW: ZSD_D01.0NETVAL_INV (CURR 17,2). Нетто-стоимость фактуры';
COMMENT ON COLUMN dbt_mart.zsd_d01_active.doc_currcy IS 'BW: ZSD_D01.0DOC_CURRCY (CUKY 5). Валюта документа';

CREATE TABLE IF NOT EXISTS dbt_stg.zsd_s01_inbound (
    load_id    bigint NOT NULL,
    record_no  bigint NOT NULL,
    recordmode char(1) NOT NULL,
    doc_number varchar(10),
    s_ord_item varchar(6),
    material   varchar(40),
    matl_group varchar(9),
    calday     date,
    inv_qty    numeric(17,3),
    sales_unit varchar(3)
)
WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)
DISTRIBUTED RANDOMLY;
COMMENT ON TABLE dbt_stg.zsd_s01_inbound IS 'BW: ADSO ZSD_S01 (staging), входящая таблица, таблица BW /BIC/AZSD_S011. Продажи: промежуточный слой';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.load_id IS 'Служебная: номер загрузки (meta.load_log)';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.record_no IS 'Служебная: номер записи в загрузке';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.recordmode IS 'Служебная: N новая запись, X before image, '' '' after image';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.doc_number IS 'BW: ZSD_S01.0DOC_NUMBER (CHAR 10). Документ сбыта';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.s_ord_item IS 'BW: ZSD_S01.0S_ORD_ITEM (NUMC 6). Позиция документа сбыта';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.material IS 'BW: ZSD_S01.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.matl_group IS 'BW: ZSD_S01.0MATL_GROUP (CHAR 9). Группа материалов';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.calday IS 'BW: ZSD_S01.0CALDAY (DATS 8). Календарный день';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.inv_qty IS 'BW: ZSD_S01.0INV_QTY (QUAN 17,3). Фактурированное количество';
COMMENT ON COLUMN dbt_stg.zsd_s01_inbound.sales_unit IS 'BW: ZSD_S01.0SALES_UNIT (UNIT 3). Единица продажи';

-- ===== 060_activation.sql =====


-- Активация ADSO ZSD_A01 (standard) по ТЗ 4.3.5: из дублей ключа берётся запись с наибольшим record_no;
-- новый ключ — вставка и N в журнал, существующий — пара X / ' ' в журнал и замена строки.
CREATE OR REPLACE FUNCTION dbt_dwh.activate_zsd_a01(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_bad bigint;
    v_changed bigint;
    v_rows bigint;
BEGIN
    SELECT count(*) INTO v_bad
    FROM dbt_stg.zsd_a01_inbound
    WHERE load_id = p_load_id AND recordmode IN ('D', 'R');
    IF v_bad > 0 THEN
        RAISE EXCEPTION 'ZSD_A01: в загрузке % есть записи с recordmode D или R (%), удаления и обратные записи не поддерживаются',
            p_load_id, v_bad;
    END IF;

    -- Before image (X): старые значения, показатели с минусом
    INSERT INTO dbt_dwh.zsd_a01_changelog (load_id, record_no, recordmode, doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
    SELECT p_load_id, d.record_no, 'X', a.doc_number, a.s_ord_item, a.sold_to, a.material, a.distr_chan, a.calday, -a.netval_inv, a.doc_currcy, -a.inv_qty, a.sales_unit
    FROM dbt_dwh.zsd_a01_active a
    JOIN (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.doc_number, i.s_ord_item ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a01_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d
      ON a.doc_number = d.doc_number AND a.s_ord_item = d.s_ord_item;
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    -- After image (' ') для существующих ключей и N для новых
    INSERT INTO dbt_dwh.zsd_a01_changelog (load_id, record_no, recordmode, doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
    SELECT p_load_id, d.record_no, CASE WHEN a.doc_number IS NULL THEN 'N' ELSE ' ' END,
           d.doc_number, d.s_ord_item, d.sold_to, d.material, d.distr_chan, d.calday, d.netval_inv, d.doc_currcy, d.inv_qty, d.sales_unit
    FROM (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.doc_number, i.s_ord_item ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a01_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d
    LEFT JOIN dbt_dwh.zsd_a01_active a
      ON a.doc_number = d.doc_number AND a.s_ord_item = d.s_ord_item;

    -- Замена строк активных данных
    DELETE FROM dbt_dwh.zsd_a01_active a
    USING (SELECT DISTINCT doc_number, s_ord_item FROM dbt_stg.zsd_a01_inbound WHERE load_id = p_load_id) d
    WHERE a.doc_number = d.doc_number AND a.s_ord_item = d.s_ord_item;

    INSERT INTO dbt_dwh.zsd_a01_active (doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
    SELECT d.doc_number, d.s_ord_item, d.sold_to, d.material, d.distr_chan, d.calday, d.netval_inv, d.doc_currcy, d.inv_qty, d.sales_unit
    FROM (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.doc_number, i.s_ord_item ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a01_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d;
    GET DIAGNOSTICS v_rows = ROW_COUNT;

    UPDATE dbt_meta.load_log
    SET message = concat_ws('; ', nullif(message, ''),
        format('ZSD_A01: активировано %s записей, новых %s, изменённых %s', v_rows, v_rows - v_changed, v_changed))
    WHERE load_id = p_load_id;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_dwh.activate_zsd_a01(bigint) IS 'BW: активация ADSO ZSD_A01 (standard): перезапись и журнал изменений';

-- Активация ADSO ZSD_A02 (cumulative) по ТЗ 4.3.5: показатели загрузки суммируются по ключу
-- и прибавляются к активным данным, новый ключ вставляется. Строки с нулевыми суммами остаются.
CREATE OR REPLACE FUNCTION dbt_mart.activate_zsd_a02(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_changed bigint := 0;
    v_new bigint;
BEGIN
    UPDATE dbt_mart.zsd_a02_active a
    SET netval_inv = coalesce(a.netval_inv, 0) + coalesce(g.netval_inv, 0),
        inv_qty = coalesce(a.inv_qty, 0) + coalesce(g.inv_qty, 0)
    FROM (SELECT i.sold_to, i.calmonth, i.doc_currcy, i.sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a02_inbound i WHERE i.load_id = p_load_id GROUP BY i.sold_to, i.calmonth, i.doc_currcy, i.sales_unit) g
    WHERE a.sold_to = g.sold_to AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit;
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    INSERT INTO dbt_mart.zsd_a02_active (sold_to, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
    SELECT g.sold_to, g.calmonth, g.doc_currcy, g.sales_unit, g.netval_inv, g.inv_qty
    FROM (SELECT i.sold_to, i.calmonth, i.doc_currcy, i.sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a02_inbound i WHERE i.load_id = p_load_id GROUP BY i.sold_to, i.calmonth, i.doc_currcy, i.sales_unit) g
    WHERE NOT EXISTS (SELECT 1 FROM dbt_mart.zsd_a02_active a WHERE a.sold_to = g.sold_to AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit);
    GET DIAGNOSTICS v_new = ROW_COUNT;

    UPDATE dbt_meta.load_log
    SET message = concat_ws('; ', nullif(message, ''),
        format('ZSD_A02: строк новых %s, изменённых %s', v_new, v_changed))
    WHERE load_id = p_load_id;
    RETURN v_new + v_changed;
END;
$$;
COMMENT ON FUNCTION dbt_mart.activate_zsd_a02(bigint) IS 'BW: активация ADSO ZSD_A02 (cumulative): суммирование';

-- Активация ADSO ZSD_A03 (standard) по ТЗ 4.3.5: из дублей ключа берётся запись с наибольшим record_no;
-- новый ключ — вставка и N в журнал, существующий — пара X / ' ' в журнал и замена строки.
CREATE OR REPLACE FUNCTION dbt_dwh.activate_zsd_a03(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_bad bigint;
    v_changed bigint;
    v_rows bigint;
BEGIN
    SELECT count(*) INTO v_bad
    FROM dbt_stg.zsd_a03_inbound
    WHERE load_id = p_load_id AND recordmode IN ('D', 'R');
    IF v_bad > 0 THEN
        RAISE EXCEPTION 'ZSD_A03: в загрузке % есть записи с recordmode D или R (%), удаления и обратные записи не поддерживаются',
            p_load_id, v_bad;
    END IF;

    -- Before image (X): старые значения, показатели с минусом
    INSERT INTO dbt_dwh.zsd_a03_changelog (load_id, record_no, recordmode, sold_to, material, calday, netval_inv, doc_currcy)
    SELECT p_load_id, d.record_no, 'X', a.sold_to, a.material, a.calday, -a.netval_inv, a.doc_currcy
    FROM dbt_dwh.zsd_a03_active a
    JOIN (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.sold_to, i.material, i.calday ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a03_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d
      ON a.sold_to = d.sold_to AND a.material = d.material AND a.calday = d.calday;
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    -- After image (' ') для существующих ключей и N для новых
    INSERT INTO dbt_dwh.zsd_a03_changelog (load_id, record_no, recordmode, sold_to, material, calday, netval_inv, doc_currcy)
    SELECT p_load_id, d.record_no, CASE WHEN a.sold_to IS NULL THEN 'N' ELSE ' ' END,
           d.sold_to, d.material, d.calday, d.netval_inv, d.doc_currcy
    FROM (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.sold_to, i.material, i.calday ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a03_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d
    LEFT JOIN dbt_dwh.zsd_a03_active a
      ON a.sold_to = d.sold_to AND a.material = d.material AND a.calday = d.calday;

    -- Замена строк активных данных
    DELETE FROM dbt_dwh.zsd_a03_active a
    USING (SELECT DISTINCT sold_to, material, calday FROM dbt_stg.zsd_a03_inbound WHERE load_id = p_load_id) d
    WHERE a.sold_to = d.sold_to AND a.material = d.material AND a.calday = d.calday;

    INSERT INTO dbt_dwh.zsd_a03_active (sold_to, material, calday, netval_inv, doc_currcy)
    SELECT d.sold_to, d.material, d.calday, d.netval_inv, d.doc_currcy
    FROM (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.sold_to, i.material, i.calday ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a03_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d;
    GET DIAGNOSTICS v_rows = ROW_COUNT;

    UPDATE dbt_meta.load_log
    SET message = concat_ws('; ', nullif(message, ''),
        format('ZSD_A03: активировано %s записей, новых %s, изменённых %s', v_rows, v_rows - v_changed, v_changed))
    WHERE load_id = p_load_id;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_dwh.activate_zsd_a03(bigint) IS 'BW: активация ADSO ZSD_A03 (standard): перезапись и журнал изменений';

-- Активация ADSO ZSD_A04 (cumulative) по ТЗ 4.3.5: показатели загрузки суммируются по ключу
-- и прибавляются к активным данным, новый ключ вставляется. Строки с нулевыми суммами остаются.
CREATE OR REPLACE FUNCTION dbt_mart.activate_zsd_a04(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_changed bigint := 0;
    v_new bigint;
BEGIN
    UPDATE dbt_mart.zsd_a04_active a
    SET netval_inv = coalesce(a.netval_inv, 0) + coalesce(g.netval_inv, 0),
        inv_qty = coalesce(a.inv_qty, 0) + coalesce(g.inv_qty, 0)
    FROM (SELECT i.matl_group, i.calmonth, i.doc_currcy, i.sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a04_inbound i WHERE i.load_id = p_load_id GROUP BY i.matl_group, i.calmonth, i.doc_currcy, i.sales_unit) g
    WHERE a.matl_group = g.matl_group AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit;
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    INSERT INTO dbt_mart.zsd_a04_active (matl_group, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
    SELECT g.matl_group, g.calmonth, g.doc_currcy, g.sales_unit, g.netval_inv, g.inv_qty
    FROM (SELECT i.matl_group, i.calmonth, i.doc_currcy, i.sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a04_inbound i WHERE i.load_id = p_load_id GROUP BY i.matl_group, i.calmonth, i.doc_currcy, i.sales_unit) g
    WHERE NOT EXISTS (SELECT 1 FROM dbt_mart.zsd_a04_active a WHERE a.matl_group = g.matl_group AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit);
    GET DIAGNOSTICS v_new = ROW_COUNT;

    UPDATE dbt_meta.load_log
    SET message = concat_ws('; ', nullif(message, ''),
        format('ZSD_A04: строк новых %s, изменённых %s', v_new, v_changed))
    WHERE load_id = p_load_id;
    RETURN v_new + v_changed;
END;
$$;
COMMENT ON FUNCTION dbt_mart.activate_zsd_a04(bigint) IS 'BW: активация ADSO ZSD_A04 (cumulative): суммирование';

-- ===== 070_transformations.sql =====


-- Трансформация ZSALES_FF → ZSD_A01 (0B6F3C1A9D2E4F5A8B7C6D5E4F3A2B10)
-- Дельта: записи источника с номером загрузки p_load_id
CREATE OR REPLACE FUNCTION dbt_dwh.tr_zsales_ff__zsd_a01(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows bigint;
BEGIN
    INSERT INTO dbt_stg.zsd_a01_inbound (load_id, record_no, recordmode, doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
    SELECT
        p_load_id,
        s.record_no,
        ' ',
        coalesce(s.doc_number, ''),  -- 0DOC_NUMBER ← прямое присвоение DOC_NUMBER
        coalesce(s.s_ord_item, '000000'),  -- 0S_ORD_ITEM ← прямое присвоение S_ORD_ITEM
        s.sold_to,  -- 0SOLD_TO ← прямое присвоение SOLD_TO
        s.material,  -- 0MATERIAL ← прямое присвоение MATERIAL
        '10',  -- 0DISTR_CHAN ← константа '10'
        s.calday,  -- 0CALDAY ← прямое присвоение CALDAY
        s.netval - s.discount,  -- 0NETVAL_INV ← формула NETVAL - DISCOUNT
        s.currency,  -- 0DOC_CURRCY ← прямое присвоение CURRENCY
        s.qty,  -- 0INV_QTY ← прямое присвоение QTY
        s.unit  -- 0SALES_UNIT ← прямое присвоение UNIT
    FROM dbt_stg.zsales_ff s
    WHERE s.load_id = p_load_id;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_dwh.tr_zsales_ff__zsd_a01(bigint) IS 'BW: трансформация ZSALES_FF → ZSD_A01 (0B6F3C1A9D2E4F5A8B7C6D5E4F3A2B10), DTP DTP_00ZSALESFF0ZSDA01000000';

-- Трансформация ZSD_A01 → ZSD_A02 (1C7A4D2B0E3F5A6B9C8D7E6F5A4B3C21)
-- Дельта: записи источника с номером загрузки p_load_id
-- Источник — журнал изменений: before image (X) с минусом снимает старые суммы
CREATE OR REPLACE FUNCTION dbt_mart.tr_zsd_a01__zsd_a02(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows bigint;
BEGIN
    INSERT INTO dbt_stg.zsd_a02_inbound (load_id, record_no, recordmode, sold_to, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
    SELECT
        p_load_id,
        s.record_no,
        s.recordmode,
        coalesce(s.sold_to, ''),  -- 0SOLD_TO ← прямое присвоение 0SOLD_TO
        coalesce(to_char(s.calday, 'YYYYMM'), '000000'),  -- 0CALMONTH ← преобразование времени 0CALDAY → 0CALMONTH
        coalesce(s.doc_currcy, ''),  -- 0DOC_CURRCY ← прямое присвоение 0DOC_CURRCY
        coalesce(s.sales_unit, ''),  -- 0SALES_UNIT ← прямое присвоение 0SALES_UNIT
        s.netval_inv,  -- 0NETVAL_INV ← прямое присвоение 0NETVAL_INV
        s.inv_qty  -- 0INV_QTY ← прямое присвоение 0INV_QTY
    FROM dbt_dwh.zsd_a01_changelog s
    WHERE s.load_id = p_load_id;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_mart.tr_zsd_a01__zsd_a02(bigint) IS 'BW: трансформация ZSD_A01 → ZSD_A02 (1C7A4D2B0E3F5A6B9C8D7E6F5A4B3C21), DTP DTP_01ZSDA010ZSDA020000000';

-- Трансформация ZSD_A01 → ZSD_A03 (2D8B5E3C1F4A6B7C0D9E8F7A6B5C4D32)
-- Дельта: записи источника с номером загрузки p_load_id
-- Цель с перезаписью: из журнала изменений берутся новые записи и after image, before image (X) пропускаются
--
-- ЗАГОТОВКА: требует ручной доработки. Причины:
--   - конечная рутина ABAP
-- Прогон через эту трансформацию заблокирован. Черновик INSERT по поддерживаемым правилам:
--
-- INSERT INTO dbt_stg.zsd_a03_inbound (load_id, record_no, recordmode, sold_to, material, calday, netval_inv, doc_currcy)
-- SELECT
--     p_load_id,
--     s.record_no,
--     s.recordmode,
--     coalesce(s.sold_to, ''),  -- 0SOLD_TO ← прямое присвоение 0SOLD_TO
--     coalesce(s.material, ''),  -- 0MATERIAL ← прямое присвоение 0MATERIAL
--     coalesce(s.calday, DATE '0001-01-01'),  -- 0CALDAY ← прямое присвоение 0CALDAY
--     s.netval_inv,  -- 0NETVAL_INV ← прямое присвоение 0NETVAL_INV
--     s.doc_currcy  -- 0DOC_CURRCY ← прямое присвоение 0DOC_CURRCY
-- FROM dbt_dwh.zsd_a01_changelog s
-- WHERE s.load_id = p_load_id
--   AND s.recordmode <> 'X';
--
-- Рутина ABAP: конечная рутина
-- METHOD end_routine.
-- * Убираем строки с нулевой выручкой и пересчитываем EUR в RUB по плановому курсу
--   DELETE RESULT_PACKAGE WHERE netval_inv = 0.
--   LOOP AT RESULT_PACKAGE ASSIGNING <result_fields>.
--     IF <result_fields>-doc_currcy = 'EUR'.
--       <result_fields>-netval_inv = <result_fields>-netval_inv * '95.50'.
--       <result_fields>-doc_currcy = 'RUB'.
--     ENDIF.
--   ENDLOOP.
-- ENDMETHOD.
CREATE OR REPLACE FUNCTION dbt_dwh.tr_zsd_a01__zsd_a03(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows bigint;
BEGIN
    RAISE EXCEPTION 'Трансформация ZSD_A01 → ZSD_A03 требует ручной доработки: конечная рутина ABAP';
    RETURN 0;
END;
$$;
COMMENT ON FUNCTION dbt_dwh.tr_zsd_a01__zsd_a03(bigint) IS 'BW: трансформация ZSD_A01 → ZSD_A03 (2D8B5E3C1F4A6B7C0D9E8F7A6B5C4D32), DTP DTP_02ZSDA010ZSDA030000000. ЗАГОТОВКА: требует ручной доработки: конечная рутина ABAP';

-- Трансформация ZSD_A01 → ZSD_A04 (3E9C6F4D2A5B7C8D1E0F9A8B7C6D5E43)
-- Полная загрузка: все данные источника пишутся одной новой загрузкой p_load_id
-- Атрибут 0MATL_GROUP читается из справочника 0MATERIAL на момент прогона, а не на момент загрузки
CREATE OR REPLACE FUNCTION dbt_mart.tr_zsd_a01__zsd_a04(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows bigint;
BEGIN
    INSERT INTO dbt_stg.zsd_a04_inbound (load_id, record_no, recordmode, matl_group, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
    SELECT
        p_load_id,
        row_number() OVER (ORDER BY s.doc_number, s.s_ord_item),
        ' ',
        coalesce(m1.matl_group, ''),  -- 0MATL_GROUP ← чтение атрибута 0MATERIAL-0MATL_GROUP
        coalesce(to_char(s.calday, 'YYYYMM'), '000000'),  -- 0CALMONTH ← преобразование времени 0CALDAY → 0CALMONTH
        coalesce(s.doc_currcy, ''),  -- 0DOC_CURRCY ← прямое присвоение 0DOC_CURRCY
        coalesce(s.sales_unit, ''),  -- 0SALES_UNIT ← прямое присвоение 0SALES_UNIT
        s.netval_inv,  -- 0NETVAL_INV ← прямое присвоение 0NETVAL_INV
        s.inv_qty  -- 0INV_QTY ← прямое присвоение 0INV_QTY
    FROM dbt_dwh.zsd_a01_active s
    LEFT JOIN dbt_md.material_attr m1
      ON m1.material = s.material;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_mart.tr_zsd_a01__zsd_a04(bigint) IS 'BW: трансформация ZSD_A01 → ZSD_A04 (3E9C6F4D2A5B7C8D1E0F9A8B7C6D5E43), DTP DTP_03ZSDA010ZSDA040000000';
