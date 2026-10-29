-- Входные таблицы DataSource
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект greenplum.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

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
