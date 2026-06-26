"""Schema discovery — describes the conapesca_landings table."""

from __future__ import annotations
import json
from mcp_server.db import execute_raw, execute_select
from mcp_server.config import USE_SQLITE

_schema_cache: dict | None = None


def describe_table() -> list[dict]:
    if USE_SQLITE:
        return execute_raw("PRAGMA table_info(conapesca_landings)")
    return execute_raw("SHOW COLUMNS FROM conapesca_landings")


def get_row_count() -> int:
    rows = execute_select("SELECT COUNT(*) AS n FROM conapesca_landings")
    return rows[0].get("n", 0) if rows else 0


def build_schema_snapshot() -> dict:
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache
    _schema_cache = {
        "table": "conapesca_landings",
        "columns": describe_table(),
        "row_count": get_row_count(),
    }
    return _schema_cache
