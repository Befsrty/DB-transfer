-- Таблицы ADSO
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект greenplum.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

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
