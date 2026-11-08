"""Uploaded profiles recover their storage directory after startup."""

import io

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_upload_recreates_missing_voice_directory(tmp_path, monkeypatch):
    from api.routers import profiles
    from core import db

    voices = tmp_path / "voices"
    monkeypatch.setattr(profiles, "VOICES_DIR", str(voices))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    db.init_db()
    app = FastAPI()
    app.include_router(profiles.router)
    audio = b"RIFF" + b"\x00" * 2000

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/profiles",
            data={"name": "Scarlet", "kind": "clone"},
            files={"ref_audio": ("reference.wav", io.BytesIO(audio), "audio/wav")},
        )
        assert response.status_code == 200, response.text
        profile = client.get(f"/profiles/{response.json()['id']}").json()
        assert profile["name"] == "Scarlet"
        assert (voices / profile["ref_audio_path"]).read_bytes() == audio
