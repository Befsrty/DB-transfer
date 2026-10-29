-- Трансформации
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект greenplum.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

-- Трансформация ZSALES_FF → ZSD_A01 (0B6F3C1A9D2E4F5A8B7C6D5E4F3A2B10)
-- Дельта: записи источника с номером загрузки p_load_id
CREATE OR REPLACE FUNCTION dbt_dwh.tr_zsales_ff__zsd_a01(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows bigint;
BEGIN
    INSERT INTO dbt_stg.zsd_a01_inbound (load_id, record_no, recordmode, doc_number, s_ord_item, sold_to, material, distr_chan, calday, netval_inv, doc_currcy, inv_qty, sales_unit)
    SELECT
        p_load_id,
        s.record_no,
        ' ',
        coalesce(s.doc_number, ''),  -- 0DOC_NUMBER ← прямое присвоение DOC_NUMBER
        coalesce(s.s_ord_item, '000000'),  -- 0S_ORD_ITEM ← прямое присвоение S_ORD_ITEM
        s.sold_to,  -- 0SOLD_TO ← прямое присвоение SOLD_TO
        s.material,  -- 0MATERIAL ← прямое присвоение MATERIAL
        '10',  -- 0DISTR_CHAN ← константа '10'
        s.calday,  -- 0CALDAY ← прямое присвоение CALDAY
        s.netval - s.discount,  -- 0NETVAL_INV ← формула NETVAL - DISCOUNT
        s.currency,  -- 0DOC_CURRCY ← прямое присвоение CURRENCY
        s.qty,  -- 0INV_QTY ← прямое присвоение QTY
        s.unit  -- 0SALES_UNIT ← прямое присвоение UNIT
    FROM dbt_stg.zsales_ff s
    WHERE s.load_id = p_load_id;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_dwh.tr_zsales_ff__zsd_a01(bigint) IS 'BW: трансформация ZSALES_FF → ZSD_A01 (0B6F3C1A9D2E4F5A8B7C6D5E4F3A2B10), DTP DTP_00ZSALESFF0ZSDA01000000';

-- Трансформация ZSD_A01 → ZSD_A02 (1C7A4D2B0E3F5A6B9C8D7E6F5A4B3C21)
-- Дельта: записи источника с номером загрузки p_load_id
-- Источник — журнал изменений: before image (X) с минусом снимает старые суммы
CREATE OR REPLACE FUNCTION dbt_mart.tr_zsd_a01__zsd_a02(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows bigint;
BEGIN
    INSERT INTO dbt_stg.zsd_a02_inbound (load_id, record_no, recordmode, sold_to, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
    SELECT
        p_load_id,
        s.record_no,
        s.recordmode,
        coalesce(s.sold_to, ''),  -- 0SOLD_TO ← прямое присвоение 0SOLD_TO
        coalesce(to_char(s.calday, 'YYYYMM'), '000000'),  -- 0CALMONTH ← преобразование времени 0CALDAY → 0CALMONTH
        coalesce(s.doc_currcy, ''),  -- 0DOC_CURRCY ← прямое присвоение 0DOC_CURRCY
        coalesce(s.sales_unit, ''),  -- 0SALES_UNIT ← прямое присвоение 0SALES_UNIT
        s.netval_inv,  -- 0NETVAL_INV ← прямое присвоение 0NETVAL_INV
        s.inv_qty  -- 0INV_QTY ← прямое присвоение 0INV_QTY
    FROM dbt_dwh.zsd_a01_changelog s
    WHERE s.load_id = p_load_id;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_mart.tr_zsd_a01__zsd_a02(bigint) IS 'BW: трансформация ZSD_A01 → ZSD_A02 (1C7A4D2B0E3F5A6B9C8D7E6F5A4B3C21), DTP DTP_01ZSDA010ZSDA020000000';

-- Трансформация ZSD_A01 → ZSD_A03 (2D8B5E3C1F4A6B7C0D9E8F7A6B5C4D32)
-- Дельта: записи источника с номером загрузки p_load_id
-- Цель с перезаписью: из журнала изменений берутся новые записи и after image, before image (X) пропускаются
--
-- ЗАГОТОВКА: требует ручной доработки. Причины:
--   - конечная рутина ABAP
-- Прогон через эту трансформацию заблокирован. Черновик INSERT по поддерживаемым правилам:
--
-- INSERT INTO dbt_stg.zsd_a03_inbound (load_id, record_no, recordmode, sold_to, material, calday, netval_inv, doc_currcy)
-- SELECT
--     p_load_id,
--     s.record_no,
--     s.recordmode,
--     coalesce(s.sold_to, ''),  -- 0SOLD_TO ← прямое присвоение 0SOLD_TO
--     coalesce(s.material, ''),  -- 0MATERIAL ← прямое присвоение 0MATERIAL
--     coalesce(s.calday, DATE '0001-01-01'),  -- 0CALDAY ← прямое присвоение 0CALDAY
--     s.netval_inv,  -- 0NETVAL_INV ← прямое присвоение 0NETVAL_INV
--     s.doc_currcy  -- 0DOC_CURRCY ← прямое присвоение 0DOC_CURRCY
-- FROM dbt_dwh.zsd_a01_changelog s
-- WHERE s.load_id = p_load_id
--   AND s.recordmode <> 'X';
--
-- Рутина ABAP: конечная рутина
-- METHOD end_routine.
-- * Убираем строки с нулевой выручкой и пересчитываем EUR в RUB по плановому курсу
--   DELETE RESULT_PACKAGE WHERE netval_inv = 0.
--   LOOP AT RESULT_PACKAGE ASSIGNING <result_fields>.
--     IF <result_fields>-doc_currcy = 'EUR'.
--       <result_fields>-netval_inv = <result_fields>-netval_inv * '95.50'.
--       <result_fields>-doc_currcy = 'RUB'.
--     ENDIF.
--   ENDLOOP.
-- ENDMETHOD.
CREATE OR REPLACE FUNCTION dbt_dwh.tr_zsd_a01__zsd_a03(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows bigint;
BEGIN
    RAISE EXCEPTION 'Трансформация ZSD_A01 → ZSD_A03 требует ручной доработки: конечная рутина ABAP';
    RETURN 0;
END;
$$;
COMMENT ON FUNCTION dbt_dwh.tr_zsd_a01__zsd_a03(bigint) IS 'BW: трансформация ZSD_A01 → ZSD_A03 (2D8B5E3C1F4A6B7C0D9E8F7A6B5C4D32), DTP DTP_02ZSDA010ZSDA030000000. ЗАГОТОВКА: требует ручной доработки: конечная рутина ABAP';

-- Трансформация ZSD_A01 → ZSD_A04 (3E9C6F4D2A5B7C8D1E0F9A8B7C6D5E43)
-- Полная загрузка: все данные источника пишутся одной новой загрузкой p_load_id
-- Атрибут 0MATL_GROUP читается из справочника 0MATERIAL на момент прогона, а не на момент загрузки
CREATE OR REPLACE FUNCTION dbt_mart.tr_zsd_a01__zsd_a04(p_load_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows bigint;
BEGIN
    INSERT INTO dbt_stg.zsd_a04_inbound (load_id, record_no, recordmode, matl_group, calmonth, doc_currcy, sales_unit, netval_inv, inv_qty)
    SELECT
        p_load_id,
        row_number() OVER (ORDER BY s.doc_number, s.s_ord_item),
        ' ',
        coalesce(m1.matl_group, ''),  -- 0MATL_GROUP ← чтение атрибута 0MATERIAL-0MATL_GROUP
        coalesce(to_char(s.calday, 'YYYYMM'), '000000'),  -- 0CALMONTH ← преобразование времени 0CALDAY → 0CALMONTH
        coalesce(s.doc_currcy, ''),  -- 0DOC_CURRCY ← прямое присвоение 0DOC_CURRCY
        coalesce(s.sales_unit, ''),  -- 0SALES_UNIT ← прямое присвоение 0SALES_UNIT
        s.netval_inv,  -- 0NETVAL_INV ← прямое присвоение 0NETVAL_INV
        s.inv_qty  -- 0INV_QTY ← прямое присвоение 0INV_QTY
    FROM dbt_dwh.zsd_a01_active s
    LEFT JOIN dbt_md.material_attr m1
      ON m1.material = s.material;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END;
$$;
COMMENT ON FUNCTION dbt_mart.tr_zsd_a01__zsd_a04(bigint) IS 'BW: трансформация ZSD_A01 → ZSD_A04 (3E9C6F4D2A5B7C8D1E0F9A8B7C6D5E43), DTP DTP_03ZSDA010ZSDA040000000';
