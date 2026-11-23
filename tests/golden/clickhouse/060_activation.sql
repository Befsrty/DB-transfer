-- Активация ADSO: пошаговые запросы, выполняются при прогоне
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект clickhouse.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

-- Активация ADSO ZSD_A01 (standard) по ТЗ 4.3.5: из дублей ключа берётся запись с наибольшим record_no;
-- новый ключ — вставка и N в журнал, существующий — пара X / ' ' в журнал и замена строки.
-- В ClickHouse нет процедур и транзакций: вместо функции PL/pgSQL — шаги, которые прогон выполняет
-- по очереди с параметром {p_load_id:Int64}. Если шаг упал, строки загрузки в журнале изменений
-- нужно удалить и повторить активацию целиком.
-- Шаг 1. проверка: удаления и обратные записи (recordmode D, R) не поддерживаются
SELECT throwIf(count() > 0, 'ZSD_A01: в загрузке есть записи с recordmode D или R, удаления и обратные записи не поддерживаются')
FROM dbt_stg.zsd_a01_inbound
WHERE load_id = {p_load_id:Int64} AND recordmode IN ('D', 'R');
-- Шаг 2. before image (X) в журнал изменений: старые значения, показатели с минусом
INSERT INTO dbt_dwh.zsd_a01_changelog (load_id, record_no, recordmode, doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
SELECT {p_load_id:Int64}, d.record_no, 'X', a.doc_number, a.s_ord_item, a.sold_to, a.material, a.distr_chan, a.calday, -a.netval_inv, a.doc_currcy, -a.inv_qty, a.sales_unit
FROM dbt_dwh.zsd_a01_active a
INNER JOIN (SELECT * FROM dbt_stg.zsd_a01_inbound WHERE load_id = {p_load_id:Int64} ORDER BY record_no DESC LIMIT 1 BY doc_number, s_ord_item) d
  ON a.doc_number = d.doc_number AND a.s_ord_item = d.s_ord_item;
-- Шаг 3. after image (' ') для существующих ключей и N для новых
INSERT INTO dbt_dwh.zsd_a01_changelog (load_id, record_no, recordmode, doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
SELECT {p_load_id:Int64}, d.record_no, if(coalesce(a._hit, 0) = 1, ' ', 'N'),
       d.doc_number, d.s_ord_item, d.sold_to, d.material, d.distr_chan, d.calday, d.netval_inv, d.doc_currcy, d.inv_qty, d.sales_unit
FROM (SELECT * FROM dbt_stg.zsd_a01_inbound WHERE load_id = {p_load_id:Int64} ORDER BY record_no DESC LIMIT 1 BY doc_number, s_ord_item) d
LEFT JOIN (SELECT doc_number, s_ord_item, 1 AS _hit FROM dbt_dwh.zsd_a01_active) a
  ON a.doc_number = d.doc_number AND a.s_ord_item = d.s_ord_item;
-- Шаг 4. удаление заменяемых строк активных данных (lightweight DELETE)
DELETE FROM dbt_dwh.zsd_a01_active
WHERE (doc_number, s_ord_item) IN (SELECT doc_number, s_ord_item FROM dbt_stg.zsd_a01_inbound WHERE load_id = {p_load_id:Int64});
-- Шаг 5. новые строки активных данных
INSERT INTO dbt_dwh.zsd_a01_active (doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
SELECT d.doc_number, d.s_ord_item, d.sold_to, d.material, d.distr_chan, d.calday, d.netval_inv, d.doc_currcy, d.inv_qty, d.sales_unit
FROM (SELECT * FROM dbt_stg.zsd_a01_inbound WHERE load_id = {p_load_id:Int64} ORDER BY record_no DESC LIMIT 1 BY doc_number, s_ord_item) d;
-- Шаг 6. итог для журнала загрузок: активировано, новых, изменённых
SELECT countIf(recordmode <> 'X') AS activated, countIf(recordmode = 'N') AS new_rows,
       countIf(recordmode = 'X') AS changed_rows
FROM dbt_dwh.zsd_a01_changelog
WHERE load_id = {p_load_id:Int64};

-- Активация ADSO ZSD_A02 (cumulative) по ТЗ 4.3.5: показатели загрузки суммируются по ключу
-- и прибавляются к активным данным, новый ключ вставляется. Строки с нулевыми суммами остаются.
-- В ClickHouse нет UPDATE … FROM и транзакций: новые значения сначала собираются в рабочей таблице
-- dbt_stg.zsd_a02_work, затем старые строки удаляются и вставляются новые. Шаги выполняются по очереди
-- с параметром {p_load_id:Int64}; после сбоя активацию можно повторить с первого шага,
-- пока не выполнен шаг удаления.
-- Шаг 1. очистка рабочей таблицы
TRUNCATE TABLE IF EXISTS dbt_stg.zsd_a02_work;
-- Шаг 2. новые значения: сумма загрузки плюс активные данные; _hit = 1 — ключ уже был
INSERT INTO dbt_stg.zsd_a02_work (_hit, sold_to, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
SELECT coalesce(a._hit, 0), g.sold_to, g.calmonth, g.doc_currcy, g.sales_unit, if(coalesce(a._hit, 0) = 1, coalesce(a.netval_inv, 0) + coalesce(g.netval_inv, 0), g.netval_inv), if(coalesce(a._hit, 0) = 1, coalesce(a.inv_qty, 0) + coalesce(g.inv_qty, 0), g.inv_qty)
FROM (SELECT i.sold_to AS sold_to, i.calmonth AS calmonth, i.doc_currcy AS doc_currcy, i.sales_unit AS sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a02_inbound i WHERE i.load_id = {p_load_id:Int64} GROUP BY i.sold_to, i.calmonth, i.doc_currcy, i.sales_unit) g
LEFT JOIN (SELECT sold_to, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty, 1 AS _hit FROM dbt_mart.zsd_a02_active) a
  ON a.sold_to = g.sold_to AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit;
-- Шаг 3. удаление изменяемых строк активных данных (lightweight DELETE)
DELETE FROM dbt_mart.zsd_a02_active
WHERE (sold_to, calmonth, doc_currcy, sales_unit) IN (SELECT sold_to, calmonth, doc_currcy, sales_unit FROM dbt_stg.zsd_a02_work WHERE _hit = 1);
-- Шаг 4. запись новых значений в активные данные
INSERT INTO dbt_mart.zsd_a02_active (sold_to, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
SELECT sold_to, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty
FROM dbt_stg.zsd_a02_work;
-- Шаг 5. итог для журнала загрузок: новых и изменённых строк
SELECT countIf(_hit = 0) AS new_rows, countIf(_hit = 1) AS changed_rows
FROM dbt_stg.zsd_a02_work;

-- Активация ADSO ZSD_A03 (standard) по ТЗ 4.3.5: из дублей ключа берётся запись с наибольшим record_no;
-- новый ключ — вставка и N в журнал, существующий — пара X / ' ' в журнал и замена строки.
-- В ClickHouse нет процедур и транзакций: вместо функции PL/pgSQL — шаги, которые прогон выполняет
-- по очереди с параметром {p_load_id:Int64}. Если шаг упал, строки загрузки в журнале изменений
-- нужно удалить и повторить активацию целиком.
-- Шаг 1. проверка: удаления и обратные записи (recordmode D, R) не поддерживаются
SELECT throwIf(count() > 0, 'ZSD_A03: в загрузке есть записи с recordmode D или R, удаления и обратные записи не поддерживаются')
FROM dbt_stg.zsd_a03_inbound
WHERE load_id = {p_load_id:Int64} AND recordmode IN ('D', 'R');
-- Шаг 2. before image (X) в журнал изменений: старые значения, показатели с минусом
INSERT INTO dbt_dwh.zsd_a03_changelog (load_id, record_no, recordmode, sold_to, material, calday, netval_inv, doc_currcy)
SELECT {p_load_id:Int64}, d.record_no, 'X', a.sold_to, a.material, a.calday, -a.netval_inv, a.doc_currcy
FROM dbt_dwh.zsd_a03_active a
INNER JOIN (SELECT * FROM dbt_stg.zsd_a03_inbound WHERE load_id = {p_load_id:Int64} ORDER BY record_no DESC LIMIT 1 BY sold_to, material, calday) d
  ON a.sold_to = d.sold_to AND a.material = d.material AND a.calday = d.calday;
-- Шаг 3. after image (' ') для существующих ключей и N для новых
INSERT INTO dbt_dwh.zsd_a03_changelog (load_id, record_no, recordmode, sold_to, material, calday, netval_inv, doc_currcy)
SELECT {p_load_id:Int64}, d.record_no, if(coalesce(a._hit, 0) = 1, ' ', 'N'),
       d.sold_to, d.material, d.calday, d.netval_inv, d.doc_currcy
FROM (SELECT * FROM dbt_stg.zsd_a03_inbound WHERE load_id = {p_load_id:Int64} ORDER BY record_no DESC LIMIT 1 BY sold_to, material, calday) d
LEFT JOIN (SELECT sold_to, material, calday, 1 AS _hit FROM dbt_dwh.zsd_a03_active) a
  ON a.sold_to = d.sold_to AND a.material = d.material AND a.calday = d.calday;
-- Шаг 4. удаление заменяемых строк активных данных (lightweight DELETE)
DELETE FROM dbt_dwh.zsd_a03_active
WHERE (sold_to, material, calday) IN (SELECT sold_to, material, calday FROM dbt_stg.zsd_a03_inbound WHERE load_id = {p_load_id:Int64});
-- Шаг 5. новые строки активных данных
INSERT INTO dbt_dwh.zsd_a03_active (sold_to, material, calday, netval_inv, doc_currcy)
SELECT d.sold_to, d.material, d.calday, d.netval_inv, d.doc_currcy
FROM (SELECT * FROM dbt_stg.zsd_a03_inbound WHERE load_id = {p_load_id:Int64} ORDER BY record_no DESC LIMIT 1 BY sold_to, material, calday) d;
-- Шаг 6. итог для журнала загрузок: активировано, новых, изменённых
SELECT countIf(recordmode <> 'X') AS activated, countIf(recordmode = 'N') AS new_rows,
       countIf(recordmode = 'X') AS changed_rows
FROM dbt_dwh.zsd_a03_changelog
WHERE load_id = {p_load_id:Int64};

-- Активация ADSO ZSD_A04 (cumulative) по ТЗ 4.3.5: показатели загрузки суммируются по ключу
-- и прибавляются к активным данным, новый ключ вставляется. Строки с нулевыми суммами остаются.
-- В ClickHouse нет UPDATE … FROM и транзакций: новые значения сначала собираются в рабочей таблице
-- dbt_stg.zsd_a04_work, затем старые строки удаляются и вставляются новые. Шаги выполняются по очереди
-- с параметром {p_load_id:Int64}; после сбоя активацию можно повторить с первого шага,
-- пока не выполнен шаг удаления.
-- Шаг 1. очистка рабочей таблицы
TRUNCATE TABLE IF EXISTS dbt_stg.zsd_a04_work;
-- Шаг 2. новые значения: сумма загрузки плюс активные данные; _hit = 1 — ключ уже был
INSERT INTO dbt_stg.zsd_a04_work (_hit, matl_group, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
SELECT coalesce(a._hit, 0), g.matl_group, g.calmonth, g.doc_currcy, g.sales_unit, if(coalesce(a._hit, 0) = 1, coalesce(a.netval_inv, 0) + coalesce(g.netval_inv, 0), g.netval_inv), if(coalesce(a._hit, 0) = 1, coalesce(a.inv_qty, 0) + coalesce(g.inv_qty, 0), g.inv_qty)
FROM (SELECT i.matl_group AS matl_group, i.calmonth AS calmonth, i.doc_currcy AS doc_currcy, i.sales_unit AS sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a04_inbound i WHERE i.load_id = {p_load_id:Int64} GROUP BY i.matl_group, i.calmonth, i.doc_currcy, i.sales_unit) g
LEFT JOIN (SELECT matl_group, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty, 1 AS _hit FROM dbt_mart.zsd_a04_active) a
  ON a.matl_group = g.matl_group AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit;
-- Шаг 3. удаление изменяемых строк активных данных (lightweight DELETE)
DELETE FROM dbt_mart.zsd_a04_active
WHERE (matl_group, calmonth, doc_currcy, sales_unit) IN (SELECT matl_group, calmonth, doc_currcy, sales_unit FROM dbt_stg.zsd_a04_work WHERE _hit = 1);
-- Шаг 4. запись новых значений в активные данные
INSERT INTO dbt_mart.zsd_a04_active (matl_group, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
SELECT matl_group, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty
FROM dbt_stg.zsd_a04_work;
-- Шаг 5. итог для журнала загрузок: новых и изменённых строк
SELECT countIf(_hit = 0) AS new_rows, countIf(_hit = 1) AS changed_rows
FROM dbt_stg.zsd_a04_work;
