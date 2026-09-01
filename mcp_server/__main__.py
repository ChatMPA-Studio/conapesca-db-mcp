import logging
from mcp_server.config import PORT, MCP_BASE_PATH, setup_logging, print_startup_summary, TESTED_DB_VERSION
from mcp_server.server import mcp

setup_logging()
print_startup_summary()

# Warn if the live DB version has moved ahead of what this MCP was tested against.
try:
    from mcp_server.db import get_db_version_log
    _logger = logging.getLogger("conapesca_mcp.version")
    _rows = get_db_version_log()
    if _rows:
        _latest = _rows[0]
        _db_ver = _latest.get("version", "?")
        if _db_ver != TESTED_DB_VERSION:
            _logger.warning(
                "DB version mismatch: live DB is at %s, MCP tested against %s. "
                "Review changelog and bump TESTED_DB_VERSION in config.py.",
                _db_ver, TESTED_DB_VERSION,
            )
        else:
            _logger.info("DB version check OK: %s", _db_ver)
except Exception as _e:
    logging.getLogger("conapesca_mcp.version").warning("Could not check DB version: %s", _e)

mcp.run(transport="http", host="0.0.0.0", port=PORT, path=MCP_BASE_PATH)
