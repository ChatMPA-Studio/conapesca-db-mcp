from mcp_server.config import PORT, MCP_BASE_PATH, setup_logging, print_startup_summary
from mcp_server.server import mcp

setup_logging()
print_startup_summary()
mcp.run(transport="http", host="0.0.0.0", port=PORT, path=MCP_BASE_PATH)
