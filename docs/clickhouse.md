# Цель ClickHouse (Yandex Managed Service for ClickHouse)

Первая версия: пакет SQL для ClickHouse **генерируется**, но приложение его пока не развёртывает и не прогоняет. Развёртывание, загрузка CSV, прогон и сверка из приложения по-прежнему работают только с Greengage и PostgreSQL.

```
uv run dbtransfer generate --dialect clickhouse -p demo
```

В веб-интерфейсе: вкладка «Пакет SQL», диалект «ClickHouse (Yandex Cloud)».

## Что получается

| Файл | Greengage | ClickHouse |
|---|---|---|
| `010_schemas.sql` | `CREATE SCHEMA` | `CREATE DATABASE`: в ClickHouse слой — это база данных |
| `020_meta.sql` | последовательности и служебные таблицы | только служебные таблицы, последовательностей в ClickHouse нет |
| `030`…`050` | heap / AO-таблицы, `DISTRIBUTED BY`, `COMMENT ON` | движки семейства MergeTree, `ORDER BY`, комментарии внутри `CREATE TABLE` |
| `060_activation.sql` | функции PL/pgSQL | пошаговые запросы, у каждого шага заголовок `-- Шаг N.` |
| `070_transformations.sql` | функции PL/pgSQL | пошаговые запросы (для трансформации — один `INSERT … SELECT`) |
| `deploy_all.sql` | весь пакет для `psql -f` | только DDL (010–050) для `clickhouse-client --multiquery --queries-file` |

Шаги активации и трансформаций доступны и в коде: `SqlObject.steps` — список пар (заголовок, SQL без «;»). Будущий прогон для ClickHouse будет выполнять их по очереди с параметром запроса `{p_load_id:Int64}` (так передаются параметры в clickhouse-client — `--param_p_load_id=…`, — в HTTP-интерфейсе и в clickhouse-connect).

Шаблоны лежат в `dbtransfer/generator/templates/clickhouse/`, генератор — `dbtransfer/generator/clickhouse.py`. Функции дат и строк, которые различаются у PostgreSQL и ClickHouse, вынесены в `dbtransfer/generator/dialect.py`; пакет Greengage от этого не изменился (эталон `tests/golden/sql` тот же). Эталон ClickHouse — `tests/golden/clickhouse`.

### Активация standard (перезапись, журнал изменений)

1. Проверка: записи с `recordmode` D или R останавливают активацию (`throwIf`).
2. Before image (X) в журнал изменений: старые значения, показатели с минусом.
3. After image (' ') для существующих ключей и N для новых.
4. `DELETE` заменяемых строк активных данных.
5. `INSERT` новых строк.
6. `SELECT` с итогами для журнала загрузок (активировано, новых, изменённых).

Из дублей ключа в загрузке берётся запись с наибольшим `record_no` (`ORDER BY record_no DESC LIMIT 1 BY <ключ>`), как в функции Greengage.

### Активация cumulative (суммирование)

В ClickHouse нет `UPDATE … FROM`, поэтому для каждой cumulative ADSO создаётся рабочая таблица `<префикс>_stg.<adso>_work`:

1. `TRUNCATE` рабочей таблицы.
2. В неё пишутся новые значения: сумма загрузки по ключу плюс активные данные; `_hit = 1`, если ключ уже был.
3. `DELETE` старых строк с этими ключами из активных данных.
4. `INSERT` строк из рабочей таблицы.
5. `SELECT` с итогами (новых, изменённых).

## Допущения первой версии

Их нужно проверить на настоящем кластере Yandex Cloud. В этой версии SQL на сервере ClickHouse не выполнялся: проверены только генерация и юнит-тесты.

1. **Версия ClickHouse 23.8 или новее.** Нужны lightweight `DELETE` (без экспериментальных настроек — с 23.3), `Date32`, оконные функции и `throwIf` с текстом. Шаг `DELETE … WHERE (ключ) IN (SELECT …)` должен дождаться завершения, иначе следующий `INSERT` увидит старые строки; в 24.4 и новее за это отвечает настройка `lightweight_deletes_sync` (по умолчанию ждёт).
2. **Один хост или один шард без репликации.** Движки — `MergeTree` и `ReplacingMergeTree`. Для кластера из нескольких хостов в Yandex Cloud в `type_rules.yaml` (секция `clickhouse.engines`) нужно указать `ReplicatedMergeTree` и добавить `ON CLUSTER` к DDL; для шардов — ещё таблицы `Distributed`. Это не генерируется.
3. **Базы данных создаются SQL-запросом.** В Yandex Managed ClickHouse для этого в настройках кластера должно быть включено управление базами данных через SQL (`sql_database_management`), иначе базы `<префикс>_stg`, `_md`, `_dwh`, `_mart`, `_meta` нужно создать в консоли.
4. **Транзакций нет.** Если шаг активации standard упал на середине, строки загрузки в журнале изменений нужно удалить и повторить активацию целиком. Для cumulative повтор с первого шага безопасен, пока не выполнен шаг `DELETE`. Прогоны не должны идти параллельно.
5. **Последовательностей нет.** Номера загрузок, развёртываний и сверок должно выдавать приложение (например, `max(load_id) + 1` по `load_log`).
6. **Типы.** Перевод идёт в два шага: ABAP → тип PostgreSQL по `type_rules.yaml`, затем тип PostgreSQL → тип ClickHouse по секции `clickhouse.types`. `varchar(n)` и `char(n)` → `String` (длина не проверяется), `numeric(p,s)` → `Decimal(p,s)`, `date` → `Date32`, `time` → `String` в виде `ЧЧ:ММ:СС`. Колонки без NOT NULL — `Nullable(…)`, как в Greengage. Для экономии места можно в проекте заменить `String` на `LowCardinality(String)`.
7. **Пустая дата в ключевом поле — `1900-01-01`**, а не `0001-01-01`: это нижняя граница `Date32`. При сверке с эталоном BW нужно учитывать эту заглушку.
8. **NULL при соединении со справочником.** У трансформаций стоит `SETTINGS join_use_nulls = 1`, чтобы строка без пары в справочнике давала NULL, как в Greengage, а не пустое значение по умолчанию ClickHouse.
9. **Строковые функции формул — UTF8-варианты** (`substringUTF8`, `lengthUTF8`, `upperUTF8`): обычные считают байты и ломаются на кириллице.
10. **Число строк трансформации** берётся из итога запроса (`written_rows`), а не из отдельного запроса.
11. **Ключ сортировки:** первичный ключ таблицы, для входящих таблиц и журналов — `(load_id, record_no)`, для служебных — номер записи. ClickHouse не проверяет уникальность ключа: дубли исключает сама активация.

## Что дальше

1. Подключение к ClickHouse (clickhouse-connect, порт 8443 с TLS в Yandex Cloud) в `db.py` и параметры в `.env`.
2. Развёртывание DDL с журналом `deploy_log`, загрузка CSV и прогон шагов из `SqlObject.steps` в `runtime.py`, сверка с эталоном.
3. Интеграционный тест в CI с контейнером `clickhouse/clickhouse-server` и, затем, проверка на кластере Yandex Cloud.
4. Генерация для кластера: `ON CLUSTER`, Replicated-движки, таблицы `Distributed`.
