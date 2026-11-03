"""Backend image health check: one-off job containers are not marked unhealthy."""

from __future__ import annotations

from backend import healthcheck


def write_cmdline(tmp_path, *argv):
    path = tmp_path / "cmdline"
    path.write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
    return path


def test_server_container_is_checked(tmp_path):
    assert healthcheck.runs_dashboard_server(write_cmdline(tmp_path, "python", "-m", "backend.dashboard_server"))


def test_refresh_and_other_one_off_containers_are_not(tmp_path):
    for job in ("backend.update_dashboard_data", "backend.database.init_db", "backend.auth.create_admin"):
        assert not healthcheck.runs_dashboard_server(write_cmdline(tmp_path, "python", "-m", job))


def test_unknown_main_process_is_checked_as_before(tmp_path):
    assert healthcheck.runs_dashboard_server(tmp_path / "missing")


def test_one_off_container_reports_healthy_without_a_server(monkeypatch):
    monkeypatch.setattr(healthcheck, "runs_dashboard_server", lambda: False)
    assert healthcheck.main() == 0


def test_server_container_without_an_answer_is_unhealthy(monkeypatch):
    monkeypatch.setattr(healthcheck, "runs_dashboard_server", lambda: True)
    monkeypatch.setenv("DASHBOARD_PORT", "1")  # nothing listens there
    assert healthcheck.main() == 1
