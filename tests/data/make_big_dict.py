"""Большая синтетическая выгрузка словаря Oracle для проверки скорости и памяти импорта (10 000+ таблиц).

Запуск: uv run python tests/data/make_big_dict.py ПАПКА [ЧИСЛО_ТАБЛИЦ] — тест test_dict_scale вызывает make_oracle.
"""

import csv
import sys
from pathlib import Path

TYPES = [("NUMBER", 22, 0, 12, 0), ("VARCHAR2", 400, 100, "", ""), ("DATE", 7, 0, "", ""),
         ("NUMBER", 22, 0, 15, 2), ("TIMESTAMP(6)", 11, 0, "", 6), ("CHAR", 2, 2, "", ""), ("CLOB", 4000, 0, "", "")]


def _write(folder: Path, name: str, header: list[str], rows) -> None:
    with (folder / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def make_oracle(folder: Path, tables: int = 10_000, columns: int = 30, schemas: int = 10) -> Path:
    """tables таблиц по columns колонок в schemas схемах; у каждой первичный ключ, у каждой десятой секции,
    по одному представлению на 5 таблиц и по пакету на 10 таблиц."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    names = [(f"S{i % schemas:02d}", f"T{i:06d}") for i in range(tables)]
    _write(folder, "ALL_TABLES", ["OWNER", "TABLE_NAME", "PARTITIONED", "NUM_ROWS", "TEMPORARY"],
           ([s, t, "YES" if i % 10 == 0 else "NO", i * 10, "N"] for i, (s, t) in enumerate(names)))
    _write(folder, "ALL_TAB_COLUMNS", ["OWNER", "TABLE_NAME", "COLUMN_NAME", "COLUMN_ID", "DATA_TYPE", "DATA_LENGTH",
                                       "CHAR_LENGTH", "DATA_PRECISION", "DATA_SCALE", "NULLABLE"],
           ([s, t, "ID" if c == 0 else ("DT" if c == 2 else f"C{c:03d}"), c + 1, *TYPES[c % len(TYPES)],
             "N" if c == 0 else "Y"] for s, t in names for c in range(columns)))
    _write(folder, "ALL_CONSTRAINTS", ["OWNER", "CONSTRAINT_NAME", "CONSTRAINT_TYPE", "TABLE_NAME", "R_OWNER",
                                       "R_CONSTRAINT_NAME", "STATUS"],
           ([s, f"PK_{t}", "P", t, "", "", "ENABLED"] for s, t in names))
    _write(folder, "ALL_CONS_COLUMNS", ["OWNER", "CONSTRAINT_NAME", "TABLE_NAME", "COLUMN_NAME", "POSITION"],
           ([s, f"PK_{t}", t, "ID", 1] for s, t in names))
    _write(folder, "ALL_TAB_COMMENTS", ["OWNER", "TABLE_NAME", "TABLE_TYPE", "COMMENTS"],
           ([s, t, "TABLE", f"Таблица {t}"] for s, t in names))
    _write(folder, "ALL_COL_COMMENTS", ["OWNER", "TABLE_NAME", "COLUMN_NAME", "COMMENTS"],
           ([s, t, "ID", "Идентификатор"] for s, t in names))
    part = [(s, t) for i, (s, t) in enumerate(names) if i % 10 == 0]
    _write(folder, "ALL_PART_TABLES", ["OWNER", "TABLE_NAME", "PARTITIONING_TYPE", "SUBPARTITIONING_TYPE",
                                       "INTERVAL"], ([s, t, "RANGE", "NONE", ""] for s, t in part))
    _write(folder, "ALL_PART_KEY_COLUMNS", ["OWNER", "NAME", "OBJECT_TYPE", "COLUMN_NAME", "COLUMN_POSITION"],
           ([s, t, "TABLE", "DT", 1] for s, t in part))
    _write(folder, "ALL_TAB_PARTITIONS", ["TABLE_OWNER", "TABLE_NAME", "PARTITION_NAME", "PARTITION_POSITION",
                                          "HIGH_VALUE"],
           ([s, t, f"P{y}", y - 2019, f"TO_DATE(' {y}-01-01 00:00:00', 'SYYYY-MM-DD HH24:MI:SS')"]
            for s, t in part for y in range(2020, 2027)))
    # Одна таблица своей схемы без схемы в тексте, вторая — из соседней схемы с явной схемой
    views = [(s, f"V{i:06d}", names[i + schemas][1], ".".join(names[i + 1]))
             for i, (s, _) in enumerate(names[:-schemas]) if i % 5 == 0]
    _write(folder, "ALL_VIEWS", ["OWNER", "VIEW_NAME", "TEXT"],
           ([s, v, f"SELECT a.id, b.c001\n  FROM {a} a\n  JOIN {b} b ON b.id = a.id"] for s, v, a, b in views))
    pkgs = [(s, f"PKG{i:06d}") for i, (s, _) in enumerate(names) if i % 10 == 0]
    _write(folder, "ALL_OBJECTS", ["OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS"],
           ([s, p, "PACKAGE BODY", "VALID"] for s, p in pkgs))
    _write(folder, "ALL_SOURCE", ["OWNER", "NAME", "TYPE", "LINES"], ([s, p, "PACKAGE BODY", 300] for s, p in pkgs))
    return folder


if __name__ == "__main__":
    make_oracle(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 10_000)
