"""Большой словарь: импорт идёт за линейное время, связи представлений и секции не теряются.
Полный замер на 10 000 таблиц: uv run python tests/data/make_big_dict.py /tmp/big && dbtransfer import ...
"""

import sys
import time
from pathlib import Path

from dbtransfer.generator import TypeRules, generate
from dbtransfer.importers import oracle_dict

sys.path.insert(0, str(Path(__file__).parents[1] / "data"))
from make_big_dict import make_oracle  # noqa: E402


def test_big_dictionary(tmp_path):
    folder = make_oracle(tmp_path, tables=2000, columns=20)
    start = time.perf_counter()
    model = oracle_dict.import_oracle_dict(folder)
    elapsed = time.perf_counter() - start
    assert len(model.tables) == 2000 and sum(len(t.fields) for t in model.tables.values()) == 40_000
    assert sum(1 for t in model.tables.values() if t.partitioning) == 200
    views = [o for o in model.others if o.name.startswith("S00.V")]
    assert views and all(len(v.related) == 2 for v in views)
    assert elapsed < 10, f"импорт 2000 таблиц занял {elapsed:.1f} с"  # обычно меньше секунды
    assert generate(model, TypeRules.load()).files()["045_tables.sql"].count("CREATE TABLE IF NOT EXISTS") >= 2000
