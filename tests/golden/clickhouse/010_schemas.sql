-- Базы данных слоёв (в ClickHouse схема — это база данных)
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект clickhouse.
-- Модель: Продажи (демо), хеш 449b73d173195658. Файл перезаписывается при генерации, правки вносите в модель и правила.

CREATE DATABASE IF NOT EXISTS dbt_stg;

CREATE DATABASE IF NOT EXISTS dbt_md;

CREATE DATABASE IF NOT EXISTS dbt_dwh;

CREATE DATABASE IF NOT EXISTS dbt_mart;

CREATE DATABASE IF NOT EXISTS dbt_meta;
