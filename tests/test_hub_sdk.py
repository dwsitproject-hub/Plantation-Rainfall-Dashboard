"""DWS Hub SDK settings endpoint: public, off without a key, identifies Hub-linked users by sub."""

from __future__ import annotations

import json
from pathlib import Path

from backend import dashboard_server
from backend.database import auth_repository
from tests.test_auth import Client, auth_db, make_user, server  # noqa: F401  (fixtures)

SCRIPT = "https://dwshub.kpndomain.com/api/sdk/v1/hub-sdk.js"


def sdk_config(client):
    status, _, body = client.request("GET", "/api/hub-sdk/config")
    assert status == 200
    return json.loads(body)


def enable(monkeypatch, release="3fa9c1d"):
    monkeypatch.setattr(dashboard_server.config, "HUB_SDK_SCRIPT_URL", SCRIPT)
    monkeypatch.setattr(dashboard_server.config, "HUB_SDK_APP_KEY", "dwsk_test")
    monkeypatch.setattr(dashboard_server.config, "APP_RELEASE", release)


def test_sdk_is_off_without_a_key(server, monkeypatch):
    monkeypatch.setattr(dashboard_server.config, "HUB_SDK_APP_KEY", "")
    assert sdk_config(Client(server)) == {"enabled": False}


def test_sign_in_page_gets_the_sdk_without_a_user(server, monkeypatch):
    enable(monkeypatch)
    assert sdk_config(Client(server)) == {"enabled": True, "scriptUrl": SCRIPT, "appKey": "dwsk_test",
                                          "release": "3fa9c1d", "user": None}
    assert Client(server).request("GET", "/js/hub_sdk.js")[0] == 200  # public, like the sign-in assets


def test_local_user_is_identified_by_email_and_hub_user_by_sub(server, monkeypatch):
    enable(monkeypatch, release="")
    user = make_user("siti.rahma@example.com", "regional")
    client = Client(server)
    assert client.login("siti.rahma@example.com")[0] == 200
    config = sdk_config(client)
    assert config["user"] == {"email": "siti.rahma@example.com"} and config["release"] is None
    auth_repository.link_sso_subject(user["user_id"], "hub-uuid-1")
    assert sdk_config(client)["user"] == {"email": "siti.rahma@example.com", "id": "hub-uuid-1"}


def test_every_page_loads_the_sdk_in_its_head():
    for page in Path("frontend").rglob("*.html"):
        head = page.read_text(encoding="utf-8").split("</head>")[0]
        assert '<script src="/js/hub_sdk.js"></script>' in head, page
