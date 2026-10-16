"""One-click local workflow: pull all data, rebuild, launch, and open dashboard."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
URL = "http://192.168.1.82:8080/"


def server_running() -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", 8080)) == 0


def stop_existing_dashboard_server() -> None:
    """Stop only the Python process currently serving this dashboard port."""
    command = (
        "$connection=Get-NetTCPConnection -LocalPort 8080 -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if($connection){$process=Get-CimInstance Win32_Process -Filter "
        "\"ProcessId=$($connection.OwningProcess)\"; "
        "if($process.Name -eq 'python.exe' -and "
        "($process.CommandLine -like '*dashboard_server.py*' -or "
        "$process.CommandLine -like '*02_launch_dashboard.py*')){" 
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
    result = subprocess.run([sys.executable, str(ROOT / "update_dashboard_data.py")], cwd=ROOT)
    if result.returncode:
        input("Update failed. Press Enter to close...")
        return result.returncode
    stop_existing_dashboard_server()
    subprocess.Popen(
        [sys.executable, str(ROOT / "dashboard_server.py")],
        cwd=ROOT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(20):
        if server_running():
            break
        time.sleep(0.5)
    if not server_running():
        input("Data was updated, but the local server could not start. Press Enter to close...")
        return 1
    webbrowser.open(URL)
    input(f"Dashboard opened at {URL}\nPress Enter to close this window...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
