-- Служебные таблицы
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект greenplum.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

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
