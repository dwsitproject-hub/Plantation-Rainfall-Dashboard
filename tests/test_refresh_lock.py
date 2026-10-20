"""The PostgreSQL advisory lock that stops two data refreshes running at once."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from backend import config, update_dashboard_data
from backend.database import repository

HOLD_LOCK = (
    "import time\n"
    "from backend.database import repository\n"
    "with repository.refresh_lock():\n"
    "    print('locked', flush=True)\n"
    "    time.sleep(120)\n"
)


def test_lock_is_exclusive_and_released_on_exit(db):
    with repository.refresh_lock():
        with pytest.raises(repository.RefreshAlreadyRunning, match=r"already running \(database session \d+"):
            with repository.refresh_lock():
                pass
    with repository.refresh_lock():  # free again once the holder exits
        pass


def test_lock_is_released_when_the_holder_dies(db):
    holder = subprocess.Popen([sys.executable, "-c", HOLD_LOCK], cwd=config.PROJECT_ROOT,
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(repository.RefreshAlreadyRunning):
            with repository.refresh_lock():
                pass
    finally:
        holder.kill()
        holder.wait()

    deadline = time.monotonic() + 10
    while True:  # the server notices the dropped connection almost at once
        try:
            with repository.refresh_lock():
                break
        except repository.RefreshAlreadyRunning:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def test_refresh_steps_run_while_holding_the_lock(db, monkeypatch):
    def fake_refresh():
        with pytest.raises(repository.RefreshAlreadyRunning):
            with repository.refresh_lock():
                pass
        return 0

    monkeypatch.setattr(update_dashboard_data, "refresh", fake_refresh)
    assert update_dashboard_data.main() == 0


def test_second_refresh_process_exits_with_a_clear_message(db):
    with repository.refresh_lock():
        second = subprocess.run([sys.executable, "-m", "backend.update_dashboard_data"],
                                cwd=config.PROJECT_ROOT, capture_output=True, text=True, timeout=60)

    assert second.returncode == update_dashboard_data.EXIT_ALREADY_RUNNING
    assert "Another data refresh is already running" in second.stderr
    assert "Wait for it to finish, then try again." in second.stderr
    assert "Checking database schema" not in second.stdout  # no refresh step started


def test_unreachable_database_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(config, "DATABASE_PORT", 9)  # nothing listens here
    assert update_dashboard_data.main() == 1
    assert "Could not connect to PostgreSQL to take the refresh lock" in capsys.readouterr().err
