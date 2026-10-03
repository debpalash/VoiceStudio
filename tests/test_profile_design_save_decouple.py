"""Design-profile save is decoupled from TTS render (issue #476).

On a fresh model-less image (e.g. Docker first-run), saving a *design* voice
profile used to force a full TTS model load + inference to render an identity
sample, which 503'd when no model was present — so the save failed. Saving a
design profile is a pure persistence operation: it must succeed without a
loaded model. The deterministic identity sample is rendered lazily on first
preview/use instead.

This module runs torch-free against an isolated data dir and drives the real
`create_profile` / `get_profile_audio` endpoint coroutines directly. Each test
uses `asyncio.run(...)` (NOT a shared/`get_event_loop()` loop) and the module
lives at top-level `tests/` (not `tests/backend/`) to avoid the
sys.modules-isolation collection-order leak.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_VD = {"Gender": "female", "Age": "young adult", "Pitch": "high pitch"}
_VD_AUTO = {"Gender": "Auto", "Age": "Auto", "Pitch": "Auto"}


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Isolated data dir + freshly-reloaded config/db/profiles modules."""
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    import core.config as cfg
    importlib.reload(cfg)
    import core.db as db
    importlib.reload(db)
    from api.routers import profiles as prof
    importlib.reload(prof)
    db.init_db()
    return cfg, db, prof


def _model_unavailable(monkeypatch):
    """Simulate a model-less image: the shared renderer 503s on model load."""
    async def _boom(a, out_path):
        raise RuntimeError("503: no TTS model is downloaded yet")

    from api.routers import archetypes as arch
    monkeypatch.setattr(arch, "_render_archetype_wav", _boom)


def test_design_save_creates_row_when_model_unavailable(iso, monkeypatch):
    """kind=design saves (lazy path) instead of 503-ing when no model exists."""
    _, db, prof = iso
    _model_unavailable(monkeypatch)

    result = asyncio.run(
        prof.create_profile(
            name="Designed (no model)",
            ref_audio=None,
            ref_text="",
            instruct="female, young adult, high pitch",
            language="English",
            seed=None,
            personality="",
            kind="design",
            vd_states=json.dumps(_VD),
        )
    )
    assert result["kind"] == "design"

    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT kind, ref_audio_path, instruct, vd_states FROM voice_profiles WHERE id=?",
            (result["id"],),
        ).fetchone()
    assert row is not None, "row must be persisted even with no model"
    assert row["kind"] == "design"
    # Sample is pending — no rendered identity wav was forced at save time.
    assert not row["ref_audio_path"]
    # #983: vd_states is completed to all 6 known categories before persisting
    # (missing ones default to 'Auto') — _VD only sets 3, so the stored value
    # is a superset of it, not an exact match.
    stored = json.loads(row["vd_states"])
    assert stored == {**_VD, "Style": "Auto", "EnglishAccent": "Auto", "ChineseDialect": "Auto"}


def test_cold_design_save_does_not_load_engine(iso, monkeypatch):
    """An offline/cold renderer cannot hold the persistence request open."""
    _, db, prof = iso
    from api.routers import archetypes as arch
    from services import model_manager
    monkeypatch.setattr(model_manager, "get_model_status", lambda: {"loaded": False})
    renders = []

    async def blocked_render(*args):
        renders.append(args)
        await asyncio.Event().wait()

    monkeypatch.setattr(arch, "_render_archetype_wav", blocked_render)

    async def save():
        return await asyncio.wait_for(prof.create_profile(
            name="Offline design", ref_audio=None, ref_text="", instruct="female",
            language="English", seed=None, personality="", kind="design",
            vd_states=json.dumps(_VD), image=None,
        ), timeout=1)

    result = asyncio.run(save())
    assert not renders
    with db.db_conn() as conn:
        row = conn.execute("SELECT ref_audio_path FROM voice_profiles WHERE id=?", (result["id"],)).fetchone()
    assert row is not None and not row["ref_audio_path"]


def test_warm_design_save_preserves_identity_sample(iso, monkeypatch):
    """A resident engine still renders the reference used by later synthesis."""
    cfg, db, prof = iso
    from api.routers import archetypes as arch
    from services import model_manager
    monkeypatch.setattr(model_manager, "get_model_status", lambda: {"loaded": True})
    renders = []

    async def render(recipe, out_path, *, allow_model_load):
        assert allow_model_load is False
        renders.append(recipe)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"identity sample")

    monkeypatch.setattr(arch, "_render_archetype_wav", render)
    result = asyncio.run(prof.create_profile(
        name="Warm design", ref_audio=None, ref_text="Sample line", instruct="female",
        language="English", seed=None, personality="", kind="design",
        vd_states=json.dumps(_VD), image=None,
    ))
    assert len(renders) == 1 and renders[0]["sample_script"] == "Sample line"
    with db.db_conn() as conn:
        row = conn.execute("SELECT ref_audio_path FROM voice_profiles WHERE id=?", (result["id"],)).fetchone()
    assert (Path(cfg.VOICES_DIR) / row["ref_audio_path"]).read_bytes() == b"identity sample"


@pytest.mark.parametrize("on_gpu_worker", [False, True])
def test_design_save_cannot_cold_load_after_residency_check(iso, monkeypatch, on_gpu_worker):
    """ASR/idle unload between admission and rendering must keep save local."""
    _, db, prof = iso
    from api.routers import archetypes, generation
    from services import model_manager
    monkeypatch.setattr(model_manager, "model", object())
    monkeypatch.setattr(model_manager, "get_model_status", lambda: {"loaded": True})
    monkeypatch.setattr(model_manager, "running_on_gpu_pool", lambda: on_gpu_worker)
    monkeypatch.setattr(generation, "get_model", model_manager.get_model)
    real_render = archetypes._render_archetype_wav
    loads = []

    async def blocked_load():
        loads.append(True)
        await asyncio.Event().wait()

    def inline_load():
        loads.append(True)
        raise RuntimeError("Unexpected cold load on GPU worker")

    async def unload_then_render(*args, **kwargs):
        model_manager.model = None
        return await real_render(*args, **kwargs)

    monkeypatch.setattr(model_manager, "_load_model_with_timeout", blocked_load)
    monkeypatch.setattr(model_manager, "_load_model_exclusive", inline_load)
    monkeypatch.setattr(archetypes, "_render_archetype_wav", unload_then_render)

    async def save():
        return await asyncio.wait_for(prof.create_profile(
            name="Unloaded during save", ref_audio=None, ref_text="", instruct="female",
            language="English", seed=None, personality="", kind="design",
            vd_states=json.dumps(_VD), image=None,
        ), timeout=1)

    result = asyncio.run(save())
    assert not loads
    with db.db_conn() as conn:
        row = conn.execute("SELECT ref_audio_path FROM voice_profiles WHERE id=?", (result["id"],)).fetchone()
    assert row is not None and not row["ref_audio_path"]


def test_all_auto_design_is_saveable(iso, monkeypatch):
    """An all-Auto design (empty instruct) saves; it isn't gated on instruct."""
    _, db, prof = iso
    _model_unavailable(monkeypatch)

    result = asyncio.run(
        prof.create_profile(
            name="All Auto",
            ref_audio=None,
            ref_text="",
            instruct="",
            language="Auto",
            seed=None,
            personality="",
            kind="design",
            vd_states=json.dumps(_VD_AUTO),
        )
    )
    assert result["kind"] == "design"

    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT kind, instruct FROM voice_profiles WHERE id=?",
            (result["id"],),
        ).fetchone()
    assert row is not None
    assert row["kind"] == "design"
    assert (row["instruct"] or "") == ""


def test_lazy_sample_renders_on_first_audio_request(iso, monkeypatch):
    """A pending design sample is materialized on first /audio request."""
    _, db, prof = iso
    _model_unavailable(monkeypatch)

    result = asyncio.run(
        prof.create_profile(
            name="Lazy Sample",
            ref_audio=None,
            ref_text="",
            instruct="female, calm",
            language="English",
            seed=None,
            personality="",
            kind="design",
            vd_states=json.dumps(_VD),
        )
    )
    pid = result["id"]

    # Now the engine becomes available: the next /audio request renders + caches.
    async def _ok(a, out_path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"RIFF" + b"\x00" * 2048)

    from api.routers import archetypes as arch
    monkeypatch.setattr(arch, "_render_archetype_wav", _ok)

    resp = asyncio.run(prof.get_profile_audio(pid))
    assert getattr(resp, "status_code", 200) == 200

    with db.db_conn() as conn:
        ref = conn.execute(
            "SELECT ref_audio_path FROM voice_profiles WHERE id=?", (pid,)
        ).fetchone()["ref_audio_path"]
    assert ref, "the lazily-rendered sample should be persisted on the row"


def test_traversal_profile_id_is_rejected(iso):
    """#476 hardening / CWE-22 (CodeQL path-injection): a profile_id carrying
    path separators / `..` / NUL must 404 at the entry guard, never reaching a
    filesystem read — the audio endpoint only ever serves a direct child of
    VOICES_DIR."""
    _, _, prof = iso
    for evil in ("../../etc/passwd", "..", "a/b", "foo/../bar", "x\x00y", "/abs", ""):
        resp = asyncio.run(prof.get_profile_audio(evil))
        assert getattr(resp, "status_code", None) == 404, (evil, resp)
