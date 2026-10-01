"""Re-locking a voice gives its replacement reference a new cache identity."""
import os
from pathlib import Path

import pytest
import soundfile as sf
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def profile(tmp_path, monkeypatch):
    from api.routers import profiles
    from core import db, config

    VOICES_DIR, OUTPUTS_DIR = str(tmp_path / 'voices'), str(tmp_path / 'outputs')
    for module in (profiles, config):
        monkeypatch.setattr(module, 'VOICES_DIR', VOICES_DIR)
        monkeypatch.setattr(module, 'OUTPUTS_DIR', OUTPUTS_DIR)

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    db.init_db()
    os.makedirs(VOICES_DIR, exist_ok=True)
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    with db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles (id,name,ref_audio_path) VALUES ('voice','Voice','')")
        for take, amplitude in [("first", 0.1), ("second", 0.2)]:
            sf.write(os.path.join(OUTPUTS_DIR, f"{take}.wav"),
                     torch.full((2400,), amplitude).numpy(), 24000)
            conn.execute("INSERT INTO generation_history (id,text,profile_id,audio_path) VALUES (?,?,?,?)",
                         (take, "Same reference text", "voice", f"{take}.wav"))
    app = FastAPI()
    app.include_router(profiles.router)
    with TestClient(app) as client:
        yield client, db, VOICES_DIR


@pytest.mark.parametrize("keep_chapter", [True, False])
def test_relock_does_not_reuse_previous_take_audio(profile, tmp_path, keep_chapter):
    from api.routers.audiobook import _render_chapter_cached, _resolve_voice
    from services.audiobook import Chapter, Span

    client, _db, voices = profile
    chapter = Chapter(title="C", spans=[Span(voice_id="voice", text="Hello.", pause_ms_after=0)])
    calls = []

    def synth(text, voice_id, speed=None):
        reference = _resolve_voice(voice_id)["ref_audio"]
        samples, _ = sf.read(reference)
        calls.append(reference)
        return torch.tensor(samples, dtype=torch.float32)

    response = client.post('/profiles/voice/lock', data={'history_id': 'first', 'seed': '7'})
    assert response.status_code == 200, response.text
    first_reference = response.json()['locked_audio_path']
    first, *_ = _render_chapter_cached(chapter, synth, 24000, 'eng', _resolve_voice, str(tmp_path / 'cache'))
    if not keep_chapter:
        os.remove(first)
    response = client.post('/profiles/voice/lock', data={'history_id': 'second', 'seed': '7'})
    assert response.status_code == 200, response.text
    second_reference = response.json()['locked_audio_path']
    second, _duration, cached, stats = _render_chapter_cached(chapter, synth, 24000, 'eng', _resolve_voice,
                                                           str(tmp_path / 'cache'))
    assert cached is False
    assert stats == {'total': 1, 'cached': 0}
    assert len(calls) == 2
    assert first != second
    assert first_reference != second_reference
    assert not os.path.exists(os.path.join(voices, first_reference))
    assert sf.read(second)[0].max() == pytest.approx(0.2, abs=0.001)
    served = client.get('/profiles/voice/audio')
    assert served.status_code == 200
    assert served.content == Path(voices, second_reference).read_bytes()


def test_failed_relock_keeps_original_reference_and_cleans_new_copy(profile):
    client, db, voices = profile
    initial = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    original = Path(voices, initial).read_bytes()
    with db.db_conn() as conn:
        conn.execute("CREATE TRIGGER refuse_lock BEFORE UPDATE OF locked_audio_path ON voice_profiles "
                     "BEGIN SELECT RAISE(ABORT, 'lock update failed'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match='lock update failed'):
        client.post('/profiles/voice/lock', data={'history_id': 'second'})
    with db.db_conn() as conn:
        assert conn.execute("SELECT locked_audio_path FROM voice_profiles WHERE id='voice'").fetchone()[0] == initial
    assert Path(voices, initial).read_bytes() == original
    assert os.listdir(voices) == [initial]


def test_relock_keeps_a_reference_still_used_by_another_profile(profile):
    """Cleanup must preserve references shared by imported profile rows."""
    client, db, voices = profile
    initial = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    original = Path(voices, initial).read_bytes()
    with db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles (id,name,ref_audio_path) VALUES (?,?,?)",
                     ('shared', 'Shared reference', initial))
    response = client.post('/profiles/voice/lock', data={'history_id': 'second'})
    assert response.status_code == 200
    assert response.json()['locked_audio_path'] != initial
    assert Path(voices, initial).read_bytes() == original
