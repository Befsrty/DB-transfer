"""Проверки с живой базой. Запуск: docker compose up -d, затем uv run pytest -m db"""

import pytest

from dbtransfer.config import load_db_settings
from dbtransfer.db import check_connection

pytestmark = pytest.mark.db


def test_greengage_is_reachable():
    info = check_connection(load_db_settings())
    assert "Greengage" in info.version or "Greenplum" in info.version
    assert info.can_create_schema


def test_server_dialect_matches_ci_matrix():
    """В CI каждая версия Greengage должна получать свой диалект (DBT_EXPECT_DIALECT из матрицы)."""
    import os

    from dbtransfer.db import connect, server_dialect

    expected = os.environ.get("DBT_EXPECT_DIALECT")
    if not expected:
        pytest.skip("DBT_EXPECT_DIALECT не задан: проверка только для CI")
    with connect(load_db_settings()) as conn:
        assert server_dialect(conn) == expected
