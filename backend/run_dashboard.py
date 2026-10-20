"""One-click local workflow: pull all data, rebuild, launch, and open dashboard.

    python -m backend.run_dashboard
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import webbrowser

from backend import config


def server_running() -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", config.DASHBOARD_PORT)) == 0


def stop_existing_dashboard_server() -> None:
    """Stop only the Python process currently serving this dashboard port."""
    command = (
        f"$connection=Get-NetTCPConnection -LocalPort {config.DASHBOARD_PORT} -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if($connection){$process=Get-CimInstance Win32_Process -Filter "
        "\"ProcessId=$($connection.OwningProcess)\"; "
        "if($process.Name -eq 'python.exe' -and "
        "($process.CommandLine -like '*backend.dashboard_server*' -or "
        "$process.CommandLine -like '*dashboard_server.py*')){"
        "Stop-Process -Id $connection.OwningProcess -Force}}"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    for _ in range(20):
        if not server_running():
            return
        time.sleep(0.25)


def main() -> int:
    result = subprocess.run([sys.executable, "-m", "backend.update_dashboard_data"], cwd=config.PROJECT_ROOT)
    if result.returncode:
        input("Update failed. Press Enter to close...")
        return result.returncode
    stop_existing_dashboard_server()
    subprocess.Popen(
        [sys.executable, "-m", "backend.dashboard_server"],
        cwd=config.PROJECT_ROOT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(20):
        if server_running():
            break
        time.sleep(0.5)
    if not server_running():
        input("Data was updated, but the local server could not start. Press Enter to close...")
        return 1
    webbrowser.open(config.DASHBOARD_PUBLIC_URL)
    input(f"Dashboard opened at {config.DASHBOARD_PUBLIC_URL}\nPress Enter to close this window...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
