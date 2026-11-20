"""Export metadata is available to authenticated remote consumers, not strangers."""
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


LOCAL_CREDENTIALS = {"local", "key"}


def _client(tmp_path, monkeypatch, credential: str) -> tuple[TestClient, str]:
    from api.routers import exports

    db = tmp_path / "history.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE export_history (id TEXT, filename TEXT, destination_path TEXT, mode TEXT, created_at REAL)")

    @contextmanager
    def connection():
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            yield conn

    monkeypatch.setattr(exports, "db_conn", connection)
    monkeypatch.setattr(exports.event_bus, "emit", lambda *args: None)
    monkeypatch.setenv("OMNIVOICE_API_KEY", "test-history-key")
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    monkeypatch.delenv("OMNIVOICE_TRUSTED_NETWORKS", raising=False)
    app = FastAPI()
    app.state.network_share = SimpleNamespace(pin="123456")
    if credential == "bare-server":
        monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
        monkeypatch.delenv("OMNIVOICE_API_KEY")
        app.state.network_share.pin = None
    app.include_router(exports.router)
    headers = {"Authorization": "Bearer test-history-key"} if credential == "key" else (
        {"x-omnivoice-pin": "123456"} if credential == "pin" else {})
    host = "127.0.0.1" if credential == "local" else "203.0.113.5"
    if credential == "invalid-key":
        headers = {"Authorization": "Bearer wrong"}
    return TestClient(app, client=(host, 50000), headers=headers), credential


@pytest.mark.parametrize("credential", ["local", "key", "pin", "anonymous", "bare-server", "invalid-key"])
def test_export_history_access(tmp_path, monkeypatch, credential):
    client, _ = _client(tmp_path, monkeypatch, credential)
    with client:
        expected = 403 if credential in {"anonymous", "invalid-key"} else 200
        recorded = client.post(
            "/export/record",
            json={"filename": "clip.wav", "destination_path": "/private/operator/clip.wav", "mode": "audio"},
        )
        assert recorded.status_code == expected
        history = client.get("/export/history")
        assert history.status_code == expected
        if expected == 200:
            assert history.json()[0]["filename"] == "clip.wav"
            # The destination is a path on this host: the machine's own clients
            # (loopback, the desktop's API key) get it back, a browser on the
            # network or a PIN client does not.
            expected_path = "/private/operator/clip.wav" if credential in LOCAL_CREDENTIALS else ""
            assert history.json()[0]["destination_path"] == expected_path
        if credential != "local":
            assert client.post("/export/reveal", json={"path": "clip.wav"}).status_code == 403


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"filename": "clip.wav", "destination_path": ""}, "missing or too long"),
        ({"filename": "clip.wav", "destination_path": "x" * 5000}, "missing or too long"),
        ({"filename": "clip.wav", "destination_path": "/tmp/a\x00b.wav"}, "control characters"),
        # Every C0 control character and DEL, not just the four that were listed.
        ({"filename": "clip.wav", "destination_path": "/tmp/a\x1bb.wav"}, "control characters"),
        ({"filename": "clip.wav", "destination_path": "/tmp/a\x7fb.wav"}, "control characters"),
        ({"filename": "clip.wav", "destination_path": "/tmp/../../etc/passwd"}, "traverse upwards"),
        # Mixed separators: neither split alone sees this segment.
        ({"filename": "clip.wav", "destination_path": "C:/exports/..\\private/clip.wav"}, "traverse upwards"),
        ({"filename": "clip.wav", "destination_path": "/tmp/clip.wav", "mode": "shell"}, "Unknown export mode"),
    ],
)
def test_recording_an_export_rejects_an_unusable_destination(tmp_path, monkeypatch, payload, reason):
    client, _ = _client(tmp_path, monkeypatch, "local")
    with client:
        response = client.post("/export/record", json=payload)
        assert response.status_code == 400
        assert reason in response.json()["detail"]
        assert client.get("/export/history").json() == []


def test_the_recorded_path_keeps_the_name_the_desktop_saved(tmp_path, monkeypatch):
    """A POSIX name may end in a space; the history must show the file that exists."""
    client, _ = _client(tmp_path, monkeypatch, "local")
    with client:
        saved = "/tmp/Report final .wav"
        assert client.post("/export/record", json={"filename": "Report final .wav", "destination_path": saved}).status_code == 200
        assert client.get("/export/history").json()[0]["destination_path"] == saved