# Переводчик SQL и процедур

Модуль `dbtransfer.translate` переводит код источника в SQL целевой базы:

- **представления и запросы** Oracle, MS SQL Server (T-SQL) и Teradata — в `greenplum7` (ADB 7.5 / Greengage 7), `greenplum` (ADB 6.x, Yandex MPP) и `clickhouse`;
- **процедуры, функции, пакеты PL/SQL, процедуры T-SQL, макросы и процедуры SPL Teradata** — в черновики PL/pgSQL.

Разбор и генерация SQL сделаны на [sqlglot](https://github.com/tobymao/sqlglot); поверх него — правила, которых в sqlglot нет или которые зависят от типов колонок модели. Процедурный код разбирается собственным переводчиком операторов: каждый SQL-оператор внутри него переводится тем же переводчиком запросов.

Главное правило: **ничего не теряется молча**. То, что не переведено, остаётся в результате с пометкой `TODO` и попадает в отчёт о сложности.

## Где это видно

| Где | Что |
| --- | --- |
| `dbtransfer generate` | `080_views.sql` — представления, переведённые полностью; входят в `deploy_all.sql` и разворачиваются `dbtransfer deploy`. `drafts_views.sql` — представления с TODO, `drafts_routines.sql` — черновики процедур. Черновики в `deploy_all.sql` не входят: их дорабатывают и разворачивают вручную (`psql -f`) |
| `dbtransfer report`, Excel, веб-интерфейс | Доля переведённого автоматически (представления, операторы процедур), число TODO и остаток ручной работы в часах против полной ручной оценки |
| `dbtransfer translate файл.sql --from oracle\|tsql\|teradata [--dialect …]` | Перевод одного файла без проекта: результат и TODO на экран |

Перевод выполняется для моделей из словарей Oracle, MS SQL Server и Teradata. Для Oracle текст процедур берётся из `ALL_SOURCE` (колонки `LINE`, `TEXT`), для MS SQL — из `sys.sql_modules.definition` (колонка `DEFINITION` в выгрузке `ROUTINES`). Старая выгрузка без текста по-прежнему импортируется, но объекты без текста оцениваются как ручная работа.

## Статусы

| Статус | Что значит | Куда попадает |
| --- | --- | --- |
| `auto` | Переведено полностью, TODO нет | Представление — в `080_views.sql`; процедура — в `drafts_routines.sql` (черновик всё равно читает человек) |
| `partial` | Переведено с пометками TODO | `drafts_*.sql`, TODO — комментариями над объектом и в теле |
| `failed` | Не разобрано | `drafts_*.sql`, исходный текст закомментирован |

Представление, которое читает представление с TODO, тоже получает статус `partial`: развернуть его без зависимости нельзя.

## Представления и запросы

Имена таблиц источника заменяются именами в цели (`SALES.ORDERS` → `dbt_sales.orders`) по модели; таблица, которой нет в модели, остаётся как есть и помечается TODO.

| Конструкция | Перевод |
| --- | --- |
| Oracle `(+)` | `LEFT JOIN` |
| Oracle `CONNECT BY`, `START WITH`, `LEVEL`, `SYS_CONNECT_BY_PATH`, `CONNECT_BY_ROOT` | `WITH RECURSIVE`; `ORDER SIBLINGS BY`, `NOCYCLE`, `CONNECT_BY_ISLEAF` — TODO |
| Oracle `ROWNUM <= N` | `LIMIT N`; если запрос сортирует подзапрос, `LIMIT` переносится в него (в Greenplum порядок подзапроса теряется). `ROWNUM` вместе с `ORDER BY` на том же уровне — TODO: в Oracle он считается до сортировки |
| Oracle `ROWNUM` в списке колонок | `ROW_NUMBER() OVER ()` |
| Oracle `MERGE` | В Greenplum `MERGE` нет: `UPDATE … FROM`, `DELETE`, `INSERT … WHERE NOT EXISTS` |
| Oracle `DATE + n`, `DATE - DATE`, `ADD_MONTHS`, `MONTHS_BETWEEN`, `TRUNC(дата)`, `SYSDATE` | По типам колонок: `+ n * INTERVAL '1 day'`, разность в днях, `+ INTERVAL '1 month'`, `DATE_TRUNC`, `LOCALTIMESTAMP(0)` |
| Oracle `NVL`, `NVL2`, `DECODE`, `\|\|`, `TO_NUMBER`, `TO_CHAR`, `TO_DATE`, деление целых | `COALESCE`, `CASE`, `CONCAT` (в Oracle `NULL \|\| 'a'` = `'a'`), числовое приведение, форматы |
| T-SQL `TOP n`, `TOP n PERCENT` | `LIMIT n`; `PERCENT` — TODO |
| T-SQL `+` для строк, `ISNULL`, `GETDATE`, `DATEADD`, `DATEDIFF`, `CONVERT`, `CROSS/OUTER APPLY` | `\|\|`, `COALESCE`, `now()`, интервалы, `CAST`, `LATERAL` |
| T-SQL сравнение строк, `CHARINDEX`, `LIKE` | Нечувствительное к регистру (как у сортировки по умолчанию): `lower()`, `ILIKE` |
| Teradata `SEL`, `INS`, `UPD`, `DEL`, `QUALIFY` | `SELECT`, …; `QUALIFY` — подзапрос с оконной функцией |
| Teradata `ZEROIFNULL`, `NULLIFZERO`, `INDEX`, `OREPLACE`, `OTRANSLATE`, `CHARACTERS`, `CAST(… FORMAT …)`, `DATE` | `COALESCE`, `NULLIF`, `strpos`, `replace`, `translate`, `length`, `TO_CHAR`, `CURRENT_DATE` |
| Teradata `LOCKING … FOR ACCESS` | Убирается |
| Функция, которой нет в цели (`SYS_CONTEXT`, `REGEXP_SUBSTR`, `STUFF`, `NEWID`, `FORMAT`, `SAMPLE`, `HASHROW`, `PIVOT`, `FOR XML` …) | TODO с именем функции |
| Пользовательская функция источника | Новое имя функции в цели, если она переведена вместе с моделью; иначе TODO |

Для `clickhouse` генерируется SQL ClickHouse (`toStartOfDay`, `addMonths`, `dateDiff`); ClickHouse не исполняет процедуры, поэтому черновики PL/pgSQL для него не строятся.

## Процедуры

Черновик — это `CREATE OR REPLACE PROCEDURE … LANGUAGE plpgsql` для ADB 7 и `FUNCTION … RETURNS void` для ADB 6 (в Greenplum 6 процедур нет; вызов — `SELECT функция(…)`). В заголовке — число операторов и сколько из них переведено без TODO.

| Источник | Что переводится |
| --- | --- |
| PL/SQL | Переменные и `%TYPE`, `IF/ELSIF`, `CASE`, циклы `LOOP/WHILE/FOR`, курсоры (`CURSOR`, `FOR r IN c`, `OPEN/FETCH/CLOSE`), `SELECT … INTO` (→ `INTO STRICT`), `SQL%ROWCOUNT`, `%FOUND/%NOTFOUND`, `EXCEPTION WHEN` (имена исключений PostgreSQL), `RAISE_APPLICATION_ERROR`, `DBMS_OUTPUT`, `EXECUTE IMMEDIATE`, `COMMIT/ROLLBACK`. Пакет разбирается на члены `пакет__член` |
| T-SQL | `@параметры` и `DECLARE`, `SET`, `SELECT @v = …`, `IF/ELSE`, `WHILE`, `BEGIN TRY/CATCH` (→ `EXCEPTION WHEN OTHERS`), курсоры и `@@FETCH_STATUS`, `@@ROWCOUNT`, `#временные` таблицы (→ `TEMP`), `PRINT`, `RAISERROR`, `THROW`, `EXEC` процедуры |
| Teradata | Макросы (→ функция; `:параметр` → `p_параметр`), процедуры SPL: `DECLARE`, `SET`, `IF`, `WHILE`, `FOR … AS … CURSOR FOR … DO`, `LEAVE/ITERATE`, метки |
| Все | `OUT`-параметры процедур становятся `INOUT` (в PostgreSQL 12 у процедур нет `OUT`); параметры после параметра со значением по умолчанию получают `DEFAULT NULL` |

TODO ставится на то, что нельзя перевести без человека: коллекции и записи PL/SQL (`TABLE OF`, `BULK COLLECT`, `FORALL`), динамический SQL T-SQL (`sp_executesql`), процедуры, возвращающие набор строк, табличные переменные, `RETURNS TABLE`, триггеры, `GOTO`, автономные транзакции.

## Оценка трудоёмкости

Нормы — в `dbtransfer/rules/effort_norms.yaml`: представление, переведённое автоматически, — 0,25 ч на проверку на данных (+0,1 ч на каждые 50 строк), черновик процедуры — 1 ч на чтение и проверку (+0,5 ч на 50 строк), каждый TODO — 0,5 ч. Остаток не превышает полной ручной оценки объекта. Отчёт показывает обе цифры: сколько было бы вручную и сколько осталось после перевода.

## Проверка

- `tests/unit/test_translate.py` — конструкции, статусы набора, пакет, оценка, команда `translate`.
- `tests/sql_corpus.py` — набор представлений и процедур: 28 Oracle, 27 T-SQL, 14 Teradata; для каждого указано, ожидается ли полный перевод.
- `tests/integration/test_translate_db.py` (`-m db`) — представления и процедуры создаются в Oracle Database Free 23 и MS SQL Server 2022, переводятся, разворачиваются в Greengage 7.5.1 и 6.32.0; строки представлений, результаты функций и таблицы после вызова процедур сравниваются с источником. Teradata сравнивается с эталонными запросами на целевой базе. В CI — задание `translate`.

Результат на наборе: Oracle — 24 из 28 представлений полностью (86 %), MS SQL — 23 из 27 (85 %), Teradata — 12 из 14; все полностью переведённые совпали с источником по строкам. Процедуры без коллекций и динамического SQL переводятся без TODO и дают те же данные, что в источнике.

## Допущения

- Порядок строк без `ORDER BY` не сравнивается: в MPP он не определён.
- Сравнение строк T-SQL переводится как нечувствительное к регистру; если в источнике сортировка `_CS_`, уберите `lower()`/`ILIKE`.
- `SELECT … INTO` из PL/SQL — `INTO STRICT`: как и в Oracle, ни одной или две строки — ошибка (`NO_DATA_FOUND`, `TOO_MANY_ROWS`).
- `MERGE` при повторах ключа в источнике падает, `UPDATE … FROM` берёт любую строку.
- Teradata проверяется только на синтетике: публичного образа нет.
