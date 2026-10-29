-- Функции активации ADSO
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект greenplum.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

-- Активация ADSO ZSD_A01 (standard) по ТЗ 4.3.5: из дублей ключа берётся запись с наибольшим record_no;
-- новый ключ — вставка и N в журнал, существующий — пара X / ' ' в журнал и замена строки.
CREATE OR REPLACE FUNCTION dbt_dwh.activate_zsd_a01(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_bad bigint;
    v_changed bigint;
    v_rows bigint;
BEGIN
    SELECT count(*) INTO v_bad
    FROM dbt_stg.zsd_a01_inbound
    WHERE load_id = p_load_id AND recordmode IN ('D', 'R');
    IF v_bad > 0 THEN
        RAISE EXCEPTION 'ZSD_A01: в загрузке % есть записи с recordmode D или R (%), удаления и обратные записи не поддерживаются',
            p_load_id, v_bad;
    END IF;

    -- Before image (X): старые значения, показатели с минусом
    INSERT INTO dbt_dwh.zsd_a01_changelog (load_id, record_no, recordmode, doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
    SELECT p_load_id, d.record_no, 'X', a.doc_number, a.s_ord_item, a.sold_to, a.material, a.distr_chan, a.calday, -a.netval_inv, a.doc_currcy, -a.inv_qty, a.sales_unit
    FROM dbt_dwh.zsd_a01_active a
    JOIN (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.doc_number, i.s_ord_item ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a01_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d
      ON a.doc_number = d.doc_number AND a.s_ord_item = d.s_ord_item;
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    -- After image (' ') для существующих ключей и N для новых
    INSERT INTO dbt_dwh.zsd_a01_changelog (load_id, record_no, recordmode, doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
    SELECT p_load_id, d.record_no, CASE WHEN a.doc_number IS NULL THEN 'N' ELSE ' ' END,
           d.doc_number, d.s_ord_item, d.sold_to, d.material, d.distr_chan, d.calday, d.netval_inv, d.doc_currcy, d.inv_qty, d.sales_unit
    FROM (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.doc_number, i.s_ord_item ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a01_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d
    LEFT JOIN dbt_dwh.zsd_a01_active a
      ON a.doc_number = d.doc_number AND a.s_ord_item = d.s_ord_item;

    -- Замена строк активных данных
    DELETE FROM dbt_dwh.zsd_a01_active a
    USING (SELECT DISTINCT doc_number, s_ord_item FROM dbt_stg.zsd_a01_inbound WHERE load_id = p_load_id) d
    WHERE a.doc_number = d.doc_number AND a.s_ord_item = d.s_ord_item;

    INSERT INTO dbt_dwh.zsd_a01_active (doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
    SELECT d.doc_number, d.s_ord_item, d.sold_to, d.material, d.distr_chan, d.calday, d.netval_inv, d.doc_currcy, d.inv_qty, d.sales_unit
    FROM (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.doc_number, i.s_ord_item ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a01_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d;
    GET DIAGNOSTICS v_rows = ROW_COUNT;

    UPDATE dbt_meta.load_log
    SET message = concat_ws('; ', nullif(message, ''),
        format('ZSD_A01: активировано %s записей, новых %s, изменённых %s', v_rows, v_rows - v_changed, v_changed))
    WHERE load_id = p_load_id;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_dwh.activate_zsd_a01(bigint) IS 'BW: активация ADSO ZSD_A01 (standard): перезапись и журнал изменений';

-- Активация ADSO ZSD_A02 (cumulative) по ТЗ 4.3.5: показатели загрузки суммируются по ключу
-- и прибавляются к активным данным, новый ключ вставляется. Строки с нулевыми суммами остаются.
CREATE OR REPLACE FUNCTION dbt_mart.activate_zsd_a02(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_changed bigint := 0;
    v_new bigint;
BEGIN
    UPDATE dbt_mart.zsd_a02_active a
    SET netval_inv = coalesce(a.netval_inv, 0) + coalesce(g.netval_inv, 0),
        inv_qty = coalesce(a.inv_qty, 0) + coalesce(g.inv_qty, 0)
    FROM (SELECT i.sold_to, i.calmonth, i.doc_currcy, i.sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a02_inbound i WHERE i.load_id = p_load_id GROUP BY i.sold_to, i.calmonth, i.doc_currcy, i.sales_unit) g
    WHERE a.sold_to = g.sold_to AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit;
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    INSERT INTO dbt_mart.zsd_a02_active (sold_to, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
    SELECT g.sold_to, g.calmonth, g.doc_currcy, g.sales_unit, g.netval_inv, g.inv_qty
    FROM (SELECT i.sold_to, i.calmonth, i.doc_currcy, i.sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a02_inbound i WHERE i.load_id = p_load_id GROUP BY i.sold_to, i.calmonth, i.doc_currcy, i.sales_unit) g
    WHERE NOT EXISTS (SELECT 1 FROM dbt_mart.zsd_a02_active a WHERE a.sold_to = g.sold_to AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit);
    GET DIAGNOSTICS v_new = ROW_COUNT;

    UPDATE dbt_meta.load_log
    SET message = concat_ws('; ', nullif(message, ''),
        format('ZSD_A02: строк новых %s, изменённых %s', v_new, v_changed))
    WHERE load_id = p_load_id;
    RETURN v_new + v_changed;
END;
$$;
COMMENT ON FUNCTION dbt_mart.activate_zsd_a02(bigint) IS 'BW: активация ADSO ZSD_A02 (cumulative): суммирование';

-- Активация ADSO ZSD_A03 (standard) по ТЗ 4.3.5: из дублей ключа берётся запись с наибольшим record_no;
-- новый ключ — вставка и N в журнал, существующий — пара X / ' ' в журнал и замена строки.
CREATE OR REPLACE FUNCTION dbt_dwh.activate_zsd_a03(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_bad bigint;
    v_changed bigint;
    v_rows bigint;
BEGIN
    SELECT count(*) INTO v_bad
    FROM dbt_stg.zsd_a03_inbound
    WHERE load_id = p_load_id AND recordmode IN ('D', 'R');
    IF v_bad > 0 THEN
        RAISE EXCEPTION 'ZSD_A03: в загрузке % есть записи с recordmode D или R (%), удаления и обратные записи не поддерживаются',
            p_load_id, v_bad;
    END IF;

    -- Before image (X): старые значения, показатели с минусом
    INSERT INTO dbt_dwh.zsd_a03_changelog (load_id, record_no, recordmode, sold_to, material, calday, netval_inv, doc_currcy)
    SELECT p_load_id, d.record_no, 'X', a.sold_to, a.material, a.calday, -a.netval_inv, a.doc_currcy
    FROM dbt_dwh.zsd_a03_active a
    JOIN (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.sold_to, i.material, i.calday ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a03_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d
      ON a.sold_to = d.sold_to AND a.material = d.material AND a.calday = d.calday;
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    -- After image (' ') для существующих ключей и N для новых
    INSERT INTO dbt_dwh.zsd_a03_changelog (load_id, record_no, recordmode, sold_to, material, calday, netval_inv, doc_currcy)
    SELECT p_load_id, d.record_no, CASE WHEN a.sold_to IS NULL THEN 'N' ELSE ' ' END,
           d.sold_to, d.material, d.calday, d.netval_inv, d.doc_currcy
    FROM (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.sold_to, i.material, i.calday ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a03_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d
    LEFT JOIN dbt_dwh.zsd_a03_active a
      ON a.sold_to = d.sold_to AND a.material = d.material AND a.calday = d.calday;

    -- Замена строк активных данных
    DELETE FROM dbt_dwh.zsd_a03_active a
    USING (SELECT DISTINCT sold_to, material, calday FROM dbt_stg.zsd_a03_inbound WHERE load_id = p_load_id) d
    WHERE a.sold_to = d.sold_to AND a.material = d.material AND a.calday = d.calday;

    INSERT INTO dbt_dwh.zsd_a03_active (sold_to, material, calday, netval_inv, doc_currcy)
    SELECT d.sold_to, d.material, d.calday, d.netval_inv, d.doc_currcy
    FROM (SELECT * FROM (SELECT i.*, row_number() OVER (PARTITION BY i.sold_to, i.material, i.calday ORDER BY i.record_no DESC) AS rn FROM dbt_stg.zsd_a03_inbound i WHERE i.load_id = p_load_id) x WHERE x.rn = 1) d;
    GET DIAGNOSTICS v_rows = ROW_COUNT;

    UPDATE dbt_meta.load_log
    SET message = concat_ws('; ', nullif(message, ''),
        format('ZSD_A03: активировано %s записей, новых %s, изменённых %s', v_rows, v_rows - v_changed, v_changed))
    WHERE load_id = p_load_id;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_dwh.activate_zsd_a03(bigint) IS 'BW: активация ADSO ZSD_A03 (standard): перезапись и журнал изменений';

-- Активация ADSO ZSD_A04 (cumulative) по ТЗ 4.3.5: показатели загрузки суммируются по ключу
-- и прибавляются к активным данным, новый ключ вставляется. Строки с нулевыми суммами остаются.
CREATE OR REPLACE FUNCTION dbt_mart.activate_zsd_a04(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_changed bigint := 0;
    v_new bigint;
BEGIN
    UPDATE dbt_mart.zsd_a04_active a
    SET netval_inv = coalesce(a.netval_inv, 0) + coalesce(g.netval_inv, 0),
        inv_qty = coalesce(a.inv_qty, 0) + coalesce(g.inv_qty, 0)
    FROM (SELECT i.matl_group, i.calmonth, i.doc_currcy, i.sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a04_inbound i WHERE i.load_id = p_load_id GROUP BY i.matl_group, i.calmonth, i.doc_currcy, i.sales_unit) g
    WHERE a.matl_group = g.matl_group AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit;
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    INSERT INTO dbt_mart.zsd_a04_active (matl_group, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
    SELECT g.matl_group, g.calmonth, g.doc_currcy, g.sales_unit, g.netval_inv, g.inv_qty
    FROM (SELECT i.matl_group, i.calmonth, i.doc_currcy, i.sales_unit, sum(i.netval_inv) AS netval_inv, sum(i.inv_qty) AS inv_qty FROM dbt_stg.zsd_a04_inbound i WHERE i.load_id = p_load_id GROUP BY i.matl_group, i.calmonth, i.doc_currcy, i.sales_unit) g
    WHERE NOT EXISTS (SELECT 1 FROM dbt_mart.zsd_a04_active a WHERE a.matl_group = g.matl_group AND a.calmonth = g.calmonth AND a.doc_currcy = g.doc_currcy AND a.sales_unit = g.sales_unit);
    GET DIAGNOSTICS v_new = ROW_COUNT;

    UPDATE dbt_meta.load_log
    SET message = concat_ws('; ', nullif(message, ''),
        format('ZSD_A04: строк новых %s, изменённых %s', v_new, v_changed))
    WHERE load_id = p_load_id;
    RETURN v_new + v_changed;
END;
$$;
COMMENT ON FUNCTION dbt_mart.activate_zsd_a04(bigint) IS 'BW: активация ADSO ZSD_A04 (cumulative): суммирование';
