-- Удаление всех схем прототипа вместе с данными и журналами
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект greenplum.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

DROP SCHEMA IF EXISTS dbt_meta CASCADE;
DROP SCHEMA IF EXISTS dbt_mart CASCADE;
DROP SCHEMA IF EXISTS dbt_dwh CASCADE;
DROP SCHEMA IF EXISTS dbt_md CASCADE;
DROP SCHEMA IF EXISTS dbt_stg CASCADE;
