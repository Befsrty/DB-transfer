# Arenadata DB 7.5 / Greengage 7 и режим совместимости 6.x

Основная цель генерации — **Arenadata DB 7.5.x** (ядро PostgreSQL 12). Публичного образа ADB нет, поэтому в CI и в
`docker-compose.yml` используется **Greengage 7.5.1** — открытое ядро, на котором построен ADB 7.5. Это допущение:
SQL, проверенный на Greengage 7.5.1, считаем работающим на ADB 7.5.1, пока не будет стенда ADB.

ADB 6.x, Greengage 6, Greenplum 6 и Yandex MPP Analytics for PostgreSQL (Greenplum 6.28/6.29) — режим
совместимости, диалект `greenplum`. Его пакет SQL не изменился: эталонные файлы `tests/golden/sql` прежние.

| Диалект | Цель | Ядро | Как выбирается |
|---|---|---|---|
| `greenplum7` | ADB 7.5, Greengage 7, Greenplum 7 | PostgreSQL 12 | сервер Greenplum-семейства с `server_version_num` ≥ 120000; умолчание `generate` и интерфейса |
| `greenplum` | ADB 6.x, Greengage 6, Greenplum 6, Yandex MPP | PostgreSQL 9.4 | сервер Greenplum-семейства с ядром ниже 12; `generate --dialect greenplum` |
| `postgres` | PostgreSQL 12+ | — | прочие серверы |

`deploy`, `run` и `run-demo` выбирают диалект по подключённому серверу сами (`db.server_dialect`).
Функция `generate()` в Python по умолчанию по-прежнему берёт `greenplum`, чтобы не менять поведение существующих
вызовов; командная строка и интерфейс по умолчанию берут `greenplum7`.

## Что различается в генерируемом SQL

| Место | `greenplum7` | `greenplum` (6.x) |
|---|---|---|
| AO-колоночные таблицы (входящие, журнал изменений, DataSource) | `USING ao_column WITH (compresstype=zstd, compresslevel=1)` | `WITH (appendonly=true, orientation=column, compresstype=zstd, compresslevel=1)` |
| PRIMARY KEY на AO-таблице (`overrides.<объект>.storage.active: ao_column`) | разрешён | ошибка правил: только heap |
| Последовательности `meta` | `CREATE SEQUENCE IF NOT EXISTS` | блок `DO` с проверкой `pg_class` |

Параметры хранения в `type_rules.yaml` по-прежнему пишутся в форме 6.x: генератор для 7 сам переводит
`appendonly`/`appendoptimized` и `orientation` в метод доступа `ao_column` или `ao_row`, остальное идёт в `WITH`.

## Различия, которые генератор пока не использует

Они заведены флагами в `generator/dialects.py` (`Dialect`), чтобы следующие этапы (переводчик SQL, процедуры
Oracle/MS SQL, партиционирование) опирались на них, а не на имя диалекта.

| Флаг | 7 | 6.x | Где понадобится |
|---|---|---|---|
| `procedures` — `CREATE PROCEDURE` и `COMMIT` внутри | да ⚠ проверить на стенде | нет: только функции, транзакцию дробит оркестратор | перевод PL/SQL и T-SQL с промежуточными COMMIT |
| `declarative_partitions` — `PARTITION BY … ` + `PARTITION OF` | да; классический `START … END … EVERY` тоже, без `START EXCLUSIVE` / `END INCLUSIVE` | только классический синтаксис | `overrides.<объект>.partition_by` по 0CALDAY / 0CALMONTH |
| `statement_triggers` — `FOR EACH STATEMENT` | нет | нет | триггеры источников переносим в ETL |
| `on_conflict` — `INSERT … ON CONFLICT` | да ⚠ проверить на стенде | нет: `UPDATE` + `INSERT … WHERE NOT EXISTS` | перевод `MERGE`; `MERGE` нет ни в 6, ни в 7 |
| `generated_columns` — `GENERATED ALWAYS AS (…) STORED` | да | нет | вычисляемые колонки Oracle/MS SQL |

Функции активации и трансформаций остаются функциями (`RETURNS bigint`) в обоих диалектах: им не нужен COMMIT
внутри, а приложение читает из них число строк.

## Проверка

- Модульные тесты `tests/unit/test_dialects.py`: DDL 7 и 6, выбор диалекта по строке версии.
- Интеграционные тесты (`pytest -m db`) в CI идут матрицей на `woblerr/greengage:7.5.1` и `:6.32.0`;
  `test_server_dialect_matches_ci_matrix` проверяет, что каждая версия получила свой диалект.
- Локально: `docker compose up -d` поднимает Greengage 7.5.1 на порту 15432;
  `docker compose --profile gg6 up -d greengage6` — Greengage 6.32 на 15433 (в `.env` `DBT_DB_PORT=15433`).
  У версий разные тома: каталог данных 7 несовместим с 6. Прежний том `gg_data` достаётся контейнеру 6.

## Допущения

- ADB 7.5 ≈ Greengage 7.5.1 по SQL. Расширения и настройки, которые есть только в ADB (ADB Control, ADBM,
  коммерческие расширения), не проверяются.
- Apache Cloudberry (ядро PostgreSQL 14, есть в Yandex MPP) по строке версии попадает в `greenplum7`; отдельно
  не проверялся.
