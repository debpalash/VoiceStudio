"""Consent-locked voice profiles (parity program Wave 0.2, Action 22).

Endpoint tests run against an isolated tmp data dir (pattern from
tests/test_dub_transcribe.py); the migration test drives alembic
programmatically against a fixture DB (pattern from
tests/backend/services/test_settings_store.py).
"""

import os
import sqlite3
import sys

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

def _wav_bytes(seconds: float = 0.2, rate: int = 16000) -> bytes:
    """A real, decodable WAV above the _MIN_CONSENT_AUDIO_BYTES floor."""
    import io
    import math
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        n = int(seconds * rate)
        out.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(n)
        ))
    return buf.getvalue()


_FAKE_AUDIO = _wav_bytes()  # > _MIN_CONSENT_AUDIO_BYTES floor
_CONSENT_TEXT = "I confirm this is my own voice and I consent to cloning it in OmniVoice Studio."


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    """TestClient with an isolated data dir so profile/consent files land in tmp.

    Deliberately does NOT run the app lifespan (no ``with TestClient(...)``):
    startup/shutdown touch module-level asyncio primitives (event bus, job
    queues) that other test modules may have bound to a different event loop,
    which made this suite order-dependent in full-suite CI runs. The consent
    endpoints only need the DB schema, so init_db() is called directly.
    """
    mp = pytest.MonkeyPatch()
    tmp_path = tmp_path_factory.mktemp("consent-data")
    mp.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))

    import importlib
    import core.config as _cfg
    importlib.reload(_cfg)
    import core.db as _db
    importlib.reload(_db)
    from api.routers import profiles as _profiles
    importlib.reload(_profiles)
    import main as _main
    importlib.reload(_main)

    _db.init_db()

    from fastapi.testclient import TestClient
    try:
        yield TestClient(_main.app, client=("127.0.0.1", 50000)), _cfg
    finally:
        mp.undo()


def _create_profile(client) -> str:
    r = client.post(
        "/profiles",
        data={"name": "Me"},
        files={"ref_audio": ("me.wav", _FAKE_AUDIO, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_new_profile_is_unverified(app_client):
    client, _ = app_client
    pid = _create_profile(client)
    profile = client.get(f"/profiles/{pid}").json()
    assert profile["verified_own_voice"] == 0
    assert profile["consent_text"] == ""
    assert profile["consent_recorded_at"] is None


def test_record_consent_sets_flag_and_stores_audio(app_client):
    client, cfg = app_client
    pid = _create_profile(client)

    r = client.post(
        f"/profiles/{pid}/consent",
        data={"consent_text": _CONSENT_TEXT},
        files={"consent_audio": ("consent.wav", _FAKE_AUDIO, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified_own_voice"] is True
    assert body["consent_recorded_at"] is not None

    profile = client.get(f"/profiles/{pid}").json()
    assert profile["verified_own_voice"] == 1
    assert profile["consent_text"] == _CONSENT_TEXT
    assert profile["consent_audio_path"] == f"{pid}_consent.wav"
    assert os.path.exists(os.path.join(cfg.VOICES_DIR, f"{pid}_consent.wav"))


def test_rerecord_replaces_previous_consent_file(app_client):
    client, cfg = app_client
    pid = _create_profile(client)
    for ext, mime in (("wav", "audio/wav"), ("webm", "audio/webm")):
        r = client.post(
            f"/profiles/{pid}/consent",
            data={"consent_text": _CONSENT_TEXT},
            files={"consent_audio": (f"consent.{ext}", _FAKE_AUDIO, mime)},
        )
        assert r.status_code == 200, r.text
    assert not os.path.exists(os.path.join(cfg.VOICES_DIR, f"{pid}_consent.wav"))
    assert os.path.exists(os.path.join(cfg.VOICES_DIR, f"{pid}_consent.webm"))


def test_consent_validation(app_client):
    client, _ = app_client
    pid = _create_profile(client)

    # Empty statement.
    r = client.post(
        f"/profiles/{pid}/consent",
        data={"consent_text": "   "},
        files={"consent_audio": ("c.wav", _FAKE_AUDIO, "audio/wav")},
    )
    assert r.status_code == 422

    # Recording below the size floor.
    r = client.post(
        f"/profiles/{pid}/consent",
        data={"consent_text": _CONSENT_TEXT},
        files={"consent_audio": ("c.wav", b"tiny", "audio/wav")},
    )
    assert r.status_code == 422

    # Unknown profile.
    r = client.post(
        "/profiles/nope1234/consent",
        data={"consent_text": _CONSENT_TEXT},
        files={"consent_audio": ("c.wav", _FAKE_AUDIO, "audio/wav")},
    )
    assert r.status_code == 404

    # Failed attempts must not flip the flag.
    assert client.get(f"/profiles/{pid}").json()["verified_own_voice"] == 0


def test_malicious_upload_filename_cannot_steer_path(app_client):
    """py/path-injection hardening: extension whitelist + VOICES_DIR containment."""
    client, cfg = app_client
    pid = _create_profile(client)
    r = client.post(
        f"/profiles/{pid}/consent",
        data={"consent_text": _CONSENT_TEXT},
        files={"consent_audio": ("../../evil.sh/x.....", _FAKE_AUDIO, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    profile = client.get(f"/profiles/{pid}").json()
    assert profile["consent_audio_path"] == f"{pid}_consent.wav"  # fell back
    assert os.path.exists(os.path.join(cfg.VOICES_DIR, f"{pid}_consent.wav"))


def test_revoke_consent_clears_flag_and_file(app_client):
    client, cfg = app_client
    pid = _create_profile(client)
    client.post(
        f"/profiles/{pid}/consent",
        data={"consent_text": _CONSENT_TEXT},
        files={"consent_audio": ("consent.wav", _FAKE_AUDIO, "audio/wav")},
    )

    r = client.delete(f"/profiles/{pid}/consent")
    assert r.status_code == 200
    assert r.json()["verified_own_voice"] is False

    profile = client.get(f"/profiles/{pid}").json()
    assert profile["verified_own_voice"] == 0
    assert profile["consent_text"] == ""
    assert profile["consent_recorded_at"] is None
    assert not os.path.exists(os.path.join(cfg.VOICES_DIR, f"{pid}_consent.wav"))

    assert client.delete("/profiles/nope1234/consent").status_code == 404


def test_delete_profile_removes_consent_audio(app_client):
    client, cfg = app_client
    pid = _create_profile(client)
    client.post(
        f"/profiles/{pid}/consent",
        data={"consent_text": _CONSENT_TEXT},
        files={"consent_audio": ("consent.wav", _FAKE_AUDIO, "audio/wav")},
    )
    consent_path = os.path.join(cfg.VOICES_DIR, f"{pid}_consent.wav")
    assert os.path.exists(consent_path)

    assert client.delete(f"/profiles/{pid}").status_code == 200
    assert not os.path.exists(consent_path)


# ── Migration ───────────────────────────────────────────────────────────────

_PRE_CONSENT_PROFILES = """
    CREATE TABLE voice_profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        ref_audio_path TEXT,
        ref_text TEXT DEFAULT '',
        instruct TEXT DEFAULT '',
        language TEXT DEFAULT 'Auto',
        locked_audio_path TEXT DEFAULT '',
        seed INTEGER DEFAULT NULL,
        is_locked INTEGER DEFAULT 0,
        personality TEXT DEFAULT '',
        description TEXT DEFAULT '',
        is_demo INTEGER DEFAULT 0,
        created_at REAL
    );
"""


def _run_alembic(direction: str, db_path: str, target: str = "head"):
    from alembic import command
    from alembic.config import Config

    here = os.path.abspath(os.path.dirname(__file__))
    root = here
    while root and root != "/" and not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    assert os.path.isfile(os.path.join(root, "alembic.ini")), "alembic.ini not found"
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    if direction == "upgrade":
        command.upgrade(cfg, target)
    else:
        command.downgrade(cfg, target)


def _columns(db, table):
    with sqlite3.connect(str(db)) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_0003_adds_consent_columns(tmp_path):
    db = tmp_path / "pre.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(_PRE_CONSENT_PROFILES)
        conn.execute("INSERT INTO voice_profiles(id, name) VALUES ('vp-1', 'Alice')")
        conn.commit()

    _run_alembic("upgrade", str(db))

    cols = _columns(db, "voice_profiles")
    for col in ("verified_own_voice", "consent_text", "consent_audio_path", "consent_recorded_at"):
        assert col in cols, f"missing column {col}"

    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM voice_profiles WHERE id='vp-1'").fetchone()
        assert row["name"] == "Alice"  # no data loss
        assert row["verified_own_voice"] == 0  # legacy rows default unverified
        assert row["consent_text"] == ""
        assert row["consent_recorded_at"] is None


def test_migration_0003_downgrade_drops_columns(tmp_path):
    db = tmp_path / "pre.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(_PRE_CONSENT_PROFILES)
        conn.commit()

    _run_alembic("upgrade", str(db))
    _run_alembic("downgrade", str(db), target="0002_voice_profile_demo_fields")

    cols = _columns(db, "voice_profiles")
    for col in ("verified_own_voice", "consent_text", "consent_audio_path", "consent_recorded_at"):
        assert col not in cols
    assert "is_demo" in cols  # 0002 still applied


_HTML_PAGE = (b"<!doctype html><html><body><script>document.title='x'</script>" + b" " * 2000 + b"</body></html>")


def test_consent_refuses_a_non_audio_extension(app_client):
    """VulnCheck VC-CVD-01892-V: a ".html" consent upload became a stored page."""
    client, cfg = app_client
    pid = _create_profile(client)
    r = client.post(
        f"/profiles/{pid}/consent",
        data={"consent_text": _CONSENT_TEXT},
        files={"consent_audio": ("a.html", _HTML_PAGE, "audio/wav")},
    )
    assert r.status_code == 415
    assert not os.path.exists(os.path.join(cfg.VOICES_DIR, f"{pid}_consent.html"))
    assert client.get(f"/profiles/{pid}").json()["verified_own_voice"] == 0


def test_consent_refuses_content_that_is_not_audio(app_client):
    client, cfg = app_client
    pid = _create_profile(client)
    r = client.post(
        f"/profiles/{pid}/consent",
        data={"consent_text": _CONSENT_TEXT},
        files={"consent_audio": ("a.wav", _HTML_PAGE, "audio/wav")},
    )
    assert r.status_code == 422
    assert not os.path.exists(os.path.join(cfg.VOICES_DIR, f"{pid}_consent.wav"))
    assert client.get(f"/profiles/{pid}").json()["verified_own_voice"] == 0


def test_voice_audio_is_never_served_as_a_page(app_client):
    """Even a file already on disk under an HTML name is only ever a download."""
    client, cfg = app_client
    os.makedirs(cfg.VOICES_DIR, exist_ok=True)
    with open(os.path.join(cfg.VOICES_DIR, "planted.html"), "wb") as f:
        f.write(_HTML_PAGE)
    r = client.get("/voice_audio/planted.html")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == "attachment"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]

    pid = _create_profile(client)
    r = client.post(
        f"/profiles/{pid}/consent",
        data={"consent_text": _CONSENT_TEXT},
        files={"consent_audio": ("consent.wav", _FAKE_AUDIO, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    r = client.get(f"/voice_audio/{pid}_consent.wav")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/wav")
    assert "content-disposition" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"
