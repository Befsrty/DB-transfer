"""Синтетические выгрузки словаря Oracle, MS SQL Server, Teradata и каталога Greenplum 6 для тестов
(tests/data/oracle_dict, mssql_dict, greenplum_dict, teradata_dict).

Запуск: uv run python tests/data/make_dict_samples.py — файлы перезаписываются.
"""

import csv
from pathlib import Path

HERE = Path(__file__).parent


def write(folder: Path, name: str, header: list[str], rows: list[list]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def oracle() -> None:
    d = HERE / "oracle_dict"
    write(d, "ALL_TABLES", ["OWNER", "TABLE_NAME", "PARTITIONED", "NUM_ROWS", "TEMPORARY"], [
        ["SALES", "CUSTOMERS", "NO", "1200", "N"],
        ["SALES", "ORDERS", "YES", "150000", "N"],
        ["SALES", "ORDER_LINES", "NO", "480000", "N"],
        ["SALES", "LOG_EVENTS", "NO", "", "N"],
        ["SALES", "MV_SALES", "NO", "300", "N"],
        ["SYS", "AUD$", "NO", "10", "N"],
    ])
    cols = [
        # OWNER, TABLE, COLUMN, ID, TYPE, LEN, CHAR_LEN, PREC, SCALE, NULLABLE
        ["SALES", "CUSTOMERS", "CUSTOMER_ID", 1, "NUMBER", 22, 0, 10, 0, "N"],
        ["SALES", "CUSTOMERS", "NAME", 2, "VARCHAR2", 400, 100, "", "", "N"],
        ["SALES", "CUSTOMERS", "REGION", 3, "CHAR", 2, 2, "", "", "Y"],
        ["SALES", "CUSTOMERS", "CREATED", 4, "DATE", 7, 0, "", "", "Y"],
        ["SALES", "ORDERS", "ORDER_ID", 1, "NUMBER", 22, 0, 12, 0, "N"],
        ["SALES", "ORDERS", "CUSTOMER_ID", 2, "NUMBER", 22, 0, 10, 0, "N"],
        ["SALES", "ORDERS", "ORDER_DATE", 3, "DATE", 7, 0, "", "", "N"],
        ["SALES", "ORDERS", "AMOUNT", 4, "NUMBER", 22, 0, 15, 2, "Y"],
        ["SALES", "ORDERS", "STATUS", 5, "VARCHAR2", 20, 20, "", "", "Y"],
        ["SALES", "ORDERS", "NOTE", 6, "CLOB", 4000, 0, "", "", "Y"],
        ["SALES", "ORDERS", "TOTAL_WEIGHT", 7, "NUMBER", 22, 0, "", "", "Y"],
        ["SALES", "ORDER_LINES", "ORDER_ID", 1, "NUMBER", 22, 0, 12, 0, "N"],
        ["SALES", "ORDER_LINES", "LINE_NO", 2, "NUMBER", 22, 0, 4, 0, "N"],
        ["SALES", "ORDER_LINES", "QTY", 3, "NUMBER", 22, 0, "", 0, "Y"],
        ["SALES", "ORDER_LINES", "PRICE", 4, "BINARY_DOUBLE", 8, 0, "", "", "Y"],
        ["SALES", "ORDER_LINES", "UPDATED_AT", 5, "TIMESTAMP(6) WITH TIME ZONE", 13, 0, "", 6, "Y"],
        ["SALES", "ORDER_LINES", "CHECKSUM", 6, "RAW", 16, 0, "", "", "Y"],
        ["SALES", "LOG_EVENTS", "EVENT_TS", 1, "TIMESTAMP(6)", 11, 0, "", 6, "Y"],
        ["SALES", "LOG_EVENTS", "MESSAGE", 2, "VARCHAR2", 4000, 4000, "", "", "Y"],
        ["SALES", "LOG_EVENTS", "LOCATION", 3, "SDO_GEOMETRY", 1, 0, "", "", "Y"],
        ["SALES", "MV_SALES", "REGION", 1, "CHAR", 2, 2, "", "", "Y"],
        ["SALES", "MV_SALES", "AMOUNT", 2, "NUMBER", 22, 0, "", "", "Y"],
        ["SALES", "V_SALES_BY_REGION", "REGION", 1, "CHAR", 2, 2, "", "", "Y"],
        ["SALES", "V_SALES_BY_REGION", "AMOUNT", 2, "NUMBER", 22, 0, "", "", "Y"],
        ["SYS", "AUD$", "SESSIONID", 1, "NUMBER", 22, 0, "", "", "N"],
    ]
    write(d, "ALL_TAB_COLUMNS", ["OWNER", "TABLE_NAME", "COLUMN_NAME", "COLUMN_ID", "DATA_TYPE", "DATA_LENGTH",
                                 "CHAR_LENGTH", "DATA_PRECISION", "DATA_SCALE", "NULLABLE"], cols)
    write(d, "ALL_CONSTRAINTS", ["OWNER", "CONSTRAINT_NAME", "CONSTRAINT_TYPE", "TABLE_NAME", "R_OWNER",
                                 "R_CONSTRAINT_NAME", "STATUS"], [
        ["SALES", "PK_CUSTOMERS", "P", "CUSTOMERS", "", "", "ENABLED"],
        ["SALES", "PK_ORDERS", "P", "ORDERS", "", "", "ENABLED"],
        ["SALES", "FK_ORDERS_CUSTOMER", "R", "ORDERS", "SALES", "PK_CUSTOMERS", "ENABLED"],
        ["SALES", "UQ_ORDER_LINES", "U", "ORDER_LINES", "", "", "ENABLED"],
        ["SALES", "FK_LINES_ORDER", "R", "ORDER_LINES", "SALES", "PK_ORDERS", "ENABLED"],
    ])
    write(d, "ALL_CONS_COLUMNS", ["OWNER", "CONSTRAINT_NAME", "TABLE_NAME", "COLUMN_NAME", "POSITION"], [
        ["SALES", "PK_CUSTOMERS", "CUSTOMERS", "CUSTOMER_ID", 1],
        ["SALES", "PK_ORDERS", "ORDERS", "ORDER_ID", 1],
        ["SALES", "FK_ORDERS_CUSTOMER", "ORDERS", "CUSTOMER_ID", 1],
        ["SALES", "UQ_ORDER_LINES", "ORDER_LINES", "LINE_NO", 2],
        ["SALES", "UQ_ORDER_LINES", "ORDER_LINES", "ORDER_ID", 1],
        ["SALES", "FK_LINES_ORDER", "ORDER_LINES", "ORDER_ID", 1],
    ])
    write(d, "ALL_TAB_COMMENTS", ["OWNER", "TABLE_NAME", "TABLE_TYPE", "COMMENTS"], [
        ["SALES", "CUSTOMERS", "TABLE", "Клиенты"],
        ["SALES", "ORDERS", "TABLE", "Заказы клиентов"],
    ])
    write(d, "ALL_COL_COMMENTS", ["OWNER", "TABLE_NAME", "COLUMN_NAME", "COMMENTS"], [
        ["SALES", "ORDERS", "AMOUNT", "Сумма заказа, руб."],
        ["SALES", "CUSTOMERS", "NAME", "Наименование клиента"],
    ])
    write(d, "ALL_VIEWS", ["OWNER", "VIEW_NAME", "TEXT"], [
        ["SALES", "V_SALES_BY_REGION", "SELECT c.region, SUM(o.amount) AS amount\n"
                                       "  FROM orders o\n  JOIN sales.customers c ON c.customer_id = o.customer_id\n"
                                       " GROUP BY c.region"],
    ])
    write(d, "ALL_OBJECTS", ["OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS"], [
        ["SALES", "PKG_LOAD", "PACKAGE", "VALID"],
        ["SALES", "PKG_LOAD", "PACKAGE BODY", "VALID"],
        ["SALES", "P_RECALC", "PROCEDURE", "INVALID"],
        ["SALES", "MV_SALES", "MATERIALIZED VIEW", "VALID"],
        ["SALES", "V_SALES_BY_REGION", "VIEW", "VALID"],
    ])
    write(d, "ALL_SOURCE", ["OWNER", "NAME", "TYPE", "LINES"], [
        ["SALES", "PKG_LOAD", "PACKAGE", 20],
        ["SALES", "PKG_LOAD", "PACKAGE BODY", 180],
        ["SALES", "P_RECALC", "PROCEDURE", 35],
    ])
    write(d, "ALL_PART_TABLES", ["OWNER", "TABLE_NAME", "PARTITIONING_TYPE", "SUBPARTITIONING_TYPE", "INTERVAL"], [
        ["SALES", "ORDERS", "RANGE", "NONE", "NUMTOYMINTERVAL(1,'MONTH')"],
        ["SALES", "CUSTOMERS", "LIST", "NONE", ""],
        ["SALES", "LOG_EVENTS", "HASH", "NONE", ""],
    ])
    write(d, "ALL_PART_KEY_COLUMNS", ["OWNER", "NAME", "OBJECT_TYPE", "COLUMN_NAME", "COLUMN_POSITION"], [
        ["SALES", "ORDERS", "TABLE", "ORDER_DATE", 1],
        ["SALES", "CUSTOMERS", "TABLE", "REGION", 1],
        ["SALES", "LOG_EVENTS", "TABLE", "EVENT_TS", 1],
        ["SALES", "PK_ORDERS", "INDEX", "ORDER_ID", 1],
    ])
    write(d, "ALL_TAB_PARTITIONS", ["TABLE_OWNER", "TABLE_NAME", "PARTITION_NAME", "PARTITION_POSITION",
                                    "HIGH_VALUE"], [
        ["SALES", "ORDERS", "P_2025", 1,
         "TO_DATE(' 2026-01-01 00:00:00', 'SYYYY-MM-DD HH24:MI:SS', 'NLS_CALENDAR=GREGORIAN')"],
        ["SALES", "ORDERS", "P_2026", 2,
         "TO_DATE(' 2027-01-01 00:00:00', 'SYYYY-MM-DD HH24:MI:SS', 'NLS_CALENDAR=GREGORIAN')"],
        ["SALES", "ORDERS", "P_MAX", 3, "MAXVALUE"],
        ["SALES", "CUSTOMERS", "P_CENTER", 1, "'C', 'M'"],
        ["SALES", "CUSTOMERS", "P_OTHER", 2, "DEFAULT"],
        ["SALES", "LOG_EVENTS", "SYS_P1", 1, ""],
    ])
    data = HERE / "oracle_data"
    write(data, "SALES_ORDERS", ["ORDER_ID", "CUSTOMER_ID", "ORDER_DATE", "AMOUNT", "STATUS", "NOTE", "TOTAL_WEIGHT"], [
        [1, 10, "2026-01-15 10:30:00", "1500,50", "NEW", "первый", "12.5"],
        [2, 11, "2026-01-16 00:00:00", "200", "PAID", "", ""],
        [3, 10, "2026-02-01 08:00:00", "", "NEW", "", "1e3"],
    ])
    write(data, "SALES_ORDERS_update", ["ORDER_ID", "CUSTOMER_ID", "ORDER_DATE", "AMOUNT", "STATUS"], [
        [2, 11, "2026-01-16 00:00:00", "250", "SHIPPED"],
        [4, 12, "2026-03-01 12:00:00", "99.99", "NEW"],
    ])
    write(data, "SALES_ORDER_LINES", ["ORDER_ID", "LINE_NO", "QTY", "PRICE", "UPDATED_AT", "CHECKSUM"], [
        [1, 1, 3, "10.5", "2026-01-15 10:30:00+03:00", "0x0A0B"],
        [1, 2, 1, "1.25e2", "2026-01-15T10:31:00Z", ""],
    ])
    write(data, "etalon_SALES_ORDERS", ["ORDER_ID", "CUSTOMER_ID", "AMOUNT", "TOTAL_WEIGHT"], [
        [1, 10, "1500.50", "12.5"], [2, 11, "250", ""], [3, 10, "", "1000"], [4, 12, "99.99", ""],
    ])


def mssql() -> None:
    d = HERE / "mssql_dict"
    write(d, "TABLES", ["TABLE_CATALOG", "TABLE_SCHEMA", "TABLE_NAME", "TABLE_TYPE"], [
        ["Shop", "dbo", "Customer", "BASE TABLE"],
        ["Shop", "dbo", "Order", "BASE TABLE"],
        ["Shop", "dbo", "AuditLog", "BASE TABLE"],
        ["Shop", "dbo", "vOrderTotals", "VIEW"],
        ["Shop", "dbo", "Country", "BASE TABLE"],
        ["Shop", "sales", "Sale", "BASE TABLE"],
        ["Shop", "sys", "trace_xe_action_map", "BASE TABLE"],
    ])
    write(d, "COLUMNS", ["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "ORDINAL_POSITION", "IS_NULLABLE",
                         "DATA_TYPE", "CHARACTER_MAXIMUM_LENGTH", "NUMERIC_PRECISION", "NUMERIC_SCALE",
                         "DATETIME_PRECISION", "COLLATION_NAME"], [
        ["dbo", "Customer", "CustomerId", 1, "NO", "int", "", 10, 0, "", ""],
        ["dbo", "Customer", "Name", 2, "NO", "nvarchar", 200, "", "", "", "Cyrillic_General_CI_AS"],
        ["dbo", "Customer", "Guid", 3, "YES", "uniqueidentifier", "", "", "", "", ""],
        ["dbo", "Customer", "Since", 4, "YES", "date", "", "", "", 0, ""],
        ["dbo", "Customer", "IsVip", 5, "NO", "bit", "", "", "", "", ""],
        ["dbo", "Order", "OrderId", 1, "NO", "bigint", "", 19, 0, "", ""],
        ["dbo", "Order", "CustomerId", 2, "NO", "int", "", 10, 0, "", ""],
        ["dbo", "Order", "Created", 3, "NO", "datetime2", "", "", "", 7, ""],
        ["dbo", "Order", "Total", 4, "YES", "money", "", 19, 4, "", ""],
        ["dbo", "Order", "Comment", 5, "YES", "nvarchar", -1, "", "", "", "Cyrillic_General_CI_AS"],
        ["dbo", "Order", "RowVer", 6, "NO", "timestamp", 8, "", "", "", ""],
        ["dbo", "Order", "ShipTime", 7, "YES", "time", "", "", "", 0, ""],
        ["dbo", "AuditLog", "At", 1, "NO", "datetimeoffset", "", "", "", 7, ""],
        ["dbo", "AuditLog", "Data", 2, "YES", "sql_variant", "", "", "", "", ""],
        ["dbo", "vOrderTotals", "CustomerId", 1, "NO", "int", "", 10, 0, "", ""],
        ["dbo", "Country", "Code", 1, "NO", "nvarchar", 2, "", "", "", "Cyrillic_General_CI_AS"],
        ["dbo", "Country", "Name", 2, "NO", "nvarchar", 100, "", "", "", "Cyrillic_General_CI_AS"],
        ["dbo", "Country", "Capital", 3, "YES", "varchar", 50, "", "", "", "Cyrillic_General_CI_AS"],  # cp1251
        ["sales", "Sale", "SaleId", 1, "NO", "bigint", "", 19, 0, "", ""],
        ["sales", "Sale", "SaleDate", 2, "NO", "date", "", "", "", 0, ""],
        ["sales", "Sale", "Amount", 3, "YES", "decimal", "", 18, 2, "", ""],
    ])
    write(d, "TABLE_CONSTRAINTS", ["CONSTRAINT_SCHEMA", "CONSTRAINT_NAME", "TABLE_SCHEMA", "TABLE_NAME",
                                   "CONSTRAINT_TYPE"], [
        ["dbo", "PK_Customer", "dbo", "Customer", "PRIMARY KEY"],
        ["dbo", "PK_Order", "dbo", "Order", "PRIMARY KEY"],
        ["dbo", "FK_Order_Customer", "dbo", "Order", "FOREIGN KEY"],
        ["dbo", "PK_Country", "dbo", "Country", "PRIMARY KEY"],
        ["sales", "PK_Sale", "sales", "Sale", "PRIMARY KEY"],
    ])
    write(d, "KEY_COLUMN_USAGE", ["CONSTRAINT_SCHEMA", "CONSTRAINT_NAME", "TABLE_SCHEMA", "TABLE_NAME",
                                  "COLUMN_NAME", "ORDINAL_POSITION"], [
        ["dbo", "PK_Customer", "dbo", "Customer", "CustomerId", 1],
        ["dbo", "PK_Order", "dbo", "Order", "OrderId", 1],
        ["dbo", "FK_Order_Customer", "dbo", "Order", "CustomerId", 1],
        ["dbo", "PK_Country", "dbo", "Country", "Code", 1],
        ["sales", "PK_Sale", "sales", "Sale", "SaleId", 1],
        ["sales", "PK_Sale", "sales", "Sale", "SaleDate", 2],
    ])
    write(d, "PARTITIONS", ["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "FUNCTION_NAME", "RANGE_RIGHT",
                            "BOUNDARY_ID", "BOUNDARY_VALUE", "VALUE_TYPE"], [
        ["sales", "Sale", "SaleDate", "pfSaleYear", 1, 1, "2025-01-01T00:00:00", "date"],
        ["sales", "Sale", "SaleDate", "pfSaleYear", 1, 2, "2026-01-01T00:00:00", "date"],
    ])
    write(d, "REFERENTIAL_CONSTRAINTS", ["CONSTRAINT_SCHEMA", "CONSTRAINT_NAME", "UNIQUE_CONSTRAINT_SCHEMA",
                                         "UNIQUE_CONSTRAINT_NAME"],
          [["dbo", "FK_Order_Customer", "dbo", "PK_Customer"]])
    write(d, "COMMENTS", ["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "COMMENT"], [
        ["dbo", "Order", "", "Заказы интернет-магазина"],
        ["dbo", "Order", "Total", "Сумма с НДС"],
    ])
    write(d, "VIEWS", ["TABLE_SCHEMA", "TABLE_NAME", "VIEW_DEFINITION"], [
        ["dbo", "vOrderTotals", "CREATE VIEW dbo.vOrderTotals AS\nSELECT CustomerId, SUM(Total) AS Total\n"
                                "FROM [dbo].[Order] GROUP BY CustomerId"],
    ])
    write(d, "ROUTINES", ["ROUTINE_SCHEMA", "ROUTINE_NAME", "ROUTINE_TYPE", "LINES"], [
        ["dbo", "usp_LoadOrders", "SQL_STORED_PROCEDURE", 140],
        ["dbo", "fn_Vat", "SQL_SCALAR_FUNCTION", 12],
    ])
    data = HERE / "mssql_data"
    write(data, "dbo_Country", ["Code", "Name"], [["ru", "Россия"], ["KZ", "Казахстан"]])
    write(data, "dbo_Country_update", ["Code", "Name"], [["RU", "Российская Федерация"]])
    write(data, "etalon_dbo_Country", ["Code"], [["RU"], ["kz"]])
    write(data, "sales_Sale", ["SaleId", "SaleDate", "Amount"], [
        [1, "2024-12-31", "10.00"], [2, "2025-06-01", "20.50"], [3, "2026-02-01", "5"]])
    write(data, "dbo_Order", ["OrderId", "CustomerId", "Created", "Total", "Comment", "RowVer", "ShipTime"], [
        [100, 1, "2026-05-01 09:00:00.1234567", "10.5000", "без НДС", "0x00000000000007D1", "10:15:00"],
        [101, 2, "2026-05-02T18:30:00", "", "", "0x00000000000007D2", ""],
    ])


def greenplum() -> None:
    """Каталог Greenplum 6 (Yandex MPP 6.x) в том виде, в каком его возвращают запросы greenplum.queries(6)."""
    d = HERE / "greenplum_dict"
    write(d, "VERSION", ["VERSION", "VERSION_NUM", "DATABASE"], [
        ["PostgreSQL 9.4.26 (Greenplum Database 6.28.1 build commit:abc) on x86_64-unknown-linux-gnu", "90426",
         "dwh"],
    ])
    write(d, "TABLES", ["TABLE_SCHEMA", "TABLE_NAME", "STORAGE", "RELOPTIONS", "HAS_OIDS", "COMMENT", "ROW_ESTIMATE"], [
        ["sales", "orders", "heap", "", "N", "Заказы", "150000"],
        ["sales", "order_lines", "ao_column",
         "appendonly=true, orientation=column, compresstype=quicklz, compresslevel=1", "N", "", "4800000"],
        ["sales", "fact_sales", "ao_row", "appendonly=true, compresstype=zstd, compresslevel=5", "N",
         "Продажи по дням", "9000000"],
        ["sales", "regions", "heap", "fillfactor=90", "Y", "Справочник регионов", "80"],
        ["sales", "ext_orders", "external", "", "N", "", "0"],
        ["pg_catalog", "pg_class", "heap", "", "N", "", "0"],
    ])
    cols = [
        # схема, таблица, колонка, позиция, format_type, NOT NULL, значение по умолчанию, схема типа, комментарий
        ["sales", "orders", "order_id", 1, "bigint", "Y", "nextval('sales.orders_order_id_seq'::regclass)",
         "pg_catalog", "Номер заказа"],
        ["sales", "orders", "region", 2, "character(2)", "N", "", "pg_catalog", ""],
        ["sales", "orders", "amount", 3, "numeric(15,2)", "N", "", "pg_catalog", ""],
        ["sales", "orders", "created", 4, "timestamp(3) without time zone", "N", "", "pg_catalog", ""],
        ["sales", "orders", "attrs", 5, "jsonb", "N", "", "pg_catalog", ""],
        ["sales", "orders", "status", 6, "sales.order_status", "N", "", "sales", ""],
        ["sales", "orders", "valid_until", 7, "abstime", "N", "", "pg_catalog", ""],
        ["sales", "order_lines", "order_id", 1, "bigint", "Y", "", "pg_catalog", ""],
        ["sales", "order_lines", "line_no", 2, "integer", "Y", "", "pg_catalog", ""],
        ["sales", "order_lines", "price", 3, "double precision", "N", "", "pg_catalog", ""],
        ["sales", "order_lines", "tags", 4, "text[]", "N", "", "pg_catalog", ""],
        ["sales", "fact_sales", "sale_date", 1, "date", "Y", "", "pg_catalog", ""],
        ["sales", "fact_sales", "region", 2, "character(2)", "Y", "", "pg_catalog", ""],
        ["sales", "fact_sales", "qty", 3, "integer", "N", "", "pg_catalog", ""],
        ["sales", "fact_sales", "amount", 4, "numeric(18,2)", "N", "", "pg_catalog", ""],
        ["sales", "regions", "code", 1, "character(2)", "Y", "", "pg_catalog", ""],
        ["sales", "regions", "name", 2, "character varying(100)", "Y", "", "pg_catalog", ""],
        ["sales", "ext_orders", "line", 1, "text", "N", "", "pg_catalog", ""],
    ]
    write(d, "COLUMNS", ["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "POSITION", "DATA_TYPE", "NOT_NULL",
                         "COLUMN_DEFAULT", "TYPE_SCHEMA", "COMMENT"], cols)
    write(d, "CONSTRAINTS", ["TABLE_SCHEMA", "TABLE_NAME", "CONSTRAINT_NAME", "CONSTRAINT_TYPE", "COLUMNS",
                             "REF_TABLE", "DEFINITION"], [
        ["sales", "orders", "orders_pkey", "p", "order_id", "", "PRIMARY KEY (order_id)"],
        ["sales", "regions", "regions_pkey", "p", "code", "", "PRIMARY KEY (code)"],
        ["sales", "orders", "orders_region_fkey", "f", "region", "sales.regions",
         "FOREIGN KEY (region) REFERENCES sales.regions(code)"],
        ["sales", "fact_sales", "fact_sales_qty_check", "c", "qty", "", "CHECK (qty >= 0)"],
    ])
    write(d, "DISTRIBUTION", ["TABLE_SCHEMA", "TABLE_NAME", "DISTRIBUTED_BY"], [
        ["sales", "orders", "DISTRIBUTED BY (order_id)"],
        ["sales", "order_lines", "DISTRIBUTED BY (order_id cdbhash_int8_ops)"],
        ["sales", "fact_sales", "DISTRIBUTED RANDOMLY"],
        ["sales", "regions", "DISTRIBUTED REPLICATED"],
    ])
    header = ["TABLE_SCHEMA", "TABLE_NAME", "PARTITION_NAME", "PARTITION_TABLE", "LEVEL", "POSITION", "KIND",
              "KEY_COLUMNS", "IS_DEFAULT", "LIST_VALUES", "RANGE_START", "START_INCLUSIVE", "RANGE_END",
              "END_INCLUSIVE", "BOUND", "SUBPARTITIONED"]
    write(d, "PARTITIONS", header, [
        # START … END … EVERY: безымянные секции, имя — из таблицы-наследника; есть разрыв 2025-01 … 2025-07
        ["sales", "fact_sales", "", "fact_sales_1_prt_2", 0, 1, "range", "sale_date", "N", "",
         "'2024-01-01'::date", "Y", "'2024-07-01'::date", "N", "", "N"],
        ["sales", "fact_sales", "", "fact_sales_1_prt_3", 0, 2, "range", "sale_date", "N", "",
         "'2024-07-01'::date", "Y", "'2025-01-01'::date", "N", "", "N"],
        ["sales", "fact_sales", "h2025", "fact_sales_1_prt_h2025", 0, 3, "range", "sale_date", "N", "",
         "'2025-07-01'::date", "Y", "'2026-01-01'::date", "N", "", "N"],
        ["sales", "fact_sales", "other", "fact_sales_1_prt_other", 0, 4, "range", "sale_date", "Y", "",
         "", "N", "", "N", "", "N"],
        ["sales", "fact_sales", "ru", "fact_sales_1_prt_2_2_prt_ru", 1, 1, "list", "region", "N", "'RU'::bpchar",
         "", "N", "", "N", "", "N"],
    ])
    write(d, "VIEWS", ["TABLE_SCHEMA", "TABLE_NAME", "KIND", "VIEW_DEFINITION"], [
        ["sales", "v_daily", "v", " SELECT fact_sales.sale_date, sum(fact_sales.amount) AS amount\n"
         "   FROM sales.fact_sales\n  GROUP BY fact_sales.sale_date;"],
    ])
    write(d, "ROUTINES", ["ROUTINE_SCHEMA", "ROUTINE_NAME", "ARGUMENTS", "LANGUAGE", "KIND", "IS_TRIGGER",
                          "SOURCE"], [
        ["sales", "load_day", "p_day date", "plpgsql", "f", "N",
         "\nBEGIN\n  DELETE FROM sales.fact_sales WHERE sale_date = p_day;\n"
         "  INSERT INTO sales.fact_sales SELECT * FROM sales.orders;\nEND;\n"],
        ["sales", "load_day", "", "plpgsql", "f", "N", "\nBEGIN\n  PERFORM sales.load_day(current_date);\nEND;\n"],
        ["sales", "median", "numeric", "sql", "a", "N", ""],
    ])


def teradata() -> None:
    """Словарь DBC Teradata 17.20 в том виде, в каком его возвращают запросы teradata.TERADATA_QUERIES.
    Коды типов и индексов — по документации Teradata (Data Dictionary, 17.20); живой системы не было."""
    d = HERE / "teradata_dict"
    write(d, "DBCInfoV", ["InfoKey", "InfoData"], [
        ["LANGUAGE SUPPORT MODE", "Standard"], ["RELEASE", "17.20.03.09"], ["VERSION", "17.20.03.09"],
    ])
    view = ("REPLACE VIEW SALES.vLastOrder AS LOCKING ROW FOR ACCESS\n"
            "SEL o.OrderId, o.OrderDate, c.Name\nFROM SALES.Orders o\nJOIN SALES.Customers c "
            "ON c.CustomerId = o.CustomerId\n"
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY o.CustomerId ORDER BY o.OrderDate DESC) = 1;")
    macro = ("REPLACE MACRO SALES.mLoadDay (d DATE) AS (\n"
             "  DEL FROM SALES.EventLog WHERE CAST(EventTs AS DATE) = :d;\n"
             "  INS INTO SALES.EventLog SEL * FROM STG.Events WHERE CAST(EventTs AS DATE FORMAT 'YYYY-MM-DD') = :d;\n"
             ");")
    proc = ("REPLACE PROCEDURE SALES.spRecalc()\nBEGIN\n"
            "  MERGE INTO SALES.Customers c USING (SEL CustomerId, ZEROIFNULL(SUM(Amount)) s FROM SALES.Orders "
            "GROUP BY 1) o\n  ON c.CustomerId = o.CustomerId\n"
            "  WHEN MATCHED THEN UPDATE SET Segment = CASE WHEN o.s > 1000 THEN 1 ELSE 0 END;\nEND;")
    write(d, "TablesV", ["DatabaseName", "TableName", "TableKind", "CheckOpt", "CommitOpt", "CommentString",
                         "RequestText"], [
        ["SALES", "Customers", "T", "Y", "N", "Клиенты",
         "CREATE SET TABLE SALES.Customers ... UNIQUE PRIMARY INDEX (CustomerId);"],
        ["SALES", "Orders", "T", "N", "N", "Заказы",
         "CREATE MULTISET TABLE SALES.Orders ... PRIMARY INDEX (CustomerId) PARTITION BY RANGE_N(...);"],
        ["SALES", "OrderLines", "T", "N", "N", "", ""],
        ["SALES", "EventLog", "T", "Y", "N", "Журнал событий",
         "CREATE SET TABLE SALES.EventLog (EventType CHAR(10) COMPRESS ('CLICK', 'VIEW'), ...) "
         "PRIMARY INDEX (EventType);"],
        ["SALES", "Clicks", "O", "N", "N", "", "CREATE MULTISET TABLE SALES.Clicks (...) NO PRIMARY INDEX "
                                               "PARTITION BY COLUMN;"],
        ["SALES", "Daily", "T", "N", "N", "", ""],
        ["SALES", "TmpLoad", "T", "N", "P", "", ""],
        ["SALES", "vLastOrder", "V", "N", "N", "", view],
        ["SALES", "mLoadDay", "M", "N", "N", "", macro],
        ["SALES", "spRecalc", "P", "N", "N", "", proc],
        ["SALES", "fnNet", "F", "N", "N", "", ""],
        ["SALES", "trgOrders", "G", "N", "N", "", "REPLACE TRIGGER SALES.trgOrders AFTER INSERT ON SALES.Orders ..."],
        ["SALES", "jiOrders", "I", "N", "N", "", "CREATE JOIN INDEX SALES.jiOrders AS SEL CustomerId, OrderDate "
                                                 "FROM SALES.Orders;"],
        ["SALES", "hiOrders", "N", "N", "N", "", ""],
        ["DBC", "TVM", "T", "N", "N", "", ""],
    ])
    # DatabaseName, TableName, ColumnName, ColumnId, ColumnType, ColumnLength, DecimalTotalDigits,
    # DecimalFractionalDigits, Nullable, CharType, UpperCaseFlag, ColumnFormat, IdColType, CommentString
    cols = [
        ["SALES", "Customers", "CustomerId", 1025, "I ", 4, "", "", "N", "", "", "-(10)9", "", "Код клиента"],
        ["SALES", "Customers", "Name", 1026, "CV", 200, "", "", "N", 2, "N", "X(100)", "", ""],
        ["SALES", "Customers", "Region", 1027, "CF", 2, "", "", "Y", 1, "C", "X(2)", "", ""],
        ["SALES", "Customers", "Segment", 1028, "I1", 1, "", "", "Y", "", "", "-(3)9", "", ""],
        ["SALES", "Customers", "CreatedAt", 1029, "TS", 26, "", 6, "Y", "", "", "YYYY-MM-DDBHH:MI:SS.S(6)", "", ""],
        ["SALES", "Orders", "OrderId", 1025, "I8", 8, "", "", "N", "", "", "-(19)9", "", ""],
        ["SALES", "Orders", "CustomerId", 1026, "I ", 4, "", "", "N", "", "", "-(10)9", "", ""],
        ["SALES", "Orders", "OrderDate", 1027, "DA", 4, "", "", "N", "", "", "YY/MM/DD", "", ""],
        ["SALES", "Orders", "Amount", 1028, "D ", 8, 15, 2, "Y", "", "", "--(13)9.99", "", ""],
        ["SALES", "Orders", "Status", 1029, "CV", 10, "", "", "Y", 1, "N", "X(10)", "", ""],
        ["SALES", "Orders", "Delivery", 1030, "DS", 16, 2, 0, "Y", "", "", "", "", ""],
        ["SALES", "Orders", "Notes", 1031, "CO", 2097152, "", "", "Y", 1, "N", "", "", ""],
        ["SALES", "OrderLines", "OrderId", 1025, "I8", 8, "", "", "N", "", "", "", "", ""],
        ["SALES", "OrderLines", "LineNo", 1026, "I2", 2, "", "", "N", "", "", "", "", ""],
        ["SALES", "OrderLines", "Sku", 1027, "CF", 40, "", "", "N", 2, "C", "X(20)", "", ""],
        ["SALES", "OrderLines", "Qty", 1028, "D ", 4, 9, 3, "Y", "", "", "", "", ""],
        ["SALES", "OrderLines", "Price", 1029, "N ", 18, 12, 2, "Y", "", "", "", "", ""],
        ["SALES", "EventLog", "EventTs", 1025, "SZ", 32, "", 6, "N", "", "", "", "", ""],
        ["SALES", "EventLog", "EventType", 1026, "CF", 10, "", "", "N", 1, "N", "X(10)", "", ""],
        ["SALES", "EventLog", "Payload", 1027, "JN", 16000, "", "", "Y", 2, "", "", "", ""],
        ["SALES", "EventLog", "Validity", 1028, "PD", 8, "", "", "Y", "", "", "", "", ""],
        ["SALES", "EventLog", "RawData", 1029, "BV", 16, "", "", "Y", "", "", "", "", ""],
        ["SALES", "EventLog", "Score", 1030, "F ", 8, "", "", "Y", "", "", "", "", ""],
        ["SALES", "EventLog", "Weight", 1031, "N ", 18, -128, -128, "Y", "", "", "", "", ""],
        ["SALES", "Clicks", "ClickId", 1025, "I8", 8, "", "", "N", "", "", "", "GA", ""],
        ["SALES", "Clicks", "Url", 1026, "CV", 2000, "", "", "Y", 1, "C", "", "", ""],
        ["SALES", "Clicks", "ClickedAt", 1027, "TS", 23, "", 3, "Y", "", "", "", "", ""],
        ["SALES", "Daily", "D", 1025, "DA", 4, "", "", "N", "", "", "YYYY-MM-DD", "", ""],
        ["SALES", "Daily", "V", 1026, "I ", 4, "", "", "Y", "", "", "", "", ""],
        ["SALES", "Daily", "Kind", 1027, "PS", 16, "", "", "Y", "", "", "", "", ""],
        ["SALES", "TmpLoad", "Id", 1025, "I ", 4, "", "", "N", "", "", "", "", ""],
        ["SALES", "TmpLoad", "Shape", 1026, "UT", 100, "", "", "Y", "", "", "", "", ""],
        ["SALES", "TmpLoad", "Odd", 1027, "ZZ", 4, "", "", "Y", "", "", "", "", ""],
        ["DBC", "TVM", "TVMId", 1025, "BF", 6, "", "", "N", "", "", "", "", ""],
    ]
    write(d, "ColumnsV", ["DatabaseName", "TableName", "ColumnName", "ColumnId", "ColumnType", "ColumnLength",
                          "DecimalTotalDigits", "DecimalFractionalDigits", "Nullable", "CharType", "UpperCaseFlag",
                          "ColumnFormat", "IdColType", "CommentString"], cols)
    write(d, "IndicesV", ["DatabaseName", "TableName", "IndexNumber", "IndexType", "UniqueFlag", "IndexName",
                          "ColumnName", "ColumnPosition"], [
        ["SALES", "Customers", 1, "P", "Y", "", "CustomerId", 1],
        ["SALES", "Orders", 1, "Q", "N", "", "CustomerId", 1],
        ["SALES", "Orders", 4, "K", "Y", "PK_Orders", "OrderId", 1],
        ["SALES", "OrderLines", 1, "Q", "Y", "", "LineNo", 2],
        ["SALES", "OrderLines", 1, "Q", "Y", "", "OrderId", 1],
        ["SALES", "OrderLines", 4, "S", "N", "", "Sku", 1],
        ["SALES", "EventLog", 1, "P", "N", "", "EventType", 1],
        ["SALES", "Daily", 1, "Q", "N", "", "D", 1],
        ["SALES", "TmpLoad", 1, "P", "N", "", "Id", 1],
    ])
    write(d, "PartitioningConstraintsV", ["DatabaseName", "TableName", "ConstraintText", "PartitioningLevels",
                                          "ColumnPartitioningLevel"], [
        ["SALES", "Orders", "CHECK ((RANGE_N(OrderDate  BETWEEN DATE '2024-01-01' AND DATE '2024-12-31' "
                            "EACH INTERVAL '1' MONTH , NO RANGE)) BETWEEN 1 AND 13)", 1, 0],
        ["SALES", "OrderLines", "CHECK ((RANGE_N(LineNo  BETWEEN 1  AND 100  EACH 25 , UNKNOWN)) BETWEEN 1 AND 5)",
         1, 0],
        ["SALES", "Clicks", "CHECK (/*01 02 */ PARTITION#L1 /*1 9+10*/ =1)", 1, 1],
        ["SALES", "Daily", "CHECK (/*02 03 */ PARTITION#L1 /*1 3653+0*/ = RANGE_N(D  BETWEEN DATE '2015-01-01' "
                           "AND DATE '2024-12-31' EACH INTERVAL '1' DAY ) AND PARTITION#L2 /*2 2+0*/ = "
                           "CASE_N(V <  0 , NO CASE) IS NOT NULL )", 2, 0],
    ])
    write(d, "All_RI_ChildrenV", ["IndexName", "ChildDB", "ChildTable", "ChildKeyColumn", "ParentDB",
                                  "ParentTable"], [
        ["FK_Lines_Orders", "SALES", "OrderLines", "OrderId", "SALES", "Orders"],
    ])
    scripts = d / "bteq"
    scripts.mkdir(exist_ok=True)
    (scripts / "load_orders.bteq").write_text(
        ".LOGON tdprod/etl,secret;\nDATABASE SALES;\n\nDEL FROM Orders WHERE OrderDate = DATE;\n"
        "INS INTO Orders SEL * FROM STG.Orders;\n.IF ERRORCODE <> 0 THEN .QUIT 8;\n"
        "COLLECT STATISTICS ON Orders COLUMN (OrderDate);\n.LOGOFF;\n.QUIT 0;\n", encoding="utf-8")
    (scripts / "customers.fld").write_text(
        "SESSIONS 4;\nLOGON tdprod/etl,secret;\nDEFINE CustomerId (VARCHAR(10)), Name (VARCHAR(100))\n"
        "FILE = customers.txt;\nBEGIN LOADING SALES.Customers ERRORFILES SALES.Cust_e1, SALES.Cust_e2;\n"
        "INSERT INTO SALES.Customers VALUES (:CustomerId, :Name);\nEND LOADING;\nLOGOFF;\n", encoding="utf-8")


if __name__ == "__main__":
    oracle()
    mssql()
    greenplum()
    teradata()
