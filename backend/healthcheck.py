"""Docker health check for the backend image: is the dashboard server answering?

The same image also runs one-off jobs through ``docker compose run`` (the data
refresh, init_db, create_admin, imports). Those containers do not run the web
server, so checking /api/health there could only fail and would mark them
"unhealthy" for no reason. They report healthy instead; the job's own exit code
says whether it worked.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

SERVER_MODULE = b"backend.dashboard_server"


def runs_dashboard_server(cmdline_path: Path = Path("/proc/1/cmdline")) -> bool:
    """True when the container's main process is the dashboard server."""
    try:
        return SERVER_MODULE in cmdline_path.read_bytes()
    except OSError:
        return True  # cannot tell: check the server as before


def main() -> int:
    if not runs_dashboard_server():
        return 0
    port = os.environ.get("DASHBOARD_PORT", "8000")
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5)
    except Exception as exc:  # any failure (refused, timeout, HTTP 503) means unhealthy
        print(f"Health check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
