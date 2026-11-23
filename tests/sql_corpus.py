"""Набор тестовых представлений для переводчика SQL (этап 1, критерий: выполнимый SQL для ~70% и совпадение
результатов с источником).

Одни и те же данные (клиенты, заказы, сотрудники с иерархией) создаются в источнике и в целевой базе.
Представления написаны так, как их пишут в хранилищах на Oracle, MS SQL Server и Teradata: (+), CONNECT BY,
ROWNUM, DECODE, TOP, CROSS APPLY, QUALIFY, SEL, функции дат. В каждом наборе есть и конструкции, которые
переводчик честно не переводит (PIVOT, FOR XML, пользовательские функции, SAMPLE): они должны попасть
в черновики с TODO, а не потеряться.

Teradata живьём недоступна: для её представлений записан эталонный запрос PostgreSQL, результаты
сравниваются с ним на той же целевой базе.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

CUSTOMERS = [  # customer_id, name, region, created, credit
    (1, "Alpha", "North", date(2023, 1, 15), D("1000.00")),
    (2, "Beta", "South", date(2023, 2, 1), D("250.50")),
    (3, "Gamma", None, date(2023, 3, 10), D("0.00")),
    (4, "Delta", "North", date(2023, 4, 5), None),
    (5, "Epsilon", "East", date(2023, 5, 20), D("75.25")),
    (6, "Zeta", "West", date(2023, 6, 30), D("1200.00")),
    (7, "Eta", "South", date(2023, 7, 7), D("10.00")),
    (8, "Theta", None, date(2023, 8, 18), D("500.00")),
]
ORDERS = [  # order_id, customer_id, order_date, amount, qty, status
    (101, 1, datetime(2024, 1, 10), D("120.00"), 3, "NEW"),
    (102, 1, datetime(2024, 1, 25), D("80.50"), 1, "DONE"),
    (103, 2, datetime(2024, 2, 3), D("300.00"), 7, "DONE"),
    (104, 2, datetime(2024, 2, 14), D("45.99"), 2, None),
    (105, 3, datetime(2024, 3, 1), D("999.99"), 10, "NEW"),
    (106, 4, datetime(2024, 3, 15), D("15.00"), 1, "CANCEL"),
    (107, 4, datetime(2024, 4, 2), D("230.00"), 4, "DONE"),
    (108, 4, datetime(2024, 4, 20), D("60.00"), 5, "NEW"),
    (109, 5, datetime(2024, 5, 5), D("500.00"), 9, "DONE"),
    (110, 6, datetime(2024, 5, 18), D("75.75"), 2, "DONE"),
    (111, 6, datetime(2024, 6, 9), D("410.10"), 6, None),
    (112, 6, datetime(2024, 6, 22), D("12.34"), 1, "NEW"),
    (113, 1, datetime(2024, 7, 4), D("88.00"), 3, "DONE"),
    (114, 2, datetime(2024, 7, 19), D("640.00"), 8, "DONE"),
    (115, 5, datetime(2024, 8, 8), D("5.50"), 1, "CANCEL"),
    (116, 3, datetime(2024, 8, 27), D("150.00"), 2, "NEW"),
    (117, 1, datetime(2024, 9, 12), D("220.00"), 4, "DONE"),
    (118, 4, datetime(2024, 10, 3), D("33.30"), 3, "NEW"),
    (119, 6, datetime(2024, 10, 21), D("710.00"), 11, "DONE"),
    (120, 5, datetime(2024, 11, 11), D("95.00"), 2, None),
]
EMPLOYEES = [  # emp_id, name, manager_id, salary, hired
    (1, "Boss", None, D("9000.00"), date(2015, 1, 12)),
    (2, "Anna", 1, D("5000.00"), date(2016, 3, 1)),
    (3, "Boris", 1, D("5200.00"), date(2017, 6, 15)),
    (4, "Clara", 2, D("3100.00"), date(2018, 9, 9)),
    (5, "Denis", 2, D("3000.00"), date(2019, 2, 20)),
    (6, "Elena", 3, D("3500.00"), date(2020, 11, 2)),
    (7, "Fedor", 6, D("2500.00"), date(2021, 4, 14)),
    (8, "Galina", 6, D("2600.00"), date(2022, 8, 8)),
    (9, "Igor", None, D("8000.00"), date(2015, 5, 5)),
    (10, "Kira", 9, D("4100.00"), date(2023, 1, 23)),
]
DATA = {"CUSTOMERS": CUSTOMERS, "ORDERS": ORDERS, "EMPLOYEES": EMPLOYEES}
COLUMNS = {
    "CUST_SUMMARY": ["CUSTOMER_ID", "TOTAL", "CNT", "LVL"],
    "CUSTSUMMARY": ["CustomerId", "Total", "Cnt", "Lvl"],
    "CUSTOMERS": ["CUSTOMER_ID", "NAME", "REGION", "CREATED", "CREDIT"],
    "ORDERS": ["ORDER_ID", "CUSTOMER_ID", "ORDER_DATE", "AMOUNT", "QTY", "STATUS"],
    "EMPLOYEES": ["EMP_ID", "NAME", "MANAGER_ID", "SALARY", "HIRED"],
}

ORACLE_TABLES = [
    "CREATE TABLE CUSTOMERS (CUSTOMER_ID NUMBER(10) PRIMARY KEY, NAME VARCHAR2(50) NOT NULL, REGION VARCHAR2(20), "
    "CREATED DATE, CREDIT NUMBER(12,2))",
    "CREATE TABLE ORDERS (ORDER_ID NUMBER(10) PRIMARY KEY, CUSTOMER_ID NUMBER(10) NOT NULL, ORDER_DATE DATE NOT NULL, "
    "AMOUNT NUMBER(12,2), QTY NUMBER(5), STATUS VARCHAR2(10))",
    "CREATE TABLE EMPLOYEES (EMP_ID NUMBER(10) PRIMARY KEY, NAME VARCHAR2(50), MANAGER_ID NUMBER(10), "
    "SALARY NUMBER(10,2), HIRED DATE)",
]
MSSQL_TABLES = [
    "CREATE TABLE sales.Customers (CustomerId int PRIMARY KEY, Name nvarchar(50) NOT NULL, Region nvarchar(20), "
    "Created date, Credit decimal(12,2))",
    "CREATE TABLE sales.Orders (OrderId int PRIMARY KEY, CustomerId int NOT NULL, OrderDate datetime2(0) NOT NULL, "
    "Amount decimal(12,2), Qty int, Status varchar(10))",
    "CREATE TABLE sales.Employees (EmpId int PRIMARY KEY, Name nvarchar(50), ManagerId int, Salary decimal(10,2), "
    "Hired date)",
]
MSSQL_COLUMNS = {
    "CUSTSUMMARY": ["CustomerId", "Total", "Cnt", "Lvl"],
    "CUSTOMERS": ["CustomerId", "Name", "Region", "Created", "Credit"],
    "ORDERS": ["OrderId", "CustomerId", "OrderDate", "Amount", "Qty", "Status"],
    "EMPLOYEES": ["EmpId", "Name", "ManagerId", "Salary", "Hired"],
}

# (имя, текст, переводится ли автоматически). Тексты — как в CREATE VIEW источника
ORACLE_VIEWS = [
    ("V_CUST_BASIC", "SELECT CUSTOMER_ID, UPPER(NAME) NAME_U, NVL(REGION, 'N/A') REGION FROM CUSTOMERS", True),
    ("V_ORDER_STATUS", "SELECT ORDER_ID, DECODE(STATUS, 'NEW', 1, 'DONE', 2, NULL, -1, 0) ST FROM ORDERS", True),
    ("V_CUST_ORDERS_OJ", "SELECT c.CUSTOMER_ID, c.NAME, o.ORDER_ID FROM CUSTOMERS c, ORDERS o "
                         "WHERE c.CUSTOMER_ID = o.CUSTOMER_ID(+)", True),
    ("V_TOP5_ORDERS", "SELECT * FROM (SELECT ORDER_ID, AMOUNT FROM ORDERS ORDER BY AMOUNT DESC, ORDER_ID) "
                      "WHERE ROWNUM <= 5", True),
    ("V_EMP_TREE", "SELECT EMP_ID, NAME, LEVEL LVL, SYS_CONNECT_BY_PATH(NAME, '/') PATH FROM EMPLOYEES "
                   "START WITH MANAGER_ID IS NULL CONNECT BY PRIOR EMP_ID = MANAGER_ID", True),
    ("V_EMP_ROOT", "SELECT EMP_ID, CONNECT_BY_ROOT EMP_ID ROOT_ID, LEVEL - 1 DEPTH FROM EMPLOYEES "
                   "START WITH MANAGER_ID IS NULL CONNECT BY PRIOR EMP_ID = MANAGER_ID", True),
    ("V_ORDER_DUE", "SELECT ORDER_ID, ORDER_DATE + 30 DUE_DATE, TRUNC(ORDER_DATE, 'MM') MONTH_START, "
                    "TRUNC(ORDER_DATE) DAY_START FROM ORDERS", True),
    ("V_ORDER_NEXT_MONTH", "SELECT ORDER_ID, ADD_MONTHS(ORDER_DATE, 1) NEXT_MONTH FROM ORDERS", True),
    ("V_ORDER_AGE", "SELECT ORDER_ID, ROUND(MONTHS_BETWEEN(DATE '2025-01-01', ORDER_DATE), 4) MONTHS_LEFT, "
                    "DATE '2025-01-01' - ORDER_DATE DAYS_LEFT FROM ORDERS", True),
    ("V_CUST_TOTALS", "SELECT CUSTOMER_ID, COUNT(*) CNT, SUM(AMOUNT) TOTAL, ROUND(AVG(AMOUNT), 4) AVG_AMT "
                      "FROM ORDERS GROUP BY CUSTOMER_ID HAVING COUNT(*) > 1", True),
    ("V_CUST_STATUSES", "SELECT CUSTOMER_ID, LISTAGG(STATUS, ',') WITHIN GROUP (ORDER BY ORDER_ID) STATUSES "
                        "FROM ORDERS GROUP BY CUSTOMER_ID", True),
    ("V_RUNNING", "SELECT ORDER_ID, CUSTOMER_ID, SUM(AMOUNT) OVER (PARTITION BY CUSTOMER_ID ORDER BY ORDER_ID) "
                  "RUNNING, RANK() OVER (ORDER BY AMOUNT DESC) RNK FROM ORDERS", True),
    ("V_CUST_LABEL", "SELECT CUSTOMER_ID, NAME || ' (' || REGION || ')' LABEL FROM CUSTOMERS", True),
    ("V_CUST_STR", "SELECT CUSTOMER_ID, SUBSTR(NAME, 1, 3) S3, INSTR(NAME, 'a') POS_A, LENGTH(NAME) LEN, "
                   "LPAD(TO_CHAR(CUSTOMER_ID), 4, '0') CODE FROM CUSTOMERS", True),
    ("V_CUST_NVL2", "SELECT CUSTOMER_ID, NVL2(REGION, 'has', 'none') HAS_REGION, COALESCE(CREDIT, 0) CREDIT "
                    "FROM CUSTOMERS", True),
    ("V_ORDER_YM", "SELECT ORDER_ID, TO_CHAR(ORDER_DATE, 'YYYY-MM') YM, EXTRACT(MONTH FROM ORDER_DATE) MON "
                   "FROM ORDERS", True),
    ("V_NO_ORDERS", "SELECT CUSTOMER_ID FROM CUSTOMERS MINUS SELECT CUSTOMER_ID FROM ORDERS", True),
    ("V_BIG_CUST", "WITH t AS (SELECT CUSTOMER_ID, SUM(AMOUNT) S FROM ORDERS GROUP BY CUSTOMER_ID) "
                   "SELECT c.NAME, t.S FROM t JOIN CUSTOMERS c ON c.CUSTOMER_ID = t.CUSTOMER_ID WHERE t.S > 500",
     True),
    ("V_QTY_QUARTERS", "SELECT ORDER_ID, QTY / 4 Q4, MOD(QTY, 4) R4 FROM ORDERS", True),
    ("V_CUST_EXISTS", "SELECT CUSTOMER_ID, (SELECT COUNT(*) FROM ORDERS o WHERE o.CUSTOMER_ID = c.CUSTOMER_ID) N "
                      "FROM CUSTOMERS c WHERE EXISTS (SELECT 1 FROM ORDERS o WHERE o.CUSTOMER_ID = c.CUSTOMER_ID "
                      "AND o.STATUS = 'DONE')", True),
    ("V_ORDER_COUNT", "SELECT (SELECT COUNT(*) FROM ORDERS) CNT FROM DUAL", True),
    ("V_SPRING_ORDERS", "SELECT ORDER_ID FROM ORDERS WHERE ORDER_DATE >= TO_DATE('2024-03-01', 'YYYY-MM-DD') "
                        "AND ORDER_DATE < TO_DATE('2024-06-01', 'YYYY-MM-DD')", True),
    ("V_CASE_SIZE", "SELECT ORDER_ID, CASE WHEN AMOUNT >= 500 THEN 'L' WHEN AMOUNT >= 100 THEN 'M' ELSE 'S' END SZ "
                    "FROM ORDERS", True),
    ("V_BIG_CUST_NAMES", "SELECT NAME FROM V_BIG_CUST WHERE S > 600", True),  # представление на представлении
    # Не переводятся автоматически: должны уйти в черновики с TODO
    ("V_PIVOT", "SELECT * FROM (SELECT CUSTOMER_ID, STATUS, AMOUNT FROM ORDERS) "
                "PIVOT (SUM(AMOUNT) FOR STATUS IN ('NEW' AS NEW_AMT, 'DONE' AS DONE_AMT))", False),
    ("V_REGEXP", "SELECT CUSTOMER_ID, REGEXP_SUBSTR(NAME, '[aeiou]+') VOWELS FROM CUSTOMERS", False),
    ("V_CTX", "SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') SCH FROM DUAL", False),
    ("V_ROWNUM_ORDER", "SELECT ORDER_ID FROM ORDERS WHERE ROWNUM <= 3 ORDER BY AMOUNT", False),
]

MSSQL_VIEWS = [
    ("vTop5Orders", "CREATE VIEW sales.vTop5Orders AS SELECT TOP 5 OrderId, Amount FROM sales.Orders "
                    "ORDER BY Amount DESC, OrderId", True),
    ("vCustBasic", "CREATE VIEW sales.vCustBasic AS SELECT CustomerId, UPPER(Name) AS NameU, "
                   "ISNULL(Region, 'N/A') AS Region FROM sales.Customers", True),
    ("vOrderFlag", "CREATE VIEW sales.vOrderFlag AS SELECT OrderId, IIF(Amount > 100, 1, 0) AS Big FROM sales.Orders",
     True),
    ("vCustLabel", "CREATE VIEW sales.vCustLabel AS SELECT CustomerId, Name + ' (' + ISNULL(Region, '?') + ')' "
                   "AS Label FROM sales.Customers", True),
    ("vOrderDue", "CREATE VIEW sales.vOrderDue AS SELECT OrderId, DATEADD(day, 30, OrderDate) AS DueDate, "
                  "DATEADD(month, 1, OrderDate) AS NextMonth FROM sales.Orders", True),
    ("vOrderAge", "CREATE VIEW sales.vOrderAge AS SELECT OrderId, DATEDIFF(day, OrderDate, '2025-01-01') AS DaysLeft "
                  "FROM sales.Orders", True),
    ("vOrderYmd", "CREATE VIEW sales.vOrderYmd AS SELECT OrderId, CONVERT(varchar(10), OrderDate, 120) AS Ymd, "
                  "YEAR(OrderDate) AS Y, MONTH(OrderDate) AS M FROM sales.Orders", True),
    ("vOrderEom", "CREATE VIEW sales.vOrderEom AS SELECT OrderId, EOMONTH(OrderDate) AS MonthEnd FROM sales.Orders",
     True),
    ("vCustStr", "CREATE VIEW sales.vCustStr AS SELECT CustomerId, LEN(Name) AS L, CHARINDEX('a', Name) AS PosA, "
                 "LEFT(Name, 2) AS L2, RIGHT(Name, 2) AS R2 FROM sales.Customers", True),
    ("vLastOrder", "CREATE VIEW sales.vLastOrder AS SELECT c.CustomerId, x.OrderId, x.Amount FROM sales.Customers c "
                   "CROSS APPLY (SELECT TOP 1 o.OrderId, o.Amount FROM sales.Orders o "
                   "WHERE o.CustomerId = c.CustomerId ORDER BY o.OrderDate DESC) x", True),
    ("vFirstOrder", "CREATE VIEW sales.vFirstOrder AS SELECT c.CustomerId, x.OrderId FROM sales.Customers c "
                    "OUTER APPLY (SELECT TOP 1 o.OrderId FROM sales.Orders o "
                    "WHERE o.CustomerId = c.CustomerId ORDER BY o.OrderDate) x", True),
    ("vOrderRank", "CREATE VIEW sales.vOrderRank AS SELECT OrderId, CustomerId, ROW_NUMBER() OVER (PARTITION BY "
                   "CustomerId ORDER BY Amount DESC) AS Rn FROM sales.Orders", True),
    ("vBigCust", "CREATE VIEW sales.vBigCust AS WITH t AS (SELECT CustomerId, SUM(Amount) AS S FROM sales.Orders "
                 "GROUP BY CustomerId) SELECT c.Name, t.S FROM t JOIN sales.Customers c "
                 "ON c.CustomerId = t.CustomerId WHERE t.S > 500", True),
    ("vCustTotals", "CREATE VIEW sales.vCustTotals AS SELECT CustomerId, COUNT(*) AS Cnt, SUM(Amount) AS Total "
                    "FROM sales.Orders GROUP BY CustomerId HAVING COUNT(*) > 1", True),
    ("vCustStatuses", "CREATE VIEW sales.vCustStatuses AS SELECT CustomerId, STRING_AGG(Status, ',') WITHIN GROUP "
                      "(ORDER BY OrderId) AS Statuses FROM sales.Orders GROUP BY CustomerId", True),
    ("vCaseSize", "CREATE VIEW sales.vCaseSize AS SELECT OrderId, CASE WHEN Amount >= 500 THEN 'L' "
                  "WHEN Amount >= 100 THEN 'M' ELSE 'S' END AS Sz FROM sales.Orders", True),
    ("vBrackets", "CREATE VIEW [sales].[vBrackets] AS SELECT [OrderId] AS [Order Id], [Qty] FROM [sales].[Orders]",
     True),
    ("vQtyDiv", "CREATE VIEW sales.vQtyDiv AS SELECT OrderId, Qty / 4 AS Q4, Qty % 4 AS R4 FROM sales.Orders", True),
    ("vCastDec", "CREATE VIEW sales.vCastDec AS SELECT OrderId, CAST(Amount * 1.2 AS decimal(12,2)) AS WithVat "
                 "FROM sales.Orders", True),
    ("vNoOrders", "CREATE VIEW sales.vNoOrders AS SELECT CustomerId FROM sales.Customers EXCEPT "
                  "SELECT CustomerId FROM sales.Orders", True),
    ("vAliasEq", "CREATE VIEW sales.vAliasEq AS SELECT Id = OrderId, Doubled = Qty * 2 FROM sales.Orders", True),
    ("vBigCustNames", "CREATE VIEW sales.vBigCustNames AS SELECT Name FROM sales.vBigCust WHERE S > 600", True),
    ("vSpring", "CREATE VIEW sales.vSpring AS SELECT OrderId FROM sales.Orders WITH (NOLOCK) "
                "WHERE OrderDate >= '2024-03-01' AND OrderDate < '2024-06-01'", True),
    # Не переводятся автоматически
    ("vXmlList", "CREATE VIEW sales.vXmlList AS SELECT c.CustomerId, STUFF((SELECT ',' + o.Status FROM sales.Orders o "
                 "WHERE o.CustomerId = c.CustomerId FOR XML PATH('')), 1, 1, '') AS Statuses "
                 "FROM sales.Customers c", False),
    ("vPivot", "CREATE VIEW sales.vPivot AS SELECT CustomerId, [NEW], [DONE] FROM (SELECT CustomerId, Status, Amount "
               "FROM sales.Orders) s PIVOT (SUM(Amount) FOR Status IN ([NEW], [DONE])) p", False),
    ("vNewId", "CREATE VIEW sales.vNewId AS SELECT OrderId, NEWID() AS Guid FROM sales.Orders", False),
    ("vFormat", "CREATE VIEW sales.vFormat AS SELECT OrderId, FORMAT(Amount, 'N2', 'ru-RU') AS Txt FROM sales.Orders",
     False),
]

# Teradata: (имя, текст, эталонный запрос PostgreSQL или None, если перевод не ожидается)
TERADATA_VIEWS = [
    ("vLastOrder", "REPLACE VIEW SALES.vLastOrder AS LOCKING ROW FOR ACCESS SEL CUSTOMER_ID, ORDER_ID, AMOUNT "
                   "FROM SALES.ORDERS QUALIFY ROW_NUMBER() OVER (PARTITION BY CUSTOMER_ID ORDER BY ORDER_DATE DESC) "
                   "= 1",
     "SELECT customer_id, order_id, amount FROM (SELECT o.*, row_number() OVER (PARTITION BY customer_id ORDER BY "
     "order_date DESC) rn FROM {s}.orders o) x WHERE rn = 1"),
    ("vZero", "CREATE VIEW SALES.vZero AS SELECT CUSTOMER_ID, ZEROIFNULL(CREDIT) CREDIT, NULLIFZERO(CREDIT) CREDIT_NZ "
              "FROM SALES.CUSTOMERS",
     "SELECT customer_id, coalesce(credit, 0), nullif(credit, 0) FROM {s}.customers"),
    ("vIndex", "CREATE VIEW SALES.vIndex AS SEL CUSTOMER_ID, INDEX(NAME, 'a') POS_A, CHARACTERS(NAME) LEN "
               "FROM SALES.CUSTOMERS",
     "SELECT customer_id, strpos(name, 'a'), length(name) FROM {s}.customers"),
    ("vFormat", "CREATE VIEW SALES.vFormat AS SEL ORDER_ID, CAST(CAST(ORDER_DATE AS DATE) AS FORMAT 'YYYY-MM-DD') D "
                "FROM SALES.ORDERS",
     "SELECT order_id, to_char(order_date, 'YYYY-MM-DD') FROM {s}.orders"),
    ("vTop", "CREATE VIEW SALES.vTop AS SEL TOP 3 ORDER_ID, AMOUNT FROM SALES.ORDERS ORDER BY AMOUNT DESC",
     "SELECT order_id, amount FROM {s}.orders ORDER BY amount DESC LIMIT 3"),
    ("vNextMonth", "CREATE VIEW SALES.vNextMonth AS SEL CUSTOMER_ID, ADD_MONTHS(CREATED, 1) NEXT_MONTH "
                   "FROM SALES.CUSTOMERS",
     "SELECT customer_id, (created + interval '1 month')::date FROM {s}.customers"),
    ("vDays", "CREATE VIEW SALES.vDays AS SEL CUSTOMER_ID, DATE '2025-01-01' - CREATED DAYS_SINCE FROM SALES.CUSTOMERS",
     "SELECT customer_id, DATE '2025-01-01' - created FROM {s}.customers"),
    ("vReplace", "CREATE VIEW SALES.vReplace AS SEL CUSTOMER_ID, OREPLACE(NAME, 'a', '*') N FROM SALES.CUSTOMERS",
     "SELECT customer_id, replace(name, 'a', '*') FROM {s}.customers"),
    ("vMod", "CREATE VIEW SALES.vMod AS SEL ORDER_ID, QTY MOD 4 R, QTY / 4 Q FROM SALES.ORDERS",
     "SELECT order_id, qty % 4, qty / 4 FROM {s}.orders"),
    ("vTotals", "CREATE VIEW SALES.vTotals AS SEL CUSTOMER_ID, COUNT(*) CNT, SUM(AMOUNT) TOTAL FROM SALES.ORDERS "
                "GROUP BY 1 HAVING COUNT(*) > 1",
     "SELECT customer_id, count(*), sum(amount) FROM {s}.orders GROUP BY 1 HAVING count(*) > 1"),
    ("vCase", "CREATE VIEW SALES.vCase AS SEL ORDER_ID, CASE WHEN AMOUNT >= 500 THEN 'L' ELSE 'S' END SZ, "
              "TRIM(STATUS) ST FROM SALES.ORDERS",
     "SELECT order_id, CASE WHEN amount >= 500 THEN 'L' ELSE 'S' END, trim(status) FROM {s}.orders"),
    ("vRankQ", "CREATE VIEW SALES.vRankQ AS SEL EMP_ID, SALARY FROM SALES.EMPLOYEES "
               "QUALIFY RANK() OVER (ORDER BY SALARY DESC) <= 3",
     "SELECT emp_id, salary FROM (SELECT e.*, rank() OVER (ORDER BY salary DESC) r FROM {s}.employees e) x "
     "WHERE r <= 3"),
    # Не переводятся автоматически
    ("vSample", "CREATE VIEW SALES.vSample AS SEL * FROM SALES.ORDERS SAMPLE 5", None),
    ("vHashRow", "CREATE VIEW SALES.vHashRow AS SEL ORDER_ID, HASHROW(ORDER_ID) H FROM SALES.ORDERS", None),
]
TERADATA_COLUMNS = {
    "CUSTOMERS": ("customer_id integer, name varchar(50), region varchar(20), created date, credit numeric(12,2)"),
    "ORDERS": ("order_id integer, customer_id integer, order_date timestamp(0), amount numeric(12,2), qty integer, "
               "status varchar(10)"),
    "EMPLOYEES": "emp_id integer, name varchar(50), manager_id integer, salary numeric(10,2), hired date",
}


# Словарь Oracle для набора (как выгрузка ALL_*): модель строится без живого Oracle, для модульных тестов
ORACLE_DICT_COLUMNS = {
    "CUSTOMERS": [("CUSTOMER_ID", "NUMBER", 10, 0, "N"), ("NAME", "VARCHAR2", 50, None, "N"),
                  ("REGION", "VARCHAR2", 20, None, "Y"), ("CREATED", "DATE", None, None, "Y"),
                  ("CREDIT", "NUMBER", 12, 2, "Y")],
    "ORDERS": [("ORDER_ID", "NUMBER", 10, 0, "N"), ("CUSTOMER_ID", "NUMBER", 10, 0, "N"),
               ("ORDER_DATE", "DATE", None, None, "N"), ("AMOUNT", "NUMBER", 12, 2, "Y"),
               ("QTY", "NUMBER", 5, 0, "Y"), ("STATUS", "VARCHAR2", 10, None, "Y")],
    "EMPLOYEES": [("EMP_ID", "NUMBER", 10, 0, "N"), ("NAME", "VARCHAR2", 50, None, "Y"),
                  ("MANAGER_ID", "NUMBER", 10, 0, "Y"), ("SALARY", "NUMBER", 10, 2, "Y"),
                  ("HIRED", "DATE", None, None, "Y")],
    "CUST_SUMMARY": [("CUSTOMER_ID", "NUMBER", 10, 0, "N"), ("TOTAL", "NUMBER", 14, 2, "Y"),
                     ("CNT", "NUMBER", 10, 0, "Y"), ("LVL", "VARCHAR2", 1, None, "Y")],
}


def write_oracle_dict(folder, owner: str = "SALES") -> None:
    """CSV словаря Oracle для таблиц и представлений набора."""
    import csv
    from pathlib import Path

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    def write(name: str, header: list[str], rows: list[list]) -> None:
        with (folder / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    write("ALL_TABLES", ["OWNER", "TABLE_NAME", "PARTITIONED", "NUM_ROWS", "TEMPORARY"],
          [[owner, t, "NO", len(DATA.get(t, [])), "N"] for t in ORACLE_DICT_COLUMNS])
    cols = []
    for t, columns in ORACLE_DICT_COLUMNS.items():
        for i, (c, tp, p, s, null) in enumerate(columns, start=1):
            length = 7 if tp == "DATE" else 22 if tp == "NUMBER" else p
            char = p if tp == "VARCHAR2" else 0
            cols.append([owner, t, c, i, tp, length, char, p if tp == "NUMBER" else "",
                         s if tp == "NUMBER" else "", null])
    write("ALL_TAB_COLUMNS", ["OWNER", "TABLE_NAME", "COLUMN_NAME", "COLUMN_ID", "DATA_TYPE", "DATA_LENGTH",
                              "CHAR_LENGTH", "DATA_PRECISION", "DATA_SCALE", "NULLABLE"], cols)
    write("ALL_CONSTRAINTS", ["OWNER", "CONSTRAINT_NAME", "CONSTRAINT_TYPE", "TABLE_NAME", "R_OWNER",
                              "R_CONSTRAINT_NAME", "STATUS"],
          [[owner, f"PK_{t}", "P", t, "", "", "ENABLED"] for t in ORACLE_DICT_COLUMNS])
    write("ALL_CONS_COLUMNS", ["OWNER", "CONSTRAINT_NAME", "TABLE_NAME", "COLUMN_NAME", "POSITION"],
          [[owner, f"PK_{t}", t, c[0][0], 1] for t, c in ORACLE_DICT_COLUMNS.items()])
    write("ALL_VIEWS", ["OWNER", "VIEW_NAME", "TEXT"], [[owner, n, text] for n, text, _ in ORACLE_VIEWS])
    # Процедуры: в словаре текст по строкам и без «CREATE OR REPLACE»
    objects, source = [], []
    for name, text, _ in ORACLE_ROUTINES:
        kind = "FUNCTION" if " FUNCTION " in text.split("(", 1)[0] + " " else "PROCEDURE"
        objects.append([owner, name, kind, "VALID"])
        body = text.replace("CREATE OR REPLACE ", "", 1)
        source += [[owner, name, kind, i, line + "\n"] for i, line in enumerate(body.split("\n"), start=1)]
    write("ALL_OBJECTS", ["OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS"], objects)
    write("ALL_SOURCE", ["OWNER", "NAME", "TYPE", "LINE", "TEXT"], source)


# --- процедуры: (имя, текст, переводится ли без TODO, вызов для проверки или None) ---
# Таблица итогов, которую заполняют процедуры: в источнике и в цели создаётся пустой
ORACLE_SUMMARY = ("CREATE TABLE CUST_SUMMARY (CUSTOMER_ID NUMBER(10) PRIMARY KEY, TOTAL NUMBER(14,2), "
                  "CNT NUMBER(10), LVL VARCHAR2(1))")
MSSQL_SUMMARY = "CREATE TABLE sales.CustSummary (CustomerId int PRIMARY KEY, Total decimal(14,2), Cnt int, Lvl char(1))"

ORACLE_ROUTINES = [
    ("F_ORDER_LEVEL", """CREATE OR REPLACE FUNCTION F_ORDER_LEVEL(p_amount IN NUMBER) RETURN VARCHAR2 IS
  v_level VARCHAR2(1);
BEGIN
  IF p_amount IS NULL THEN
    RETURN NULL;
  ELSIF p_amount >= 500 THEN
    v_level := 'L';
  ELSIF p_amount >= 100 THEN
    v_level := 'M';
  ELSE
    v_level := 'S';
  END IF;
  RETURN v_level;
END F_ORDER_LEVEL;""", True),
    ("P_REFRESH_SUMMARY", """CREATE OR REPLACE PROCEDURE P_REFRESH_SUMMARY(p_min_amount IN NUMBER, p_rows OUT NUMBER) IS
  v_total NUMBER(14,2) := 0;
  v_cnt PLS_INTEGER;
  CURSOR c_cust IS
    SELECT CUSTOMER_ID, SUM(AMOUNT) S FROM ORDERS WHERE AMOUNT >= p_min_amount GROUP BY CUSTOMER_ID;
BEGIN
  DELETE FROM CUST_SUMMARY;
  p_rows := 0;
  FOR r IN c_cust LOOP
    SELECT COUNT(*) INTO v_cnt FROM ORDERS WHERE CUSTOMER_ID = r.CUSTOMER_ID AND AMOUNT >= p_min_amount;
    IF r.S > 1000 THEN
      v_total := r.S * 0.9;
    ELSIF r.S > 500 THEN
      v_total := r.S * 0.95;
    ELSE
      v_total := NVL(r.S, 0);
    END IF;
    INSERT INTO CUST_SUMMARY (CUSTOMER_ID, TOTAL, CNT, LVL)
    VALUES (r.CUSTOMER_ID, ROUND(v_total, 2), v_cnt, F_ORDER_LEVEL(v_total));
    p_rows := p_rows + SQL%ROWCOUNT;
  END LOOP;
  MERGE INTO CUST_SUMMARY t
  USING (SELECT CUSTOMER_ID FROM CUSTOMERS) c ON (t.CUSTOMER_ID = c.CUSTOMER_ID)
  WHEN NOT MATCHED THEN INSERT (CUSTOMER_ID, TOTAL, CNT, LVL) VALUES (c.CUSTOMER_ID, 0, 0, 'S');
  p_rows := p_rows + SQL%ROWCOUNT;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    p_rows := -1;
END P_REFRESH_SUMMARY;""", True),
    ("P_RAISE_NEW", """CREATE OR REPLACE PROCEDURE P_RAISE_NEW(p_pct IN NUMBER DEFAULT 10) IS
  i PLS_INTEGER := 0;
BEGIN
  FOR x IN (SELECT ORDER_ID FROM ORDERS WHERE STATUS = 'NEW') LOOP
    UPDATE ORDERS SET AMOUNT = ROUND(AMOUNT * (1 + p_pct / 100), 2) WHERE ORDER_ID = x.ORDER_ID;
    i := i + 1;
  END LOOP;
  WHILE i > 0 LOOP
    i := i - 1;
  END LOOP;
END;""", True),
    ("P_BULK", """CREATE OR REPLACE PROCEDURE P_BULK IS
  TYPE t_ids IS TABLE OF NUMBER;
  v_ids t_ids;
BEGIN
  SELECT ORDER_ID BULK COLLECT INTO v_ids FROM ORDERS;
  FORALL i IN 1 .. v_ids.COUNT
    UPDATE ORDERS SET QTY = QTY WHERE ORDER_ID = v_ids(i);
END;""", False),
]
# Как проверить: вызов в источнике и в цели, затем сравнение таблицы
# (процедура, вызов в источнике, аргументы в цели, таблица для сравнения после вызова)
ORACLE_CALLS = [
    ("P_REFRESH_SUMMARY", "BEGIN P_REFRESH_SUMMARY(50, :out); END;", (50,), "CUST_SUMMARY"),
    ("P_RAISE_NEW", "BEGIN P_RAISE_NEW(10); END;", (10,), "ORDERS"),
]
# (функция, запрос в источнике, запрос в цели: {f} — функция, {s} — схема)
ORACLE_FUNCTION_CHECKS = [
    ("F_ORDER_LEVEL", "SELECT ORDER_ID, F_ORDER_LEVEL(AMOUNT) FROM ORDERS",
     "SELECT order_id, {f}(amount) FROM {s}.orders"),
]

MSSQL_ROUTINES = [
    ("fnOrderLevel", """CREATE FUNCTION sales.fnOrderLevel(@Amount decimal(12,2)) RETURNS char(1)
AS
BEGIN
  DECLARE @Level char(1)
  IF @Amount IS NULL RETURN NULL
  IF @Amount >= 500
    SET @Level = 'L'
  ELSE IF @Amount >= 100
    SET @Level = 'M'
  ELSE
    SET @Level = 'S'
  RETURN @Level
END""", True),
    ("RefreshSummary", """CREATE PROCEDURE sales.RefreshSummary @MinAmount decimal(12,2) = 0, @Rows int OUTPUT
AS
BEGIN
  SET NOCOUNT ON;
  DECLARE @Total decimal(14,2) = 0, @Cnt int, @Cust int
  DELETE FROM sales.CustSummary
  SET @Rows = 0
  IF OBJECT_ID('tempdb..#t') IS NOT NULL DROP TABLE #t
  SELECT CustomerId, SUM(Amount) AS S INTO #t FROM sales.Orders WHERE Amount >= @MinAmount GROUP BY CustomerId
  DECLARE c CURSOR FOR SELECT CustomerId, S FROM #t
  OPEN c
  FETCH NEXT FROM c INTO @Cust, @Total
  WHILE @@FETCH_STATUS = 0
  BEGIN
    SELECT @Cnt = COUNT(*) FROM sales.Orders WHERE CustomerId = @Cust AND Amount >= @MinAmount
    IF @Total > 1000
      SET @Total = @Total * 0.9
    ELSE IF @Total > 500
      SET @Total = @Total * 0.95
    INSERT INTO sales.CustSummary (CustomerId, Total, Cnt, Lvl)
    VALUES (@Cust, ROUND(@Total, 2), @Cnt, sales.fnOrderLevel(@Total))
    SET @Rows = @Rows + @@ROWCOUNT
    FETCH NEXT FROM c INTO @Cust, @Total
  END
  CLOSE c
  DEALLOCATE c
  BEGIN TRY
    INSERT INTO sales.CustSummary (CustomerId, Total, Cnt, Lvl)
    SELECT CustomerId, 0, 0, 'S' FROM sales.Customers c2
    WHERE NOT EXISTS (SELECT 1 FROM sales.CustSummary s WHERE s.CustomerId = c2.CustomerId)
    SET @Rows = @Rows + @@ROWCOUNT
  END TRY
  BEGIN CATCH
    SET @Rows = -1
  END CATCH
  DROP TABLE #t
END""", True),
    ("RaiseNew", """CREATE PROCEDURE sales.RaiseNew @Pct decimal(5,2) = 10
AS
  UPDATE sales.Orders SET Amount = ROUND(Amount * (1 + @Pct / 100), 2) WHERE Status = 'NEW'""", True),
    ("DynSql", """CREATE PROCEDURE sales.DynSql @Table sysname
AS
BEGIN
  DECLARE @sql nvarchar(max) = N'SELECT COUNT(*) FROM ' + QUOTENAME(@Table)
  EXEC sp_executesql @sql
END""", False),
]
MSSQL_CALLS = [
    ("RefreshSummary", "DECLARE @r int; EXEC sales.RefreshSummary @MinAmount = 50, @Rows = @r OUTPUT", (50,),
     "CUSTSUMMARY"),
    ("RaiseNew", "EXEC sales.RaiseNew @Pct = 10", (10,), "ORDERS"),
]
MSSQL_FUNCTION_CHECKS = [
    ("fnOrderLevel", "SELECT OrderId, sales.fnOrderLevel(Amount) FROM sales.Orders",
     "SELECT orderid, {f}(amount) FROM {s}.orders"),
]

TERADATA_ROUTINES = [
    ("mRefresh", """REPLACE MACRO SALES.mRefresh (min_amt DECIMAL(12,2), st VARCHAR(10)) AS (
  DEL FROM SALES.CUST_SUMMARY ALL;
  INS INTO SALES.CUST_SUMMARY (CUSTOMER_ID, TOTAL, CNT)
  SEL CUSTOMER_ID, SUM(AMOUNT), COUNT(*) FROM SALES.ORDERS WHERE AMOUNT >= :min_amt GROUP BY 1;
  UPD SALES.ORDERS SET STATUS = :st WHERE STATUS IS NULL;
);""", True,
     "SELECT customer_id, sum(amount), count(*) FROM {s}.orders WHERE amount >= 50 GROUP BY 1"),
    ("pCount", """REPLACE PROCEDURE SALES.pCount (IN p_min DECIMAL(12,2), OUT p_cnt INTEGER)
BEGIN
  DECLARE v_i INTEGER DEFAULT 0;
  SET p_cnt = 0;
  FOR r AS c1 CURSOR FOR SEL CUSTOMER_ID, SUM(AMOUNT) AS S FROM SALES.ORDERS GROUP BY 1 DO
    IF r.S >= p_min THEN
      SET p_cnt = p_cnt + 1;
    END IF;
  END FOR;
  L1: WHILE v_i < 3 DO
    SET v_i = v_i + 1;
  END WHILE L1;
END;""", True, None),
]
TERADATA_SUMMARY = "customer_id integer, total numeric(14,2), cnt integer, lvl varchar(1)"
