"""
Configuration — reads environment variables at import time.
Supports two modes:
  PROD : MySQL  (CONAPESCA_DB_HOST + individual vars, or DATABASE_URL)
  DEV  : SQLite (USE_SQLITE=true + SQLITE_PATH)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Helpers ------------------------------------------------------------------

def _require(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Required environment variable '{name}' is missing. "
            "Check your .env file."
        )
    return val

def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()

# ── Mode detection -----------------------------------------------------------

USE_SQLITE: bool = _get("USE_SQLITE", "false").lower() in ("1", "true", "yes")

# ── SQLite (dev) -------------------------------------------------------------

SQLITE_PATH: str = _get("SQLITE_PATH", "dev/conapesca_dev.sqlite")

# ── MySQL (prod) -------------------------------------------------------------

if not USE_SQLITE:
    DATABASE_URL: str = _get("DATABASE_URL")

    if DATABASE_URL:
        # Parse DATABASE_URL: mysql://user:pass@host:port/dbname
        import urllib.parse
        # urlparse leaves user/password percent-encoded, so decode them.
        _p = urllib.parse.urlparse(DATABASE_URL)
        DB_HOST     = _p.hostname or "localhost"
        DB_PORT     = _p.port or 3306
        DB_USER     = urllib.parse.unquote(_p.username or "")
        DB_PASSWORD = urllib.parse.unquote(_p.password or "")
        DB_NAME     = (_p.path or "").lstrip("/")
    else:
        DB_HOST     = _require("CONAPESCA_DB_HOST")
        DB_PORT     = int(_get("CONAPESCA_DB_PORT", "3306"))
        DB_USER     = _require("CONAPESCA_DB_USER")
        DB_PASSWORD = _require("CONAPESCA_DB_PASSWORD")
        DB_NAME     = _require("CONAPESCA_DB_NAME")

    # Connection pool sizing (MySQL only). maxconnections = POOL_SIZE + MAX_OVERFLOW.
    DB_POOL_SIZE: int         = int(_get("DB_POOL_SIZE", "5"))
    DB_POOL_MAX_OVERFLOW: int = int(_get("DB_POOL_MAX_OVERFLOW", "5"))

# ── Server -------------------------------------------------------------------

PORT: int         = int(_get("PORT", "8000"))
MCP_BASE_PATH: str = _get("MCP_BASE_PATH", "/mcp")
LOG_LEVEL: str    = _get("LOG_LEVEL", "INFO")

# ── Caching -------------------------------------------------------------------

# TTL for the in-memory cache (mcp_server/cache.py) used by near-static tools
# (schema_snapshot, get_version, get_estados, get_offices, species_count).
CACHE_TTL_SECONDS: float = float(_get("CACHE_TTL_SECONDS", "300"))

# ── Versioning ---------------------------------------------------------------

import importlib.metadata as _meta
try:
    MCP_VERSION: str = _meta.version("conapesca-db-mcp")
except _meta.PackageNotFoundError:
    MCP_VERSION = "dev"

# Bump this when the MCP is updated for a new DB version.
# If the live db_version_log differs from this, the server logs a warning at startup.
TESTED_DB_VERSION: str = "0.0.4"

# ── Logging -----------------------------------------------------------------

import logging

def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

def print_startup_summary() -> None:
    logger = logging.getLogger("conapesca_mcp.config")
    mode = "SQLite (dev)" if USE_SQLITE else "MySQL (prod)"
    logger.info("=" * 60)
    logger.info("  CONAPESCA MCP Server")
    logger.info(f"  Mode    : {mode}")
    if USE_SQLITE:
        logger.info(f"  DB path : {SQLITE_PATH}")
    else:
        logger.info(f"  DB host : {DB_HOST}:{DB_PORT}/{DB_NAME}")
    logger.info(f"  Port    : {PORT}  |  Path: {MCP_BASE_PATH}")
    logger.info("=" * 60)
