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
    assert os.path.exists(os.path.join(voices, first_reference))
    # Compare the middle of the 2400-sample take: resampling can overshoot
    # its edges, and the chapter may append silence after it.
    assert sf.read(second)[0][600:1800].mean() == pytest.approx(0.2, abs=0.001)
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


def test_existing_longform_voice_snapshot_survives_relock(profile):
    from api.routers.audiobook import _build_synth
    client, _db, _voices = profile
    first = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()
    # Real longform resolver caches the reference before a worker reads it.
    running = _build_synth(default_voice='voice')
    before = running['resolve']('voice')
    old_bytes = Path(before['ref_audio']).read_bytes()
    response = client.post('/profiles/voice/lock', data={'history_id': 'second'})
    assert response.status_code == 200
    assert response.json()['locked_audio_path'] != first['locked_audio_path']
    saved = running['resolve']('voice')
    assert saved['ref_audio'] == before['ref_audio']
    assert Path(saved['ref_audio']).read_bytes() == old_bytes
    assert sf.read(saved['ref_audio'])[0].mean() == pytest.approx(0.1, abs=0.001)


def test_explicit_profile_deletion_reclaims_retained_locked_versions(profile):
    client, _db, voices = profile
    versions = []
    for take in ('first', 'second'):
        versions.append(client.post('/profiles/voice/lock', data={'history_id': take}).json()['locked_audio_path'])
    assert all(Path(voices, name).exists() for name in versions)
    unrelated = Path(voices, 'voice_locked_not-a-generated-version.wav')
    unrelated.write_bytes(b'unrelated')
    response = client.delete('/profiles/voice')
    assert response.status_code == 200, response.text
    assert not any(Path(voices, name).exists() for name in versions)
    assert unrelated.read_bytes() == b'unrelated'


def test_explicit_deletion_preserves_a_retained_version_shared_by_another_profile(profile):
    client, db, voices = profile
    old = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    with db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id,name,ref_audio_path) VALUES(?,?,?)", ('shared', 'Shared', old))
    latest = client.post('/profiles/voice/lock', data={'history_id': 'second'}).json()['locked_audio_path']
    response = client.delete('/profiles/voice')
    assert response.status_code == 200
    assert Path(voices, old).exists()
    assert not Path(voices, latest).exists()
    assert client.get('/profiles/shared/audio').status_code == 200


@pytest.mark.parametrize('relock', [False, True])
def test_profile_deletion_waits_for_live_longform_reference(profile, relock):
    from api.routers.audiobook import _build_synth
    client, db, _voices = profile
    client.post('/profiles/voice/lock', data={'history_id': 'first'})
    running = _build_synth(default_voice='voice')
    reference = running['resolve']('voice')['ref_audio']
    original = Path(reference).read_bytes()
    if relock:
        client.post('/profiles/voice/lock', data={'history_id': 'second'})
    response = client.delete('/profiles/voice')
    assert response.status_code == 409, response.text
    assert Path(reference).read_bytes() == original
    with db.db_conn() as conn:
        assert conn.execute("SELECT id FROM voice_profiles WHERE id='voice'").fetchone()
    # Dropping the actual cached resolver lets the settled profile be deleted.
    del running
    response = client.delete('/profiles/voice')
    assert response.status_code == 200, response.text
    assert not Path(reference).exists()


def test_profile_deletion_waits_for_every_cached_reader_and_ignores_other_profiles(profile):
    from api.routers.audiobook import _build_synth
    client, db, _voices = profile
    client.post('/profiles/voice/lock', data={'history_id': 'first'})
    first = _build_synth(default_voice='voice')
    old = first['resolve']('voice')['ref_audio']
    client.post('/profiles/voice/lock', data={'history_id': 'second'})
    second = _build_synth(default_voice='voice')
    latest = second['resolve']('voice')['ref_audio']
    with db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id,name,ref_audio_path) VALUES('other','Other','')")
    assert client.delete('/profiles/other').status_code == 200
    assert client.delete('/profiles/voice').status_code == 409
    del first
    assert client.delete('/profiles/voice').status_code == 409
    assert Path(old).exists() and Path(latest).exists()
    del second
    assert client.delete('/profiles/voice').status_code == 200
    assert not Path(old).exists() and not Path(latest).exists()


def test_pending_render_worker_keeps_reference_custody_after_request_owner_drops(profile):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from api.routers.audiobook import _build_synth
    client, _db, _voices = profile
    client.post('/profiles/voice/lock', data={'history_id': 'first'})
    running = _build_synth(default_voice='voice')
    path = running['resolve']('voice')['ref_audio']
    original = Path(path).read_bytes()
    entered, release = threading.Event(), threading.Event()

    def worker(resolve):
        entered.set()
        assert release.wait(5)
        return Path(resolve('voice')['ref_audio']).read_bytes()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(worker, running['resolve'])
        assert entered.wait(5)
        del running  # The HTTP request can finish before its worker does.
        try:
            assert client.delete('/profiles/voice').status_code == 409
        finally:
            release.set()
        assert result.result(timeout=5) == original
    assert client.delete('/profiles/voice').status_code == 200
    assert not Path(path).exists()


def test_live_shared_reference_does_not_block_deleting_its_original_profile(profile):
    from api.routers.audiobook import _build_synth
    client, db, _voices = profile
    locked = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    running = _build_synth(default_voice='voice')
    path = running['resolve']('voice')['ref_audio']
    with db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id,name,ref_audio_path) VALUES('shared','Shared',?)", (locked,))
    assert client.delete('/profiles/voice').status_code == 200
    assert Path(path).exists()
    assert client.delete('/profiles/shared').status_code == 409
    del running
    assert client.delete('/profiles/shared').status_code == 200
    assert not Path(path).exists()


def test_delete_does_not_adopt_a_shared_path_after_its_precommit_guard(profile, monkeypatch):
    from contextlib import contextmanager
    from api.routers import profiles
    from api.routers.audiobook import _build_synth

    client, db, voices = profile
    locked = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    running = _build_synth(default_voice='voice')
    path = running['resolve']('voice')['ref_audio']
    original = Path(path).read_bytes()
    replacement = Path(voices, 'shared-replacement.wav')
    replacement.write_bytes(original)
    with db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id,name,ref_audio_path) VALUES('shared','Shared',?)", (locked,))

    moved = False
    real_db_conn = db.db_conn

    @contextmanager
    def change_other_profile_after_delete_commit():
        nonlocal moved
        with real_db_conn() as conn:
            yield conn
        if not moved:
            with real_db_conn() as conn:
                deleted = conn.execute("SELECT 1 FROM voice_profiles WHERE id='voice'").fetchone() is None
            if deleted:
                # Actual SQLite update at the committed-delete boundary. This
                # models the public replacement writer's ref/path reset without
                # invoking that writer's separate, pre-existing cleanup path.
                moved = True
                with real_db_conn() as conn:
                    conn.execute("UPDATE voice_profiles SET ref_audio_path=?, locked_audio_path='', consent_audio_path='' WHERE id='shared'", (replacement.name,))

    monkeypatch.setattr(profiles, 'db_conn', change_other_profile_after_delete_commit)
    response = client.delete('/profiles/voice')
    assert response.status_code == 200, response.text
    assert moved
    assert Path(path).read_bytes() == original
    assert running['resolve']('voice')['ref_audio'] == path


@pytest.mark.parametrize("shared_column", ["ref_audio_path", "locked_audio_path", "consent_audio_path"])
def test_unlock_preserves_locked_reference_shared_by_another_profile(profile, shared_column):
    client, db, voices = profile
    locked = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    original = Path(voices, locked).read_bytes()
    with db.db_conn() as conn:
        conn.execute(f"INSERT INTO voice_profiles(id,name,{shared_column}) VALUES('shared','Shared',?)", (locked,))
    response = client.post('/profiles/voice/unlock')
    assert response.status_code == 200, response.text
    assert Path(voices, locked).read_bytes() == original
    with db.db_conn() as conn:
        row = conn.execute("SELECT is_locked,locked_audio_path FROM voice_profiles WHERE id='voice'").fetchone()
        assert tuple(row) == (0, '')


@pytest.mark.parametrize("retained_column", ["ref_audio_path", "consent_audio_path"])
def test_unlock_preserves_locked_file_retained_by_same_profile(profile, retained_column):
    client, db, voices = profile
    locked = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    original = Path(voices, locked).read_bytes()
    with db.db_conn() as conn:
        conn.execute(f"UPDATE voice_profiles SET {retained_column}=? WHERE id='voice'", (locked,))
    response = client.post('/profiles/voice/unlock')
    assert response.status_code == 200, response.text
    assert Path(voices, locked).read_bytes() == original


def test_unlock_keeps_pending_render_reference_then_deletion_reclaims_it(profile):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from api.routers.audiobook import _build_synth

    client, _db, voices = profile
    locked = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    running = _build_synth(default_voice='voice')
    path = running['resolve']('voice')['ref_audio']
    original = Path(path).read_bytes()
    entered, release = threading.Event(), threading.Event()

    def worker(resolve):
        entered.set()
        assert release.wait(5)
        return Path(resolve('voice')['ref_audio']).read_bytes()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(worker, running['resolve'])
        assert entered.wait(5)
        del running
        try:
            response = client.post('/profiles/voice/unlock')
            assert response.status_code == 200, response.text
            assert Path(path).read_bytes() == original
            assert client.delete('/profiles/voice').status_code == 409
        finally:
            release.set()
        assert result.result(timeout=5) == original
    assert client.delete('/profiles/voice').status_code == 200
    assert not Path(voices, locked).exists()


def test_unlock_reclaims_unused_locked_file(profile):
    client, _db, voices = profile
    locked = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    response = client.post('/profiles/voice/unlock')
    assert response.status_code == 200, response.text
    assert not Path(voices, locked).exists()


def test_failed_unlock_preserves_locked_reference_and_row(profile):
    import sqlite3
    client, db, voices = profile
    locked = client.post('/profiles/voice/lock', data={'history_id': 'first'}).json()['locked_audio_path']
    original = Path(voices, locked).read_bytes()
    with db.db_conn() as conn:
        conn.execute("CREATE TRIGGER refuse_unlock BEFORE UPDATE OF locked_audio_path ON voice_profiles "
                     "WHEN NEW.locked_audio_path = '' BEGIN SELECT RAISE(ABORT, 'unlock update failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match='unlock update failed'):
        client.post('/profiles/voice/unlock')
    assert Path(voices, locked).read_bytes() == original
    with db.db_conn() as conn:
        row = conn.execute("SELECT is_locked,locked_audio_path FROM voice_profiles WHERE id='voice'").fetchone()
        assert tuple(row) == (1, locked)
