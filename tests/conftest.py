import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"

# Генераторы демо-набора лежат вне пакета, в demo/tools
sys.path.insert(0, str(DEMO / "tools"))
