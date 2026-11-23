"""Синтетические выгрузки словаря Oracle и MS SQL Server для тестов (tests/data/oracle_dict, mssql_dict).

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


if __name__ == "__main__":
    oracle()
    mssql()
