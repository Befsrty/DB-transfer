-- Удаление всех схем прототипа вместе с данными и журналами
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект clickhouse.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

DROP DATABASE IF EXISTS dbt_meta;
DROP DATABASE IF EXISTS dbt_mart;
DROP DATABASE IF EXISTS dbt_dwh;
DROP DATABASE IF EXISTS dbt_md;
DROP DATABASE IF EXISTS dbt_stg;
