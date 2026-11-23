-- Служебные таблицы
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект clickhouse.
-- Модель: Продажи (демо), хеш 449b73d173195658. Файл перезаписывается при генерации, правки вносите в модель и правила.

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
