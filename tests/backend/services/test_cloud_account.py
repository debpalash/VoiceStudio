"""The backend's view of the Electron account bridge (account sign-in S0-10)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from services import cloud_account


@pytest.fixture
def bridge():
    calls = []
    paths = []
    answer = {"body": {"signed_in": True, "access_token": "acc", "expires_at": 4_102_444_800_000}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            calls.append(self.headers.get("Authorization"))
            paths.append(self.path)
            payload = json.dumps(answer["body"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cloud_account._configure(f"http://127.0.0.1:{server.server_port}", "cap")
    yield calls, answer, paths
    server.shutdown()
    cloud_account._configure("", "")


def test_bridge_env_is_removed_from_os_environ(monkeypatch):
    import importlib
    import os

    monkeypatch.setenv("VOICESTUDIO_ACCOUNT_BRIDGE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN", "cap")
    importlib.reload(cloud_account)
    assert "VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN" not in os.environ
    assert "VOICESTUDIO_ACCOUNT_BRIDGE_URL" not in os.environ
    cloud_account._configure("", "")


def test_returns_the_token(bridge):
    calls, _, _ = bridge
    assert cloud_account.access_token() == "acc"
    assert calls == ["Bearer cap"]


def test_switching_off_takes_effect_on_the_very_next_call(bridge):
    """No backend-side cache: main's answer is the switch, asked every time."""
    _, answer, _ = bridge
    assert cloud_account.access_token() == "acc"
    answer["body"] = {"signed_in": False}
    assert cloud_account.access_token() is None


def test_a_non_object_answer_is_no_token(bridge):
    _, answer, _ = bridge
    answer["body"] = ["not", "an", "object"]
    assert cloud_account.access_token() is None


def test_signed_out_or_switched_off_is_none(bridge):
    _, answer, _ = bridge
    answer["body"] = {"signed_in": False}
    assert cloud_account.access_token() is None


def test_without_electron_there_is_no_token():
    cloud_account._configure("", "")
    assert cloud_account.access_token() is None


def test_refuses_a_non_loopback_bridge():
    cloud_account._configure("http://example.com:80", "cap")
    assert cloud_account.access_token() is None
    cloud_account._configure("", "")


SESSION = {"signed_in": True, "access_token": "acc", "expires_at": 1,
           "organization_id": "Or5tG7hJ9kL1zX3c", "sync_api": "https://appapi.voicestudio.sh/v1/sync"}


def test_session_carries_the_organization_and_sync_endpoint(bridge):
    _, answer, _ = bridge
    answer["body"] = dict(SESSION)
    assert cloud_account.session() == {
        "access_token": "acc", "organization_id": "Or5tG7hJ9kL1zX3c",
        "sync_api": "https://appapi.voicestudio.sh/v1/sync",
    }


@pytest.mark.parametrize("bad", [{"organization_id": None}, {"sync_api": "http://evil.example/v1/sync"}, {"sync_api": None}])
def test_session_is_none_without_a_usable_organization_or_endpoint(bridge, bad):
    _, answer, _ = bridge
    answer["body"] = {**SESSION, **bad}
    assert cloud_account.session() is None


def test_a_local_development_sync_endpoint_is_allowed(bridge):
    _, answer, _ = bridge
    answer["body"] = {**SESSION, "sync_api": "http://127.0.0.1:8090/v1/sync"}
    assert cloud_account.session()["sync_api"] == "http://127.0.0.1:8090/v1/sync"


def test_fresh_asks_main_to_replace_a_rejected_token(bridge):
    _, _, paths = bridge
    cloud_account.access_token(fresh=True)
    cloud_account.access_token()
    assert paths == ["/token?fresh=1", "/token"]
