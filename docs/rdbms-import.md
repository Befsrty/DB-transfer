# Импорт из Oracle и MS SQL Server

Кроме выгрузок SAP BW, модель можно построить по словарю реляционной СУБД: Oracle (`ALL_*`) или MS SQL Server (`INFORMATION_SCHEMA` и `sys.*`). Дальше всё работает как для BW: отчёт о сложности, пакет SQL для Greengage/Arenadata DB, развёртывание, загрузка CSV с данными, прогон и сверка.

Это первая версия источника. Что в ней сделано и какие приняты допущения — ниже.

## Как получить словарь

**Выгрузка в CSV.** Запросы печатает команда

```
uv run dbtransfer tables --source oracle      # или --source mssql
```

Каждый запрос выполняется в SQL Developer, DBeaver или SSMS, результат сохраняется в CSV с заголовком под именем из комментария (`ALL_TAB_COLUMNS.csv`, `COLUMNS.csv`, …), все файлы в одну папку. Обязательны два файла: `ALL_TABLES` и `ALL_TAB_COLUMNS` для Oracle, `TABLES` и `COLUMNS` для MS SQL Server. Без остальных импорт пройдёт, но не будет ключей, комментариев и отчёта о представлениях и коде. Кодировка (UTF-8 или Windows-1251) и разделитель (`,` `;` или табуляция) определяются автоматически, как у выгрузок SE16.

```
uv run dbtransfer init C:\work\my-oracle
uv run dbtransfer import C:\work\my-oracle\bw_export --source oracle -p C:\work\my-oracle
uv run dbtransfer import <папка> --source oracle --schemas HR,SALES   # только эти схемы
```

**Подключение к базе.** Нужен драйвер: `uv sync --extra oracle` (python-oracledb в тонком режиме, Oracle Client не нужен) или `uv sync --extra mssql` (pymssql). Пароль берётся из переменной `DBT_SRC_PASSWORD`, список схем обязателен:

```
set DBT_SRC_PASSWORD=...
uv run dbtransfer import --source oracle --url oracle://dwh_reader@ora-host:1521/DWH --schemas DWH,STG
uv run dbtransfer import --source mssql  --url mssql://reader@sql-host:1433/Shop --schemas dbo,sales
```

Выполняются те же запросы, что печатает `dbtransfer tables`. В интерфейсе на вкладке «Импорт» источник выбирается переключателем, импорт идёт из папки.

## Во что превращаются объекты

| В источнике | В модели | В целевой базе |
|---|---|---|
| Таблица `SALES.ORDERS` с первичным ключом | DataSource `SALES_ORDERS` (система `ORACLE` или имя базы) и ADSO `SALES_ORDERS` вида standard | `dbt_stg.sales_orders` для CSV с данными, `dbt_stg.sales_orders_inbound`, `dbt_dwh.sales_orders_active` с PRIMARY KEY, `dbt_dwh.sales_orders_changelog` |
| Таблица без первичного ключа, но с уникальным | То же, ключом берётся первый по имени уникальный ключ | То же |
| Таблица без ключей | DataSource и ADSO вида staging | Только `dbt_stg.<имя>` и `<имя>_inbound`, без активации |
| Колонки | Поля с типом модели; тип источника и комментарий — в комментарии поля | Типы по `type_rules.yaml` |
| Между ними | Трансформация `TR_<имя>` из прямых присвоений по всем колонкам, дельта-DTP | Функция трансформации и функция активации |
| Представление, материализованное представление | Прочий объект «Представление», уровень 2, упомянутые таблицы — в связях | Не генерируется |
| Процедура, функция, пакет, триггер | Прочий объект «Процедура, функция, триггер», уровень 3, трудоёмкость по числу строк | Не генерируется |
| Внешний ключ, секционирование, отключённый ключ | Сведения в отчёте о проверке импорта | Не переносится |

Загрузка данных: `dbtransfer load SALES_ORDERS <файл>.csv`, затем `dbtransfer run <load_id>`. Колонки файла называются как колонки таблицы источника. Повторная загрузка с теми же ключами перезаписывает строки (активация standard). Сверка: `dbtransfer reconcile SALES_ORDERS etalon.csv` — по ключу таблицы и всем числовым колонкам вне ключа.

## Перевод типов

| Oracle | MS SQL Server | Тип модели | Greengage |
|---|---|---|---|
| VARCHAR2(n), NVARCHAR2(n), CHAR(n), NCHAR(n) | varchar(n), nvarchar(n), char(n), nchar(n) | CHAR | varchar(n) |
| CLOB, NCLOB, LONG, ROWID | varchar(max), nvarchar(max), text, ntext, xml | STRING | text |
| NUMBER(p) при p ≤ 4 / ≤ 9 / ≤ 18 | smallint / int / bigint | INT2 / INT4 / INT8 | smallint / integer / bigint |
| — | tinyint, bit | INT1 | smallint |
| NUMBER(p,s), NUMBER(p) при p > 18, NUMBER(\*,0) | decimal(p,s), numeric(p,s), money, smallmoney | DEC | numeric(p,s) |
| NUMBER без точности | — | NUMERIC | numeric |
| FLOAT, BINARY_FLOAT, BINARY_DOUBLE | float, real | FLTP | double precision |
| — | date | DATS | date |
| — | time | TIMS | time |
| DATE, TIMESTAMP(n) | datetime, datetime2, smalldatetime | TIMESTAMP | timestamp |
| TIMESTAMP WITH (LOCAL) TIME ZONE | datetimeoffset | TIMESTAMPTZ | timestamptz |
| RAW, LONG RAW, BLOB | binary, varbinary, image, rowversion | BINARY | bytea |
| — | uniqueidentifier | CHAR(36) | varchar(36) |
| прочие (XMLTYPE, SDO_GEOMETRY, INTERVAL, …) | прочие (sql_variant, geography, hierarchyid, …) | как в источнике | text, с предупреждением |

Нераспознанный тип можно задать в `type_rules.yaml` проекта: ключ — имя типа в верхнем регистре, например `SDO_GEOMETRY: "text"`.

Формат значений в CSV с данными: числа с точкой или запятой (для NUMERIC и FLTP допустима экспонента `1e3`), даты `ГГГГ-ММ-ДД` (а также `ГГГГММДД` и `ДД.ММ.ГГГГ`), время `ЧЧ:ММ:СС`, дата и время `ГГГГ-ММ-ДД ЧЧ:ММ:СС[.дробь][±ЧЧ:ММ]` (допустим `T` между датой и временем), двоичные данные — шестнадцатеричной строкой, можно с `0x`. Для Oracle перед выгрузкой данных стоит выполнить `ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS'` и `NLS_TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS.FF'`, иначе даты придут в формате сессии и загрузка их отклонит.

## Допущения первой версии

1. **Таблица источника переносится через пару DataSource + ADSO standard.** Так переиспользуются загрузка, прогон, журнал изменений и сверка без изменений в генераторе. Цена — у каждой таблицы с ключом в целевой базе три таблицы (входящая, активная, журнал изменений) плюс входная. Целевая таблица — `<префикс>_dwh.<схема>_<таблица>_active`. Если нужна одна таблица с исходным именем, это отдельная доработка генератора (своё имя в `naming.yaml` и вид ADSO без журнала).
2. **Имя объекта — `<СХЕМА>_<ТАБЛИЦА>`**, в базе в нижнем регистре. Входная таблица и ADSO называются одинаково, `load` и `run` по имени выбирают входную таблицу. Имена длиннее 63 символов (с суффиксом `_changelog`) дают предупреждение: Greenplum их обрежет.
3. **Колонки источника с именами `load_id`, `record_no`, `recordmode`** конфликтуют со служебными колонками и дают ошибку импорта.
4. **Ключ**: первичный, иначе первый по имени уникальный. Порядок колонок в PRIMARY KEY и ключ распределения (первая колонка ключа) — по порядку колонок в таблице, а не в ограничении.
5. **DATE в Oracle → timestamp**, потому что DATE хранит время. Если в DWH там только даты, тип меняется в `type_rules.yaml` проекта.
6. **NUMBER(10) → bigint**: 10 знаков не помещаются в integer.
7. **Числовые колонки считаются показателями** (как числовые поля без InfoObject в BW): сверка сравнивает их суммы, текстовые колонки вне ключа не сверяются.
8. **Внешние ключи, секционирование, индексы, последовательности, синонимы, гранты и значения по умолчанию не переносятся.** Внешние ключи и секционирование видны в отчёте о проверке импорта.
9. **Связи представлений с таблицами** найдены поиском имён таблиц в тексте представления — без разбора SQL, возможны лишние или пропущенные связи. Текст представлений и код процедур не переводится.
10. **Трудоёмкость**: представление 2 ч + 1 ч на каждые 50 строк, процедура 4 ч + 2 ч на каждые 50 строк (`effort_norms.yaml`). Нормы ориентировочные.
11. **Проверено** на синтетических словарях в `tests/data/` (модульные тесты), сквозным прогоном «развёртывание → загрузка → прогон → сверка» на Greengage 6.32 и PostgreSQL 16 (`tests/integration/test_dict_db.py`), а также разово импортом по подключению к Oracle Database 23ai Free и SQL Server 2022 в Docker. Запросы к словарю используют только то, что видно пользователю без привилегий DBA (`ALL_*`, `INFORMATION_SCHEMA`, `sys.sql_modules` с правом VIEW DEFINITION). На Oracle 11g/12c и SQL Server старше 2016 не проверялось.
12. **Объём**: всё читается в память; на словаре в десятки тысяч таблиц не проверялось.
