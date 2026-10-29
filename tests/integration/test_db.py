"""Проверки с живой базой. Запуск: docker compose up -d, затем uv run pytest -m db"""

import pytest

from dbtransfer.config import load_db_settings
from dbtransfer.db import check_connection

pytestmark = pytest.mark.db


def test_greengage_is_reachable():
    info = check_connection(load_db_settings())
    assert "Greengage" in info.version or "Greenplum" in info.version
    assert info.can_create_schema
