"""First-run onboarding seeds the demo voice profile (#621).

The demo reference and pre-rendered clone are build artifacts. If either ships
absent, onboarding is empty or its no-engine preview fails. These tests pin that
both assets are present + valid and that onboarding seeds the demo profile.
"""
import os
import sqlite3
import sys
import wave
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import core.onboarding as onboarding  # noqa: E402

_WAV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "backend", "assets", "samples", "demo_voice.wav",
)
_CLONE_WAV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "backend", "assets", "samples", "demo_clone_output.wav",
)


def test_demo_clip_is_committed_and_valid():
    """The clip must ship with the app — guards against it silently going
    missing again (the whole #621 regression)."""
    assert os.path.isfile(_WAV), (
        f"{_WAV} is missing — regenerate with scripts/build_demos.sh and commit "
        "it (it must ship for first-run onboarding)."
    )
    with wave.open(_WAV, "rb") as w:
        assert w.getnframes() > 0, "demo_voice.wav has no audio frames"
        assert w.getframerate() > 0


def test_demo_clone_preview_is_committed_and_valid():
    """The no-engine fallback must ship as real generated audio too."""
    assert os.path.isfile(_CLONE_WAV), (
        f"{_CLONE_WAV} is missing — regenerate with "
        "scripts/render_demos_omnivoice.py --only cloning and commit it."
    )
    with wave.open(_CLONE_WAV, "rb") as w:
        assert w.getnframes() > 0, "demo_clone_output.wav has no audio frames"
        assert w.getframerate() == 24000
        assert w.getnchannels() == 1


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_demo_clip_is_not_gitignored():
    """`.gitignore` has a blanket `*.wav`; the clip survives only via the
    `!backend/assets/samples/*.wav` allowlist. If that allowlist is ever
    weakened the file becomes ignored — and any build tool that walks with
    gitignore semantics (the `ignore` crate, `git archive`, Docker) would drop
    it from the bundle while it still sits in the repo. Pin that it is NOT
    ignored so the silent-drop class can't return (#621/#633)."""
    import shutil
    import subprocess
    if shutil.which("git") is None:
        import pytest
        pytest.skip("git not available")
    for rel in (
        "backend/assets/samples/demo_voice.wav",
        "backend/assets/samples/demo_clone_output.wav",
    ):
        # `git check-ignore` exits 0 (and echoes the path) when the path IS ignored.
        res = subprocess.run(
            ["git", "check-ignore", rel], cwd=_ROOT,
            capture_output=True, text=True,
        )
        assert res.returncode != 0 and not res.stdout.strip(), (
            f"{rel} is gitignored — the un-ignore allowlist in .gitignore was lost. "
            "Restore `!backend/assets/samples/*.wav` or the clip drops from builds."
        )


def test_backend_is_a_bundled_electron_resource():
    """The backend tree, including the demo clip, must ship with Electron."""
    config = Path(_ROOT, "electron", "electron-builder.config.mjs").read_text(
        encoding="utf-8"
    )
    assert "from: '../backend'" in config
    assert "to: 'backend'" in config


def _table_sql():
    return (
        "CREATE TABLE voice_profiles (id TEXT PRIMARY KEY, name TEXT, "
        "ref_audio_path TEXT, ref_text TEXT DEFAULT '', instruct TEXT DEFAULT '', "
        "language TEXT DEFAULT 'Auto', personality TEXT DEFAULT '', "
        "description TEXT DEFAULT '', is_demo INTEGER DEFAULT 0, created_at REAL)"
    )


def test_seed_creates_demo_profile_on_empty_db(tmp_path, monkeypatch):
    # File-backed DB so the row survives the conn.close() inside onboarding;
    # each get_db() call returns a fresh connection to it.
    db_path = str(tmp_path / "ob.db")
    setup = sqlite3.connect(db_path)
    setup.execute(_table_sql())
    setup.commit()
    setup.close()
    monkeypatch.setattr(onboarding, "get_db", lambda: sqlite3.connect(db_path))
    monkeypatch.setattr(onboarding, "VOICES_DIR", str(tmp_path / "voices"))

    onboarding.seed_sample_project()

    check = sqlite3.connect(db_path)
    rows = check.execute(
        "SELECT id, ref_audio_path, is_demo FROM voice_profiles"
    ).fetchall()
    check.close()
    assert len(rows) == 1
    assert rows[0][0] == onboarding.DEMO_PROFILE_ID
    assert rows[0][1] == f"{onboarding.DEMO_PROFILE_ID}.wav"   # audio copied in
    assert rows[0][2] == 1                                      # flagged demo
    assert os.path.isfile(os.path.join(str(tmp_path / "voices"), f"{onboarding.DEMO_PROFILE_ID}.wav"))


def test_seed_is_noop_when_profiles_exist(tmp_path, monkeypatch):
    _db_path = str(tmp_path / "ob.db")
    c = sqlite3.connect(_db_path)
    c.execute(_table_sql())
    c.execute("INSERT INTO voice_profiles (id, name) VALUES ('existing', 'X')")
    c.commit()
    c.close()
    monkeypatch.setattr(onboarding, "get_db", lambda: sqlite3.connect(_db_path))
    monkeypatch.setattr(onboarding, "VOICES_DIR", str(tmp_path / "voices"))

    onboarding.seed_sample_project()

    c = sqlite3.connect(_db_path)
    n = c.execute("SELECT COUNT(*) FROM voice_profiles").fetchone()[0]
    c.close()
    assert n == 1  # unchanged — no demo seeded on a non-empty DB


def test_existing_demo_profile_receives_the_latest_bundled_voice(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ob.db")
    conn = sqlite3.connect(db_path)
    conn.execute(_table_sql())
    conn.execute(
        "INSERT INTO voice_profiles (id, name, is_demo, description, ref_text) "
        "VALUES (?, ?, 1, 'old neutral description', ?)",
        (
            onboarding.DEMO_PROFILE_ID,
            onboarding.DEMO_PROFILE_NAME,
            onboarding.DEMO_REF_TEXT,
        ),
    )
    conn.commit()
    conn.close()

    bundled = tmp_path / "bundled.wav"
    bundled.write_bytes(b"new omnivoice demo")
    voices = tmp_path / "voices"
    voices.mkdir()
    copied = voices / f"{onboarding.DEMO_PROFILE_ID}.wav"
    copied.write_bytes(b"old demo")

    monkeypatch.setattr(onboarding, "get_db", lambda: sqlite3.connect(db_path))
    monkeypatch.setattr(onboarding, "VOICES_DIR", str(voices))
    monkeypatch.setattr(onboarding, "_DEMO_AUDIO", str(bundled))

    onboarding.seed_sample_project()

    assert copied.read_bytes() == bundled.read_bytes()
    conn = sqlite3.connect(db_path)
    description = conn.execute(
        "SELECT description FROM voice_profiles WHERE id=?",
        (onboarding.DEMO_PROFILE_ID,),
    ).fetchone()[0]
    conn.close()
    assert description == onboarding._DEMO_DESCRIPTION
