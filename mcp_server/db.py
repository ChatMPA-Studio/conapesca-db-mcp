"""
Database connection layer.
Abstracts MySQL (prod) and SQLite (dev) behind the same interface.
"""

from __future__ import annotations
import logging
from typing import Any

from mcp_server.config import USE_SQLITE, SQLITE_PATH

logger = logging.getLogger("conapesca_mcp.db")

DEFAULT_MAX_ROWS = 5000
DEFAULT_TIMEOUT  = 60


# ── Connection helpers -------------------------------------------------------

def _mysql_connect():
    import pymysql
    from mcp_server.config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=DEFAULT_TIMEOUT,
        read_timeout=DEFAULT_TIMEOUT,
        ssl={"ca": None},
    )


def _sqlite_connect():
    import sqlite3
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_connection():
    """Return an open DB connection (caller must close it)."""
    if USE_SQLITE:
        return _sqlite_connect()
    return _mysql_connect()


# ── Query helpers ------------------------------------------------------------

def _rows_to_dicts(rows) -> list[dict]:
    """Normalise sqlite3.Row / pymysql dict rows to plain dicts."""
    if not rows:
        return []
    first = rows[0]
    if isinstance(first, dict):
        return list(rows)
    return [dict(r) for r in rows]


def execute_select(
    sql: str,
    params: tuple | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> list[dict[str, Any]]:
    """Execute a SELECT and return rows as list-of-dicts, capped at max_rows.

    Tool code may use either '?' (SQLite style) or '%s' (MySQL style) as
    placeholders — this function normalises to the correct style for the
    active backend before executing.
    """
    from mcp_server.security import validate_sql, enforce_limit
    sql = enforce_limit(validate_sql(sql), max_rows)
    if not USE_SQLITE:
        sql = sql.replace("%", "%%")  # escape literal % before pymysql sees them
        sql = sql.replace("?", "%s")
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


def execute_raw(sql: str) -> list[dict[str, Any]]:
    """Execute SHOW / DESCRIBE / PRAGMA — no LIMIT injection."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


def get_db_version_log() -> list[dict[str, Any]]:
    """Return all rows from db_version_log ordered by uploaded_at DESC.
    Returns [] if the table does not exist (e.g. SQLite dev mode)."""
    try:
        return execute_raw("SELECT * FROM db_version_log ORDER BY uploaded_at DESC")
    except Exception:
        return []


def test_connection() -> dict[str, str]:
    """Return DB metadata for health checks."""
    if USE_SQLITE:
        rows = execute_raw("SELECT sqlite_version() AS version")
        return {"mode": "sqlite", "version": rows[0].get("version", "?"),
                "path": SQLITE_PATH}
    rows = execute_raw("SELECT VERSION() AS version, DATABASE() AS db, USER() AS user")
    r = rows[0] if rows else {}
    return {"mode": "mysql", "version": r.get("version", "?"),
            "database": r.get("db", "?"), "user": r.get("user", "?")}
