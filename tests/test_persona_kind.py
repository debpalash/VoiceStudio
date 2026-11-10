"""Persona-gallery kind preservation (parity §R3).

A *designed* (synthetic) voice persona must keep ``kind='design'`` when it
travels through a marketplace ``.omnivoice`` bundle — otherwise it silently
becomes a clone and the gallery's synthetic-only gating can't work. These
tests run torch-free (marketplace imports no model) against an isolated data
dir; the round-trip exercises the real export-metadata + import-INSERT paths.
"""
from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture(scope="module")
def iso(tmp_path_factory):
    """Isolated data dir + reloaded config/db/marketplace (no main, no torch)."""
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("persona-kind-data")
    mp.setenv("OMNIVOICE_DATA_DIR", str(tmp))
    import core.config as cfg
    importlib.reload(cfg)
    import core.db as db
    importlib.reload(db)
    from api.routers import marketplace as mk
    importlib.reload(mk)
    db.init_db()
    try:
        yield cfg, db, mk
    finally:
        mp.undo()


# ── Pure metadata helper (export + publish) ──────────────────────────────────

def test_bundle_metadata_captures_design_kind_and_vd_states(iso):
    _, _, mk = iso
    profile = {
        "name": "Aria", "kind": "design",
        "vd_states": json.dumps({"Gender": "female", "Pitch": "high pitch"}),
        "instruct": "female, high pitch", "seed": 7,
    }
    meta = mk._bundle_metadata(profile, exported_at=123.0)
    assert meta["kind"] == "design"
    assert json.loads(meta["vd_states"])["Gender"] == "female"
    assert meta["exported_at"] == 123.0   # extras pass through


def test_bundle_metadata_defaults_to_clone(iso):
    _, _, mk = iso
    meta = mk._bundle_metadata({"name": "Rec"})   # no kind
    assert meta["kind"] == "clone"
    assert meta["vd_states"] is None


# ── Import round-trip preserves kind + vd_states ─────────────────────────────

def _wav_bytes() -> bytes:
    """A small real WAV: bundle import now refuses clips that do not decode."""
    import io as _io
    import wave as _wave

    buf = _io.BytesIO()
    with _wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\x00\x10" * 1600)
    return buf.getvalue()


def _make_bundle(metadata: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("metadata.json", json.dumps(metadata))
        zf.writestr("ref_audio.wav", _wav_bytes())
    return buf.getvalue()


def test_import_preserves_design_kind(iso):
    cfg, db, mk = iso
    from fastapi import UploadFile

    data = _make_bundle({
        "bundle_version": 1, "profile_name": "Imported Aria",
        "kind": "design",
        "vd_states": json.dumps({"Gender": "female"}),
        "instruct": "female, high pitch", "seed": 9,
    })
    file = UploadFile(filename="aria.omnivoice", file=io.BytesIO(data))
    result = asyncio.run(mk.import_profile(file))

    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT kind, vd_states FROM voice_profiles WHERE id=?",
            (result["profile_id"],),
        ).fetchone()
    assert row["kind"] == "design"
    assert json.loads(row["vd_states"])["Gender"] == "female"


def test_imported_clone_preserves_freeform_prose(iso):
    """A real marketplace import must not lose its original style on VoxCPM2."""
    _, db, marketplace = iso
    from fastapi import UploadFile
    from services.voice_design_profiles import profile_instruct

    prose = "Speak softly with warm breathiness and a gentle, wistful emotion."
    file = UploadFile(
        filename="freeform-clone.omnivoice",
        file=io.BytesIO(_make_bundle({
            "bundle_version": 1,
            "profile_name": "Freeform clone",
            "kind": "clone",
            "instruct": prose,
        })),
    )
    result = asyncio.run(marketplace.import_profile(file))
    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?",
            (result["profile_id"],),
        ).fetchone()

    assert row["kind"] == "clone"
    assert row["instruct"] == prose
    assert profile_instruct(row, "voxcpm2") == prose
    assert profile_instruct(row, "omnivoice") != prose


def test_marketplace_install_preserves_design_kind(iso):
    cfg, db, mk = iso
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    states = json.dumps({"Gender": "female"})
    (Path(cfg.VOICES_DIR) / "aria.wav").write_bytes(_wav_bytes())
    with db.db_conn() as conn:
        conn.execute(
            """INSERT INTO voice_profiles
               (id, name, ref_audio_path, instruct, kind, vd_states)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("original-aria", "Marketplace Aria", "aria.wav", "female", "design", states),
        )

    app = FastAPI()
    app.include_router(mk.router)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        published = client.post("/marketplace/publish/original-aria")
        assert published.status_code == 200
        bundle_name = os.path.basename(published.json()["bundle_path"])
        installed = client.post(f"/marketplace/install/{bundle_name}")
        assert installed.status_code == 200
        result = installed.json()

    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT kind, vd_states FROM voice_profiles WHERE id=?",
            (result["profile_id"],),
        ).fetchone()
    assert row["kind"] == "design"
    assert json.loads(row["vd_states"])["Gender"] == "female"


def test_marketplace_install_legacy_bundle_defaults_to_clone(iso):
    _, db, mk = iso
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    bundle = mk.MARKETPLACE_DIR / "legacy.omnivoice"
    bundle.write_bytes(_make_bundle({"bundle_version": 1, "profile_name": "Legacy"}))
    app = FastAPI()
    app.include_router(mk.router)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        installed = client.post("/marketplace/install/legacy.omnivoice")
    assert installed.status_code == 200
    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT kind, vd_states FROM voice_profiles WHERE id=?",
            (installed.json()["profile_id"],),
        ).fetchone()
    assert row["kind"] == "clone"
    assert row["vd_states"] is None


def test_import_legacy_bundle_defaults_to_clone(iso):
    cfg, db, mk = iso
    from fastapi import UploadFile

    # An old bundle with no kind/vd_states keys must import as a clone.
    data = _make_bundle({"bundle_version": 1, "profile_name": "Legacy"})
    file = UploadFile(filename="legacy.omnivoice", file=io.BytesIO(data))
    result = asyncio.run(mk.import_profile(file))

    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT kind, vd_states FROM voice_profiles WHERE id=?",
            (result["profile_id"],),
        ).fetchone()
    assert row["kind"] == "clone"
    assert row["vd_states"] is None


def test_import_sanitises_untrusted_kind_and_design_settings(iso):
    cfg, db, mk = iso
    from fastapi import UploadFile

    data = _make_bundle({
        "bundle_version": 1, "profile_name": "Odd",
        "kind": "admin", "vd_states": {"Gender": "female"},
    })
    result = asyncio.run(mk.import_profile(UploadFile(filename="odd.omnivoice", file=io.BytesIO(data))))

    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT kind, vd_states FROM voice_profiles WHERE id=?", (result["profile_id"],),
        ).fetchone()
    assert row["kind"] == "clone"
    assert json.loads(row["vd_states"]) == {"Gender": "female"}


# ── VC-CVD-01892-V: a bundle cannot plant a page under /voice_audio ──────────

_HTML_PAGE = b"<!doctype html><script>document.title='x'</script>" + b" " * 2000


def _bundle_with(member: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"bundle_version": 1, "profile_name": "X"}))
        zf.writestr(member, data)
    return buf.getvalue()


def _voice_files(cfg) -> set[str]:
    return {p.name for p in Path(cfg.VOICES_DIR).iterdir()} if Path(cfg.VOICES_DIR).exists() else set()


@pytest.mark.parametrize("member", ["ref_audio.html", "ref_audio.wav"])
def test_import_refuses_bundle_audio_that_is_not_audio(iso, member):
    cfg, _, mk = iso
    from fastapi import HTTPException, UploadFile

    before = _voice_files(cfg)
    file = UploadFile(filename="evil.omnivoice", file=io.BytesIO(_bundle_with(member, _HTML_PAGE)))
    with pytest.raises(HTTPException) as err:
        asyncio.run(mk.import_profile(file))
    assert err.value.status_code == 400
    assert _voice_files(cfg) == before


def test_import_never_keeps_a_non_media_extension(iso):
    cfg, db, mk = iso
    from fastapi import UploadFile

    file = UploadFile(filename="x.omnivoice", file=io.BytesIO(_bundle_with("ref_audio.html", _wav_bytes())))
    result = asyncio.run(mk.import_profile(file))
    with db.db_conn() as conn:
        ref = conn.execute(
            "SELECT ref_audio_path FROM voice_profiles WHERE id=?", (result["profile_id"],)
        ).fetchone()["ref_audio_path"]
    assert ref.endswith(".wav")


def test_persona_member_ext_is_media_only():
    from services.persona_bundle import _safe_member_ext

    assert _safe_member_ext("ref_audio.html") == ".wav"
    assert _safe_member_ext("ref_audio.svg") == ".wav"
    assert _safe_member_ext("ref_audio.MP3") == ".mp3"


@pytest.mark.parametrize("member", ["ref_audio.html", "ref_audio.wav", "consent_audio.html"])
def test_persona_import_refuses_bundle_audio_that_is_not_audio(iso, member):
    cfg, _, _ = iso
    from fastapi import HTTPException, UploadFile
    from api.routers import personas

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"bundle_version": 1, "profile_name": "X"}))
        if not member.startswith("ref_audio"):
            zf.writestr("ref_audio.wav", _wav_bytes())
        zf.writestr(member, _HTML_PAGE)
    before = _voice_files(cfg)
    file = UploadFile(filename="evil.ovsvoice", file=io.BytesIO(buf.getvalue()))
    with pytest.raises(HTTPException) as err:
        asyncio.run(personas.import_persona(file))
    assert err.value.status_code == 400, err.value.detail
    assert "could not be read" in err.value.detail
    assert _voice_files(cfg) == before
