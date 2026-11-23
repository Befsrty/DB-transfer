# Импорт из Teradata

Teradata и цель — MPP-СУБД одного устройства: строки раскладываются по AMP хешем первичного индекса, как в Greenplum по сегментам хешем ключа распределения. Поэтому структура переносится почти прямо: таблица 1:1 с теми же колонками, ключом, распределением и секциями. Цель по умолчанию — Greengage 7 / ADB 7.5 (диалект `greenplum7`), режим совместимости — ADB 6.x и Yandex MPP 6.x (`greenplum`).

Модуль — `dbtransfer/importers/teradata.py`, состав запросов — `dbtransfer/rules/teradata_dict.yaml`. Таблицы становятся объектами модели так же, как для Oracle и MS SQL Server (`rdbms_dict.py`): по умолчанию таблица 1:1, `--target adso` — поток DataSource → ADSO, как в BW.

## Как получить словарь

**Подключение** через драйвер [teradatasql](https://pypi.org/project/teradatasql/) (ставится отдельно: `uv sync --extra teradata`). Нужно право `SELECT` на представления `DBC` (по умолчанию есть у `PUBLIC`); список баз обязателен:

```
set DBT_SRC_PASSWORD=...
uv run dbtransfer import --source teradata --url "teradata://reader@td-host/?logmech=LDAP" --schemas SALES,DWH
```

Параметры после `?` передаются драйверу как есть (`logmech`, `encryptdata`, `sslmode`, `tmode` …), порт — `teradata://user@host:1025/`.

**Папка CSV** — если прямого доступа нет. `uv run dbtransfer tables --source teradata` печатает запросы к `DBC.TablesV`, `ColumnsV`, `IndicesV`, `PartitioningConstraintsV`, `All_RI_ChildrenV`, `DBCInfoV`; результат каждого сохраняется в CSV с заголовком под именем представления (`TablesV.csv` и т. д.). Обязательны `TablesV` и `ColumnsV`.

**Скрипты** BTEQ, FastLoad, MultiLoad, FastExport и TPT кладутся в подпапку `bteq` (или `scripts`) папки выгрузки либо указываются параметром `--scripts <папка>`. Расширения: `.bteq .btq .bt .sql .fld .fl .fastload .mld .ml .mload .tpt .fexp .fe`.

## Что переносится

| Что в Teradata | Что в цели |
|---|---|
| `PRIMARY INDEX` (UPI, NUPI), секционированный PI, `PRIMARY AMP INDEX` | `DISTRIBUTED BY` по тем же колонкам |
| `NO PRIMARY INDEX` (NoPI) | `DISTRIBUTED RANDOMLY` |
| Ключ: UPI, иначе `PRIMARY KEY`, `UNIQUE`, уникальный вторичный индекс (USI) | `PRIMARY KEY` и перезапись по ключу при загрузке. Если первичный индекс не входит в ключ — распределение по первому полю ключа (ограничение Greenplum) и замечание |
| SET-таблица с уникальным ключом | Как есть: дубли строк исключает ключ |
| SET-таблица без уникального ключа | Предупреждение: Teradata отбрасывает одинаковые строки, Greenplum — нет; повторная загрузка добавлением удвоит строки |
| PPI `RANGE_N(col BETWEEN … AND … EACH …)` по дате или целому, в том числе `*`, несколько диапазонов | Секции RANGE с явными границами (`START … INCLUSIVE END … EXCLUSIVE`): верхняя граница Teradata включительна, поэтому `END` — следующий день или число. Больше 1000 дневных секций — укрупняются до месяца или года с замечанием |
| `NO RANGE`, `UNKNOWN` | `DEFAULT PARTITION p_other` (при одном `UNKNOWN` туда же попадут значения вне диапазонов — замечание) |
| `CASE_N`, выражение вместо колонки, `TIMESTAMP`-границы, многоуровневое секционирование | Не переносится или переносится только первый уровень — замечание |
| `PARTITION BY COLUMN` (колоночные таблицы) | Хранение AO колоночное, сжатие zstd |
| `NOT CASESPECIFIC` у колонок ключа | Ключ сравнивается через `lower()` в загрузке и сверке, как `*_CI_*` MS SQL Server. Для остальных колонок — общее замечание: в переводимых запросах `lower()` / `ILIKE` |
| `IDENTITY`, `COMPRESS` (MVC), `FALLBACK`, `JOURNAL`, вторичные и join-индексы, hash-индексы, внешние ключи | Не переносятся — замечания; данные приходят с готовыми значениями |
| Глобальная временная таблица, таблица-очередь (QUEUE) | Обычная таблица — замечание |

Типы (`DBC.ColumnsV.ColumnType` → тип модели → тип цели по `type_rules.yaml`):

| Teradata | Цель | Замечание |
|---|---|---|
| `CHAR(n)`, `VARCHAR(n)` (`CF`, `CV`) | `char(n)`, `varchar(n)` | Длина в символах: у `UNICODE` и `GRAPHIC` `ColumnLength` в байтах делится на 2 |
| `CLOB` | `text` | |
| `BYTEINT`, `SMALLINT`, `INTEGER`, `BIGINT` | `smallint`, `smallint`, `integer`, `bigint` | |
| `DECIMAL(p,s)`, `NUMBER(p,s)` | `numeric(p,s)` | |
| `NUMBER`, `NUMBER(*)` | `numeric` | |
| `FLOAT` / `REAL` / `DOUBLE PRECISION` | `double precision` | В Teradata всегда 8 байт |
| `DATE`, `TIME(n)`, `TIMESTAMP(n)` | `date`, `time`, `timestamp` | |
| `TIME WITH TIME ZONE` | `timetz(n)` | |
| `TIMESTAMP WITH TIME ZONE` | `timestamptz` | ⚠ исходное смещение не сохраняется |
| `BYTE`, `VARBYTE`, `BLOB` | `bytea` | В CSV — шестнадцатеричная строка |
| `INTERVAL …` (13 видов) | `interval` с теми же полями (`interval day to second`) | Значение приводит сама база |
| `JSON` | `jsonb` | Пробелы и порядок ключей не сохраняются |
| `XML` | `text` | Тип `xml` есть не во всех сборках |
| `PERIOD(…)`, `ARRAY`, UDT, `DATASET` | `text` | ⚠ лучше разложить на колонки при выгрузке |
| Прочие коды | `text` + предупреждение; тип задаётся в `type_rules.yaml` проекта по коду (`types: {XX: …}`) | |

Код — в отчёт о сложности, без генерации SQL: представления (уровень 2), макросы, процедуры SPL, UDF, триггеры (уровень 3, «переписывается на PL/pgSQL»), join-индексы (как материализованные представления), скрипты загрузки (новый тип «Скрипт загрузки», норма `script` в `effort_norms.yaml`). У каждого объекта — число строк, таблицы, найденные в тексте (у скриптов база по умолчанию берётся из `DATABASE x;`), и найденные конструкции Teradata: `QUALIFY`, `SEL/INS/UPD/DEL`, `TOP n`, `SAMPLE`, `LOCKING … FOR ACCESS`, `FORMAT`, `CASESPECIFIC`, `MERGE`, `UPDATE … ELSE INSERT`, `ZEROIFNULL`, `HASHROW`, volatile-таблицы, `COLLECT STATISTICS`, `**`, `EXEC`, команды BTEQ.

## Перенос данных и сверка

```
uv run dbtransfer generate && uv run dbtransfer deploy
uv run dbtransfer transfer --url "teradata://reader@td-host/?logmech=LDAP"          # все таблицы модели
uv run dbtransfer compare  --url "teradata://…" --tables SALES.Orders               # только сверка
uv run dbtransfer export-data --url "teradata://…" --out out/data                    # только CSV, потом load
```

`transfer` читает таблицу `SELECT` через teradatasql и пишет CSV, значения форматирует Python: даты `ГГГГ-ММ-ДД`, время ISO, `DECIMAL` без показателя степени, `FLOAT` без потери знаков, `BYTE` — `0x…`, NULL — `\N`. Дальше — обычный загрузчик (`load`) с проверкой значений и журналом, таблица цели очищается в той же транзакции.

Сверка: число строк и суммы числовых колонок. В Teradata `SUM(INTEGER)` — тоже `INTEGER` и переполняется, поэтому суммы считаются как `SUM(CAST(col AS DECIMAL(38,s)))`, число строк — `CAST(COUNT(*) AS BIGINT)`.

Если данные выгружаете сами (TPT, BTEQ), задайте формат дат `ГГГГ-ММ-ДД`: `CAST(col AS DATE FORMAT 'YYYY-MM-DD')` или `DateForm=ANSIDATE`; формат по умолчанию в режиме Teradata — `YY/MM/DD`, загрузчик его не примет. Колонки с собственным `FORMAT` у дат перечисляются в замечаниях импорта.

## Допущения и ограничения

* **Живой Teradata не проверялся.** Vantage Express распространяется только образом виртуальной машины (VMware / VirtualBox), в CI его не поднять. Коды `ColumnType` и `IndexType`, `CheckOpt = 'Y'` у SET-таблиц, `UpperCaseFlag` и вид `ConstraintText` взяты из документации Teradata (Data Dictionary 16.20–17.20) и проверены на синтетике `tests/data/teradata_dict`. Первая проверка на настоящем словаре может потребовать правки разбора.
* **Сквозной тест** (`tests/integration/test_teradata_db.py`) идёт в CI на Greengage 7.5.1 и 6.32: DDL из словаря, развёртывание, выгрузка через DB-API, загрузка, сверка. Источником данных в нём служит sqlite3 — тоже драйвер DB-API; особенности значений teradatasql (`Decimal`, `datetime`, `bytes`) проверяются модульными тестами.
* **Текст кода** берётся из `TablesV.RequestText`: у очень длинных макросов и процедур он может быть обрезан, число строк тогда занижено.
* **Объёмы.** Выгрузка идёт одной сессией через драйвер, а загрузчик держит файл в памяти — это для проверки переноса на реальных данных. Для боевого объёма — TPT Export в файлы и `gpfdist` / PXF, а `compare` — для сверки после них.
* **Значение `\N` в данных** (строка из двух символов) после переноса станет NULL — ограничение формата выгрузки.
* SQL макросов, процедур и представлений не переводится. Перевод SQL Teradata → PostgreSQL (sqlglot) — следующий шаг этапа 1 плана.
