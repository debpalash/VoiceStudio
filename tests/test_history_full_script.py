"""A take's history row keeps its whole script.

``generation_history.text`` was cut to 200 characters on write, while the
history rail's Reuse, a project's reuse, reopening a Voice Design take and
Save as profile all read it back as the script that produced the take, so a
longer script came back silently cut short. Locking a profile to a take then
cut it again to 100 characters and stored that as the locked reference's
transcript, which no longer matched the audio.

The engine layer is stubbed (no real model loads), matching
``tests/test_generate_ref_text_persist.py``.
"""
import asyncio
import importlib
import os

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_SCRIPT = " ".join(
    f"Sentence {word} of a script that is longer than the history preview."
    for word in ("one", "two", "three", "four", "five", "six")
)


def _make_fake_engine():
    tts = importlib.import_module("services.tts_backend")

    class _FakeEngine(tts.TTSBackend):
        id = "fake-history-script-engine"
        display_name = "Fake History Script Engine (test)"
        applies_own_mastering = False
        gpu_compat = ("cpu",)

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            return torch.zeros(1, 24000)

    return tts, _FakeEngine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from core.db import init_db
    from main import app

    init_db()
    return TestClient(app, client=("127.0.0.1", 50000))


def test_history_keeps_the_whole_script(client, monkeypatch):
    from core.db import db_conn

    assert len(_SCRIPT) > 200
    tts, fake = _make_fake_engine()
    monkeypatch.setitem(tts._REGISTRY, fake.id, fake)

    res = client.post("/generate", data={"text": _SCRIPT, "engine": fake.id})
    assert res.status_code == 200, res.text
    audio_id = res.headers["X-Audio-Id"]
    try:
        items = {item["id"]: item for item in client.get("/history").json()}
        assert items[audio_id]["text"] == _SCRIPT
    finally:
        with db_conn() as conn:
            conn.execute("DELETE FROM generation_history WHERE id=?", (audio_id,))


def test_locking_a_take_keeps_its_whole_transcript(tmp_path, monkeypatch):
    from api.routers import profiles
    from core import config, db

    voices = tmp_path / "voices"
    outputs = tmp_path / "outputs"
    voices.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(profiles, "VOICES_DIR", str(voices))
    monkeypatch.setattr(profiles, "OUTPUTS_DIR", str(outputs))
    monkeypatch.setattr(config, "VOICES_DIR", str(voices))
    db.init_db()
    (outputs / "take.wav").write_bytes(b"take")
    with db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id,name) VALUES('voice','V')")
        conn.execute(
            "INSERT INTO generation_history(id, text, audio_path) VALUES('take', ?, 'take.wav')",
            (_SCRIPT,),
        )

    asyncio.run(profiles.lock_profile("voice", history_id="take", seed=7))

    with db.db_conn() as conn:
        row = conn.execute("SELECT ref_text FROM voice_profiles WHERE id='voice'").fetchone()
    assert row["ref_text"] == _SCRIPT
