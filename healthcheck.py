import os, socket, sys
port = int(os.environ.get("PORT", "8000"))
try:
    s = socket.create_connection(("localhost", port), timeout=3)
    s.close()
except Exception:
    sys.exit(1)
