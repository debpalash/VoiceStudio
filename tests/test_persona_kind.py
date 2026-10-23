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

def _make_bundle(metadata: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("metadata.json", json.dumps(metadata))
        zf.writestr("ref_audio.wav", b"RIFF" + b"\x00" * 512)
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


def test_marketplace_install_preserves_design_kind(iso):
    cfg, db, mk = iso
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    states = json.dumps({"Gender": "female"})
    (Path(cfg.VOICES_DIR) / "aria.wav").write_bytes(b"RIFF" + b"\x00" * 512)
    with db.db_conn() as conn:
        conn.execute(
            """INSERT INTO voice_profiles
               (id, name, ref_audio_path, instruct, kind, vd_states)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("original-aria", "Marketplace Aria", "aria.wav", "female", "design", states),
        )

    app = FastAPI()
    app.include_router(mk.router)
    with TestClient(app) as client:
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
    with TestClient(app) as client:
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
