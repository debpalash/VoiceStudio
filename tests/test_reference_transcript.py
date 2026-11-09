"""Script text used as a reference transcript (#3062).

A voice whose transcript holds words its reference audio never speaks makes
OmniVoice say those words at the start of every chunk. Take text reached
transcripts cut to 200 characters and with its script markup intact.
"""
import asyncio
import importlib
import os
import sqlite3

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")



def _rt():
    # Resolved at run time: a test that reloads app modules must not leave
    # this file holding a stale copy.
    return importlib.import_module("services.reference_transcript")


def _db():
    return importlib.import_module("core.db")


LEGACY_HISTORY_TEXT_CAP = 200  # what pre-#3062 history rows kept

LONG_TAKE = (
    "The lighthouse keeper climbed the spiral stairs every evening at dusk. "
    "[pause 1s] He wound the clockwork, trimmed the wick, and watched the "
    "ships turn away from the rocks below. [slow]Nobody ever thanked him.[/slow] "
    "He never minded at all."
)


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("Hi [pause] there", "Hi there"),
        ("Hi [PAUSE 1.5s] there", "Hi there"),
        ("[voice:Narrator] Hi", "Hi"),
        ("[speed:1.2] Hi [rate 0.9] there", "Hi there"),
        ("[emphasis]Hi[/emphasis] [spell]AI[/spell]", "Hi AI"),
        ("Say [[GIF|jif]] and [[SQL]]", "Say jif and SQL"),
        ("Ha [laughter] ha", "Ha [laughter] ha"),
        ("Line one\n[pause 2s]\nLine two", "Line one\n\nLine two"),
        ("[pause 1s]", ""),
        (None, ""),
    ],
)
def test_strip_script_markup(raw, spoken):
    assert _rt().strip_script_markup(raw) == spoken


def test_take_transcript_refuses_text_that_may_be_cut():
    take_reference_transcript = _rt().take_reference_transcript
    assert _rt().LEGACY_HISTORY_TEXT_CAP == LEGACY_HISTORY_TEXT_CAP
    assert take_reference_transcript("x" * LEGACY_HISTORY_TEXT_CAP) == ""
    assert take_reference_transcript("x" * (LEGACY_HISTORY_TEXT_CAP + 1)) == "x" * (LEGACY_HISTORY_TEXT_CAP + 1)
    assert take_reference_transcript("Hi [pause 1s] there") == "Hi there"


def test_omnivoice_never_receives_markup_in_a_transcript():
    from services.tts_backend import omnivoice_ref_text

    assert omnivoice_ref_text(None, "Hi [pause 1s] there") == "Hi there"
    assert omnivoice_ref_text(None, "[pause 1s]") is None


@pytest.fixture
def history_db(tmp_path, monkeypatch):
    gen = importlib.import_module("api.routers.generation")
    db_path = tmp_path / "t.db"

    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    db = _db()
    monkeypatch.setattr(db, "get_db", _connect)
    db.ensure_schema()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(gen, "OUTPUTS_DIR", str(outputs))
    return gen


def test_a_long_take_keeps_its_whole_text_and_offers_a_clean_transcript(history_db):
    gen = history_db
    assert len(LONG_TAKE) > LEGACY_HISTORY_TEXT_CAP
    asyncio.run(gen._finalize_generation(
        torch.zeros(1, 2400), 24000, text=LONG_TAKE, history_mode="clone", ref_audio_path=None,
        language="Auto", instruct="", resolved_profile_id=None, used_seed=1, start_time=0.0,
    ))
    (item,) = gen.list_history()
    assert item["text"] == LONG_TAKE
    assert item["transcript"] == _rt().strip_script_markup(LONG_TAKE)
    assert "[pause" not in item["transcript"] and item["transcript"].endswith("He never minded at all.")


def test_a_take_saved_before_the_fix_offers_no_transcript(history_db):
    gen = history_db
    asyncio.run(gen._finalize_generation(
        torch.zeros(1, 2400), 24000, text=LONG_TAKE, history_mode="clone", ref_audio_path=None,
        language="Auto", instruct="", resolved_profile_id=None, used_seed=1, start_time=0.0,
    ))
    with _db().db_conn() as conn:
        conn.execute("UPDATE generation_history SET text=?", (LONG_TAKE[:LEGACY_HISTORY_TEXT_CAP],))
        conn.commit()
    (item,) = gen.list_history()
    assert item["transcript"] == ""


def test_stored_transcripts_for_dub_audiobook_and_stream():
    # The routes that read a profile row directly share one rule (#3062).
    stored = _rt().stored_reference_transcript
    locked = {"is_locked": 1, "locked_audio_path": "p_locked.wav"}
    assert stored({**locked, "ref_text": "x" * 200}) is None
    assert stored({**locked, "ref_text": "x" * 100}) is None
    assert stored({**locked, "ref_text": "One. [pause 1s] Two."}) == "One. Two."
    assert stored({"is_locked": 0, "ref_text": ""}) is None
