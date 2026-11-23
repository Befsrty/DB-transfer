-- Входные таблицы DataSource
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект clickhouse.
-- Модель: Продажи (демо), хеш fd9833907e6e5870. Файл перезаписывается при генерации, правки вносите в модель и правила.

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
