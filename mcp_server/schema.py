"""Schema discovery — describes the conapesca_landings_historical table."""

from __future__ import annotations
from mcp_server.db import execute_raw, execute_select
from mcp_server.config import USE_SQLITE

_schema_cache: dict | None = None


def describe_table() -> list[dict]:
    if USE_SQLITE:
        return execute_raw("PRAGMA table_info(conapesca_landings_historical)")
    return execute_raw("SHOW COLUMNS FROM conapesca_landings_historical")


def get_row_count() -> int:
    rows = execute_select("SELECT COUNT(*) AS n FROM conapesca_landings_historical")
    return rows[0].get("n", 0) if rows else 0


def get_coverage() -> dict:
    """Year range, unique states and fleet types — low-cardinality, fast."""
    rows = execute_select(
        "SELECT "
        "MIN(anio_corte) AS year_min, "
        "MAX(anio_corte) AS year_max, "
        "COUNT(DISTINCT nombre_estado)  AS unique_estados, "
        "COUNT(DISTINCT tipo_aviso)     AS unique_fleet_types "
        "FROM conapesca_landings_historical"
    )
    return dict(rows[0]) if rows else {}


def build_schema_snapshot() -> dict:
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache
    coverage = get_coverage()
    _schema_cache = {
        "table": "conapesca_landings_historical",
        "columns": describe_table(),
        "row_count": get_row_count(),
        "year_min": coverage.get("year_min"),
        "year_max": coverage.get("year_max"),
        "unique_estados": coverage.get("unique_estados"),
        "unique_fleet_types": coverage.get("unique_fleet_types"),
    }
    return _schema_cache
