# Импорт из Greenplum (Greenplum 6/7, Yandex MPP, Cloudberry)

Источник той же семьи, что и цель: Greenplum 6 и 7 (Broadcom), Yandex MPP Analytics for PostgreSQL (Greenplum 6 и Cloudberry 2), Apache Cloudberry, Greengage и Arenadata DB. Поэтому структура переносится **как есть, без перевода**: те же типы колонок, ключи, распределение по сегментам, хранение и секции. Цель по умолчанию — Greengage 7 / ADB 7.5 (диалект `greenplum7`), режим совместимости — ADB 6.x и Yandex MPP 6.x (`greenplum`).

Модуль — `dbtransfer/importers/greenplum.py`, состав запросов — `dbtransfer/rules/greenplum_dict.yaml`. Таблицы становятся объектами модели так же, как для Oracle и MS SQL Server (`rdbms_dict.py`): по умолчанию таблица 1:1, `--target adso` — поток DataSource → ADSO, как в BW (в нём распределение и хранение берутся из `type_rules.yaml`).

## Как получить каталог

**Подключение к базе** — проще всего, драйвер psycopg уже стоит. Нужен любой пользователь базы с правом чтения каталога:

```
set DBT_SRC_PASSWORD=...
uv run dbtransfer import --source greenplum --url greenplum://reader@gp-master:5432/dwh --schemas sales,dwh
```

Без `--schemas` берутся все схемы, кроме служебных (`pg_catalog`, `information_schema`, `gp_toolkit`, `pg_aoseg`, `pg_toast`, `pg_temp_*`). Версия сервера определяется сама: для ядра PostgreSQL 9.4 (Greenplum 6, Yandex MPP 6.x) и 12+ (Greenplum 7, Cloudberry, Greengage 7) запросы разные, колонки результата одинаковые.

**Папка CSV** — если прямого доступа нет. `uv run dbtransfer tables --source greenplum` печатает оба набора запросов; результат каждого сохраняется в CSV с заголовком (`\copy (запрос) to 'TABLES.csv' csv header` в psql). Обязательны `TABLES` и `COLUMNS`.

## Что переносится

| Что в источнике | Что в цели |
|---|---|
| Тип колонки (`format_type`) | Тот же тип: `varchar(20)`, `numeric(15,2)`, `timestamp(3)`, `jsonb`, `text[]`, `boolean`, `uuid`… Правила `type_rules.yaml` к ним не применяются |
| Тип пользователя (домен, перечисление, составной) | `text` + предупреждение: создайте тип в цели и поменяйте тип колонки |
| `abstime`, `reltime`, `tinterval` | `text` + предупреждение: в PostgreSQL 12 (Greenplum 7) этих типов нет |
| `PRIMARY KEY`, иначе первый `UNIQUE` | `PRIMARY KEY` |
| `DISTRIBUTED BY (…)`, `RANDOMLY`, `REPLICATED` (`pg_get_table_distributedby`) | То же. Классы операторов (legacy `cdbhash_*_ops`) не переносятся — замечание |
| heap, AO строчное, AO колоночное и параметры `WITH` (сжатие, `fillfactor`, `blocksize`) | То же. Для 7 — `USING ao_row / ao_column WITH (…)`. `quicklz` в 7 нет — заменяется на `zstd` уровня 1 с предупреждением |
| AO-таблица с `PRIMARY KEY` (бывает только в источнике 7) | В 7 — как есть; в 6 — heap с предупреждением |
| Секции RANGE и LIST верхнего уровня, `DEFAULT PARTITION`, разрывы между секциями | Классический синтаксис `PARTITION BY … (START … END …)`, имена секций сохраняются; безымянные секции `EVERY` получают имена `p2`, `p3` по номеру в источнике |
| Подсекции (`SUBPARTITION`), HASH-секции 7 | Не переносятся — замечание |
| `CHECK`, `FOREIGN KEY`, значения по умолчанию из последовательностей | Не переносятся — замечание; данные приходят с готовыми значениями |
| `WITH OIDS` (только 6) | Замечание: в 7 скрытой колонки `oid` нет |
| Функции и процедуры (PL/pgSQL, SQL, PL/Python …) | Отчёт о сложности: код на том же языке, перевода нет, проверка на целевой версии (6 → 7: PostgreSQL 9.4 → 12). Перегрузки — отдельные строки, связанные таблицы найдены по тексту |
| Представления, материализованные представления | Отчёт о сложности, уровень 1: переносятся как есть после таблиц |
| Внешние таблицы (gpfdist, PXF, s3) и сторонние (FDW) | Отчёт о сложности: адреса и серверы в новой среде другие, создаются вручную |

Правила проекта сильнее источника: `overrides.<схема.таблица>.distributed_by` и `storage` в `type_rules.yaml` заменяют распределение и хранение источника.

## Перенос данных и сверка

```
uv run dbtransfer generate && uv run dbtransfer deploy
uv run dbtransfer transfer --url greenplum://reader@gp-master:5432/dwh            # все таблицы модели
uv run dbtransfer transfer --url … --tables sales.orders,sales.fact_sales
uv run dbtransfer compare  --url …                                                 # только сверка
uv run dbtransfer export-data --url … --out out/data                               # только CSV, потом load
```

`transfer` выгружает каждую таблицу из источника `COPY … TO STDOUT` в `out/data/<схема>.<таблица>.csv` и загружает обычным загрузчиком (`load`): проверка значений, журнал `meta.load_log`. Перенос полный: таблица цели очищается в той же транзакции, что и загрузка, поэтому повтор не удваивает строки, а при ошибке остаются прежние данные. В выгрузке NULL записан как `\N` (пустая строка остаётся пустой строкой), `bytea` — как `0x…`, время — в UTC, `double precision` — без потери знаков (`extra_float_digits = 3`).

Сверка (`compare`, и после `transfer` по умолчанию): по каждой таблице число строк и суммы всех числовых колонок в источнике и в цели. Целые и `numeric` сравниваются точно, `real` и `double precision` — с относительным допуском 1e-9 (порядок сложения по сегментам разный). Код возврата 1 при расхождении.

## Допущения и ограничения

* **Источник в CI — Greengage 6.32.** Публичного образа Greenplum 6 и Yandex MPP нет; Greengage 6 — форк Greenplum 6 с тем же каталогом. Сквозной прогон CI: Greengage 6.32 → Greengage 7.5.1 (`greenplum 6.32 -> greengage 7.5.1`), плюс в матрице `greengage` источник и цель на одном сервере 6.32 и 7.5.1 — так проверяются оба варианта запросов каталога.
* **Cloudberry 2 на стенде не проверялся.** Он читается вариантом запросов для ядра 12+ (`pg_am`, `relispartition`, `pg_get_partkeydef`), как Greenplum 7; это допущение до появления стенда.
* **Объёмы.** Выгрузка идёт через мастер, а загрузчик держит файл в памяти — это для проверки переноса на реальных данных, а не для сотен гигабайт. Для боевого объёма — `gpcopy` / `gpfdist` / PXF между кластерами, а `compare` — для сверки после них.
* **Значение `\N` в данных** (строка из двух символов) после переноса станет NULL — ограничение формата CSV-выгрузки.
* Кодировка поколонного сжатия (`ENCODING` у колонок AO, `pg_attribute_encoding`), индексы кроме ключа, права (`GRANT`), владельцы, табличные пространства и ресурсные группы не переносятся.
