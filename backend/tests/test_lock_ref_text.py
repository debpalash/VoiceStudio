"""Locking a take keeps its whole text as the profile's reference transcript.

The lock used to cut the take's text to 100 characters. Generation pairs the
locked take with that text, so a take longer than that was cloned against a
transcript that stopped partway through the audio (#3254).
"""
from __future__ import annotations

import io
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TAKE_TEXT = (
    "Ontem à noite o vento soprou forte sobre a cidade, e as janelas da velha "
    "casa tremeram até o amanhecer, quando finalmente tudo ficou em silêncio."
)


def _wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\x00\x01" * 64)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from api.routers import profiles
    from core import db

    voices, outputs = tmp_path / "voices", tmp_path / "outputs"
    voices.mkdir()
    outputs.mkdir()
    (outputs / "take.wav").write_bytes(_wav_bytes())
    monkeypatch.setattr(profiles, "VOICES_DIR", str(voices))
    monkeypatch.setattr(profiles, "OUTPUTS_DIR", str(outputs))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    db.init_db()
    with db.db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, ref_audio_path, ref_text) "
            "VALUES ('p1', 'Narrator', '', 'original reference')"
        )
        conn.execute(
            "INSERT INTO generation_history (id, text, audio_path) VALUES ('h1', ?, 'take.wav')",
            (TAKE_TEXT,),
        )
        conn.commit()
    app = FastAPI()
    app.include_router(profiles.router)
    with TestClient(app, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def test_lock_keeps_the_whole_take_text(client):
    from core import db

    assert len(TAKE_TEXT) > 100
    response = client.post("/profiles/p1/lock", data={"history_id": "h1", "seed": "7"})
    assert response.status_code == 200, response.text
    with db.db_conn() as conn:
        row = conn.execute("SELECT ref_text, seed, is_locked FROM voice_profiles WHERE id='p1'").fetchone()
    assert row["ref_text"] == TAKE_TEXT
    assert (row["seed"], row["is_locked"]) == (7, 1)


def _lock_with_history_text(client, text):
    from core import db

    with db.db_conn() as conn:
        conn.execute("UPDATE generation_history SET text=? WHERE id='h1'", (text,))
        conn.commit()
    response = client.post("/profiles/p1/lock", data={"history_id": "h1", "seed": "7"})
    assert response.status_code == 200, response.text
    with db.db_conn() as conn:
        return conn.execute("SELECT ref_text FROM voice_profiles WHERE id='p1'").fetchone()["ref_text"]


def test_lock_drops_script_markup_from_the_transcript(client):
    # #3062: markup in a transcript is spoken at every chunk start.
    text = "Hello there. [pause 1s] [slow]Take your time.[/slow] [[VS|VoiceStudio]] [laughter]"
    assert _lock_with_history_text(client, text) == "Hello there. Take your time. VoiceStudio [laughter]"


def test_lock_leaves_a_possibly_cut_take_untranscribed(client):
    # #3062: history rows saved before the fix kept only text[:200], so a long
    # take's transcript stopped mid-word. Empty asks generation to transcribe.
    from services.reference_transcript import LEGACY_HISTORY_TEXT_CAP

    cut = ("word " * 60)[:LEGACY_HISTORY_TEXT_CAP]
    assert _lock_with_history_text(client, cut) == ""
    full = "word " * 60
    assert _lock_with_history_text(client, full) == full.strip()


def test_a_stored_transcript_loses_its_markup_at_generation():
    # Voices saved before #3062 keep working: their stored transcript is
    # cleaned when it is used, without rewriting the row.
    from api.routers.generation import _resolve_profile_conditioning

    row = {
        "kind": "clone", "is_locked": 1, "locked_audio_path": "p1_locked.wav",
        "ref_audio_path": "", "ref_text": "One. [pause 500ms] Two.", "instruct": "",
        "seed": 7, "language": "Auto",
    }
    assert _resolve_profile_conditioning(row)["ref_text"] == "One. Two."


def _voice(**patch):
    row = {
        "kind": "clone", "is_locked": 0, "locked_audio_path": "", "ref_audio_path": "p1.wav",
        "ref_text": "", "instruct": "", "seed": None, "language": "Auto",
    }
    row.update(patch)
    return row


@pytest.mark.parametrize("cut", [100, 200])
def test_an_already_locked_voice_drops_its_cut_transcript(client, cut):
    # Greptile on #3602: voices locked before #3062 (100-char cut until #3320,
    # then the 200-char history cut) keep the bug unless the cut is ignored.
    from api.routers.generation import _resolve_profile_conditioning

    cut_text = ("word " * 60)[:cut]
    row = _voice(is_locked=1, locked_audio_path="p1_locked.wav", ref_text=cut_text, seed=7)
    for request_text in (None, cut_text):  # the renderer echoes the stored text
        cond = _resolve_profile_conditioning(row, ref_text=request_text)
        assert cond["ref_audio_path"] and cond["ref_text"] is None
        assert cond["persist_ref_text"] is False  # locked transcripts never persist


def test_a_voice_saved_from_a_cut_take_drops_and_replaces_its_transcript(client):
    from api.routers.generation import _persist_profile_ref_text, _resolve_profile_conditioning
    from core import db

    full = "word " * 60
    with db.db_conn() as conn:
        conn.execute("UPDATE generation_history SET text=? WHERE id='h1'", (full[:200],))
        conn.execute("UPDATE voice_profiles SET ref_text=? WHERE id='p1'", (full[:200],))
        conn.commit()
        row = conn.execute("SELECT * FROM voice_profiles WHERE id='p1'").fetchone()
    cond = _resolve_profile_conditioning(_voice(ref_text=row["ref_text"]))
    assert cond["ref_text"] is None
    assert cond["persist_ref_text"] is True and cond["replace_ref_text"] == full[:200]
    _persist_profile_ref_text("p1", "what the clip says", replace=cond["replace_ref_text"])
    with db.db_conn() as conn:
        assert conn.execute("SELECT ref_text FROM voice_profiles WHERE id='p1'").fetchone()[0] == "what the clip says"


def test_a_typed_transcript_of_a_cut_length_is_kept(client):
    # No history take starts with it, so it is the user's own transcript.
    from api.routers.generation import _resolve_profile_conditioning

    typed = ("typed " * 40)[:200]
    cond = _resolve_profile_conditioning(_voice(ref_text=typed))
    assert cond["ref_text"] == typed.strip()
    assert cond["persist_ref_text"] is False


def test_a_transcript_never_lands_on_a_voice_that_changed_mid_transcribe(client):
    # Greptile on #3602: a lock (which may leave the transcript empty) or an
    # audio replace landing while /generate transcribes the old clip must not
    # receive that clip's words.
    from api.routers.generation import _persist_profile_ref_text
    from core import db

    def ref_text():
        with db.db_conn() as conn:
            return conn.execute("SELECT ref_text FROM voice_profiles WHERE id='p1'").fetchone()[0]

    with db.db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET ref_text='', ref_audio_path='new.wav' WHERE id='p1'")
        conn.commit()
    _persist_profile_ref_text("p1", "old clip words", ref_audio_file="old.wav")
    assert ref_text() == ""
    with db.db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET is_locked=1, locked_audio_path='p1_locked.wav' WHERE id='p1'")
        conn.commit()
    _persist_profile_ref_text("p1", "old clip words", ref_audio_file="new.wav")
    assert ref_text() == ""
    with db.db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET is_locked=0, locked_audio_path='' WHERE id='p1'")
        conn.commit()
    _persist_profile_ref_text("p1", "new clip words", ref_audio_file="new.wav")
    assert ref_text() == "new clip words"
