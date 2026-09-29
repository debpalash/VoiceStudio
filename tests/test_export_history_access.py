"""Export metadata is available to authenticated remote consumers, not strangers."""
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.mark.parametrize("credential", ["local", "key", "pin", "anonymous", "bare-server", "invalid-key"])
def test_export_history_access(tmp_path, monkeypatch, credential):
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
    with TestClient(app, client=(host, 50000), headers=headers) as client:
        expected = 403 if credential in {"anonymous", "invalid-key"} else 200
        assert client.post("/export/record", json={"filename": "clip.wav", "destination_path": "/private/operator/clip.wav"}).status_code == expected
        history = client.get("/export/history")
        assert history.status_code == expected
        if expected == 200:
            assert history.json()[0]["filename"] == "clip.wav"
            expected_path = "" if credential == "bare-server" else "/private/operator/clip.wav"
            assert history.json()[0]["destination_path"] == expected_path
        if credential != "local":
            assert client.post("/export/reveal", json={"path": "clip.wav"}).status_code == 403
