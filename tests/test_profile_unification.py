"""Unified profiles (spec: docs/specs/voice-studio-unification.md §3/§5).

Endpoint validation tests run against an isolated tmp data dir; the design
render path is monkeypatched (no model in CI). Migration tests drive alembic
programmatically. Patterns mirror tests/test_profile_consent.py.
"""

import json
import os
import sqlite3

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_FAKE_AUDIO = b"RIFF" + b"\x00" * 2000
_VD = {"Gender": "female", "Age": "young adult", "Pitch": "high pitch"}


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    """TestClient with an isolated data dir (no lifespan — schema only)."""
    mp = pytest.MonkeyPatch()
    tmp_path = tmp_path_factory.mktemp("unified-profiles-data")
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
        yield TestClient(_main.app, client=("127.0.0.1", 50001)), _cfg
    finally:
        mp.undo()


@pytest.fixture()
def fake_render(monkeypatch):
    """Stub the design sample renderer — CI has no TTS engine."""
    async def _fake(a, out_path, **kwargs):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(_FAKE_AUDIO)

    from api.routers import archetypes as _arch
    from services import model_manager
    monkeypatch.setattr(model_manager, "get_model_status", lambda: {"loaded": True})
    monkeypatch.setattr(_arch, "_render_archetype_wav", _fake)
    return _fake


# ── Create validation ────────────────────────────────────────────────────────

def test_clone_requires_ref_audio(app_client):
    client, _ = app_client
    r = client.post("/profiles", data={"name": "NoAudio"})
    assert r.status_code == 422


def test_design_requires_vd_states(app_client):
    client, _ = app_client
    r = client.post("/profiles", data={"name": "D", "kind": "design", "instruct": "female, calm"})
    assert r.status_code == 422


def test_design_saveable_without_instruct(app_client, fake_render):
    """An all-Auto design (no picks → empty instruct) is still saveable (#476).

    Saving must not gate on a non-empty instruct — synthesis falls back to
    neutral instruct-only conditioning.
    """
    client, _ = app_client
    all_auto = {"Gender": "Auto", "Age": "Auto", "Pitch": "Auto"}
    r = client.post(
        "/profiles",
        data={"name": "AllAuto", "kind": "design", "vd_states": json.dumps(all_auto)},
    )
    assert r.status_code == 200, r.text
    profile = client.get(f"/profiles/{r.json()['id']}").json()
    assert profile["kind"] == "design"
    assert (profile["instruct"] or "") == ""


def test_design_derives_instruct_from_picks_when_unset(app_client, fake_render):
    """A design saved with category picks but no explicit instruct must persist
    a matching instruct derived from vd_states — otherwise the designed voice
    renders neutral/wrong-gender (#594). Guards the save-time heal."""
    client, _ = app_client
    r = client.post(
        "/profiles",
        data={"name": "Designed", "kind": "design", "vd_states": json.dumps(_VD)},
    )
    assert r.status_code == 200, r.text
    profile = client.get(f"/profiles/{r.json()['id']}").json()
    assert profile["instruct"] == "female, young adult, high pitch"


def test_design_save_drops_object_object_instruct(app_client, fake_render):
    """The "[object Object]" poison can never be persisted — it's sanitized and
    the design is rebuilt from vd_states at save time (#550 #571 #594)."""
    client, _ = app_client
    r = client.post(
        "/profiles",
        data={
            "name": "Poisoned", "kind": "design",
            "vd_states": json.dumps(_VD), "instruct": "[object Object]",
        },
    )
    assert r.status_code == 200, r.text
    profile = client.get(f"/profiles/{r.json()['id']}").json()
    assert profile["instruct"] == "female, young adult, high pitch"


def test_native_voice_design_profile_keeps_full_description(app_client):
    """Saving a VoxCPM2 voice cannot silently collapse its prose to tags."""
    from api.routers.generation import _resolve_profile_conditioning
    from core.db import db_conn
    from services.voice_design_profiles import profile_instruct

    client, _ = app_client
    raw = {
        "description": "A soft-spoken Japanese narrator, a little breathy and introspective.",
        "picks": {"Pitch": "low pitch"},
    }
    r = client.post("/profiles", data={
        "name": "Native Designer", "kind": "design", "engine": "voxcpm2",
        "vd_states": json.dumps(_VD), "design_recipe": json.dumps(raw),
    })
    assert r.status_code == 200, r.text
    profile = r.json()
    assert profile["ref_audio_path"] is None  # no accidental OmniVoice rendering
    assert profile["instruct"] == "female, young adult, high pitch"
    stored = json.loads(profile["design_recipe"])
    assert stored["description"] == raw["description"]
    assert stored["engine"] == "voxcpm2"
    assert stored["picks"] == raw["picks"]

    with db_conn() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile["id"],)).fetchone()
    assert profile_instruct(row, "omnivoice") == "female, young adult, high pitch"
    assert profile_instruct(row, "voxcpm2") == raw["description"] + ", low pitch"
    cond = _resolve_profile_conditioning(row, engine_id="voxcpm2")
    assert cond["instruct"] == raw["description"] + ", low pitch"
    assert cond["ref_audio_path"] is None
    # The Design workspace sends this same instruction; it must not be
    # mistaken for a different identity and clear the saved profile link.
    assert not _resolve_profile_conditioning(
        row, engine_id="voxcpm2", instruct=cond["instruct"], seed=profile["seed"]
    )["diverged"]


def test_native_profile_preview_renders_with_its_authoring_engine(app_client, monkeypatch):
    """A pending VoxCPM2 profile sample is rendered by VoxCPM2, once."""
    import wave
    from pathlib import Path

    from api.routers import profiles as profiles_router
    from core.db import db_conn

    client, _ = app_client
    recipe = {"description": "A gravelly lighthouse keeper with a slow drawl.", "picks": {}}
    created = client.post("/profiles", data={
        "name": "Lighthouse keeper", "kind": "design", "engine": "voxcpm2",
        "vd_states": json.dumps(_VD), "design_recipe": json.dumps(recipe),
    })
    assert created.status_code == 200, created.text
    pid = created.json()["id"]

    renders = []

    async def _fake_native(row, path, engine_id):
        renders.append({"recipe": json.loads(row["design_recipe"]), "engine": engine_id})
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\x00\x01" * 24000)

    monkeypatch.setattr(profiles_router, "_render_native_design_sample", _fake_native)

    first = client.get(f"/profiles/{pid}/audio")
    assert first.status_code == 200, first.text
    assert first.headers["content-type"].startswith("audio/")
    assert len(renders) == 1
    assert renders[0]["engine"] == "voxcpm2"
    assert renders[0]["recipe"]["description"] == recipe["description"]

    with db_conn() as conn:
        stored = conn.execute(
            "SELECT ref_audio_path FROM voice_profiles WHERE id=?", (pid,)
        ).fetchone()
    assert stored["ref_audio_path"] == f"{pid}.wav"

    again = client.get(f"/profiles/{pid}/audio")
    assert again.status_code == 200
    assert len(renders) == 1


def _write_identity_wave(path, amplitude: int) -> None:
    """Write a valid preview with an identifiable sample value, without TTS."""
    import wave
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(amplitude.to_bytes(2, "little", signed=True) * 4800)


@pytest.mark.asyncio
async def test_edited_voice_preview_cannot_overwrite_newer_sample(app_client, monkeypatch):
    """A slow old native preview must not replace a newer edited voice's WAV."""
    import asyncio
    from pathlib import Path

    from api.routers import archetypes as archetypes_router
    from api.routers import profiles as profiles_router
    from core.db import db_conn
    from fastapi import HTTPException

    client, cfg = app_client
    raw = {
        "description": "A soft, breathy narrator from a future city.",
        "picks": {"Gender": "female"},
    }
    created = client.post("/profiles", data={
        "name": "Race test native", "kind": "design", "engine": "voxcpm2",
        "vd_states": json.dumps(_VD), "design_recipe": json.dumps(raw),
        "instruct": "female, high pitch", "ref_text": "Old scene line",
    })
    assert created.status_code == 200, created.text
    pid = created.json()["id"]
    sample = Path(cfg.VOICES_DIR) / f"{pid}.wav"
    # A previous voice's WAV can still exist on disk after its DB link was
    # cleared; the stale preview must not overwrite the refreshed version.
    _write_identity_wave(sample, 300)
    with db_conn() as conn:
        original = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (pid,),
        ).fetchone()

    started = asyncio.Event()
    release = asyncio.Event()
    old_calls = []
    new_calls = []

    async def old_native_render(row, path, engine_id):
        """Pause the original render while the user changes the profile."""
        old_calls.append((row["instruct"], engine_id))
        started.set()
        await release.wait()
        _write_identity_wave(path, 600)

    async def new_omnivoice_render(recipe, path, *, prefix):
        """Finish a replacement preview before the old render is released."""
        new_calls.append(recipe["instruct"])
        _write_identity_wave(path, 900)
        return path

    monkeypatch.setattr(profiles_router, "_render_native_design_sample", old_native_render)
    monkeypatch.setattr(archetypes_router, "_render_wav_atomic", new_omnivoice_render)

    older = asyncio.create_task(profiles_router._materialize_design_sample(pid, original))
    try:
        await asyncio.wait_for(started.wait(), 4)
        edited = client.put(f"/profiles/{pid}", json={"instruct": "male, low pitch"})
        assert edited.status_code == 200, edited.text
        assert edited.json()["design_recipe"] is None
        assert edited.json()["ref_audio_path"] is None
        with db_conn() as conn:
            current = conn.execute(
                "SELECT * FROM voice_profiles WHERE id=?", (pid,),
            ).fetchone()

        # Versioned render keys let the current voice render immediately;
        # it must not join the blocked old preview for this same profile.
        refreshed = await asyncio.wait_for(
            profiles_router._materialize_design_sample(pid, current), 4,
        )
        assert refreshed == f"{pid}.wav"
        updated_audio = sample.read_bytes()
        import wave
        with wave.open(str(sample), "rb") as wav:
            assert wav.readframes(1) == (900).to_bytes(2, "little", signed=True)
        assert old_calls == [("female, high pitch", "voxcpm2")]
        assert new_calls == ["male, low pitch"]

        release.set()
        with pytest.raises(HTTPException) as stale:
            await asyncio.wait_for(older, 4)
        assert stale.value.status_code == 409
        assert "changed" in str(stale.value.detail)
        assert sample.read_bytes() == updated_audio

        with db_conn() as conn:
            stored = conn.execute(
                "SELECT * FROM voice_profiles WHERE id=?", (pid,),
            ).fetchone()
        assert stored["ref_audio_path"] == f"{pid}.wav"
        assert stored["instruct"] == "male, low pitch"
        assert not list(Path(cfg.VOICES_DIR).glob(f".{pid}-preview-*.wav"))
        assert not sample.with_suffix(".wav.bak").exists()
    finally:
        release.set()
        if not older.done():
            await older


@pytest.mark.parametrize(
    ("patch", "expect_stale"),
    [
        ({"name": "Renamed while previewing"}, False),
        ({"ref_text": "New scene line"}, True),
        ({"language": "Japanese"}, True),
    ],
)
@pytest.mark.asyncio
async def test_preview_tracks_all_voice_inputs_but_not_metadata(
    app_client, monkeypatch, patch, expect_stale,
):
    """Only changes affecting generated speech invalidate an in-flight WAV."""
    import asyncio
    from pathlib import Path

    from api.routers import profiles as profiles_router
    from core.db import db_conn
    from fastapi import HTTPException

    client, cfg = app_client
    created = client.post("/profiles", data={
        "name": "Profile preview input test", "kind": "design", "engine": "voxcpm2",
        "vd_states": json.dumps(_VD),
        "design_recipe": json.dumps({"description": "Bright and clear", "picks": {}}),
        "ref_text": "Original greeting", "language": "English",
    })
    assert created.status_code == 200
    pid = created.json()["id"]
    with db_conn() as conn:
        original = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (pid,),
        ).fetchone()
    started, release = asyncio.Event(), asyncio.Event()

    async def render(row, path, engine_id):
        """Wait until the profile edit is committed before finishing."""
        started.set()
        await release.wait()
        _write_identity_wave(path, 800)

    monkeypatch.setattr(profiles_router, "_render_native_design_sample", render)
    task = asyncio.create_task(profiles_router._materialize_design_sample(pid, original))
    try:
        await asyncio.wait_for(started.wait(), 4)
        edited = client.put(f"/profiles/{pid}", json=patch)
        assert edited.status_code == 200, edited.text
        assert edited.json()["ref_audio_path"] is None
        release.set()
        if expect_stale:
            with pytest.raises(HTTPException) as stale:
                await asyncio.wait_for(task, 4)
            assert stale.value.status_code == 409
            assert not (Path(cfg.VOICES_DIR) / f"{pid}.wav").exists()
        else:
            assert await asyncio.wait_for(task, 4) == f"{pid}.wav"
            assert (Path(cfg.VOICES_DIR) / f"{pid}.wav").exists()
        with db_conn() as conn:
            record = conn.execute(
                "SELECT ref_audio_path FROM voice_profiles WHERE id=?", (pid,),
            ).fetchone()
        assert record["ref_audio_path"] == (None if expect_stale else f"{pid}.wav")
    finally:
        release.set()
        if not task.done():
            await task


@pytest.mark.parametrize(
    "patch",
    [{"ref_text": "Corrected transcript"}, {"language": "Japanese"}],
)
def test_saved_voice_survives_transcript_or_language_correction(app_client, patch):
    """Correcting metadata must never discard a rendered reference voice."""
    from pathlib import Path

    client, cfg = app_client
    created = client.post("/profiles", data={
        "name": "Saved voice", "kind": "design", "engine": "voxcpm2",
        "vd_states": json.dumps(_VD),
        "design_recipe": json.dumps({"description": "Gentle and warm", "picks": {}}),
        "ref_text": "Original reference transcript", "language": "English",
    })
    assert created.status_code == 200, created.text
    pid = created.json()["id"]
    filename = f"{pid}.wav"
    audio = Path(cfg.VOICES_DIR) / filename
    _write_identity_wave(audio, 670)
    from core.db import db_conn
    with db_conn() as conn:
        conn.execute(
            "UPDATE voice_profiles SET ref_audio_path=? WHERE id=?", (filename, pid),
        )

    updated = client.put(f"/profiles/{pid}", json=patch)
    assert updated.status_code == 200, updated.text
    assert updated.json()["ref_audio_path"] == filename
    assert updated.json()["design_recipe"] is not None
    assert audio.is_file()
    assert client.get(f"/profiles/{pid}/audio").status_code == 200
    with db_conn() as conn:
        record = conn.execute(
            "SELECT ref_audio_path FROM voice_profiles WHERE id=?", (pid,),
        ).fetchone()
    assert record["ref_audio_path"] == filename


@pytest.mark.asyncio
async def test_duplicate_pending_preview_requests_share_one_render(app_client, monkeypatch):
    """Parallel requests for an unchanged design must render the WAV only once."""
    import asyncio
    from pathlib import Path

    from api.routers import profiles as profiles_router
    from core.db import db_conn

    client, cfg = app_client
    created = client.post("/profiles", data={
        "name": "Repeated preview", "kind": "design", "engine": "voxcpm2",
        "vd_states": json.dumps(_VD),
        "design_recipe": json.dumps({"description": "Gentle", "picks": {}}),
    })
    assert created.status_code == 200
    pid = created.json()["id"]
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (pid,)).fetchone()

    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def render(row, path, engine_id):
        """Hold the first expensive synthesis until the second joins it."""
        calls.append(path)
        started.set()
        await release.wait()
        _write_identity_wave(path, 1100)

    monkeypatch.setattr(profiles_router, "_render_native_design_sample", render)
    first = asyncio.create_task(profiles_router._materialize_design_sample(pid, row))
    second = None
    try:
        await asyncio.wait_for(started.wait(), 4)
        second = asyncio.create_task(profiles_router._materialize_design_sample(pid, row))
        await asyncio.sleep(0)
        release.set()
        result = await asyncio.wait_for(asyncio.gather(first, second), 4)
        assert result == [f"{pid}.wav", f"{pid}.wav"]
        assert len(calls) == 1
        assert (Path(cfg.VOICES_DIR) / f"{pid}.wav").exists()
    finally:
        release.set()
        pending = [t for t in (first, second) if t is not None and not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_deleted_profile_during_http_preview_never_reappears(app_client, monkeypatch):
    """An HTTP preview underway when a voice is deleted must return 409, not resurrect it."""
    import asyncio
    from pathlib import Path

    from api.routers import profiles as profiles_router
    from httpx import ASGITransport, AsyncClient
    from main import app

    client, cfg = app_client
    created = client.post("/profiles", data={
        "name": "Deleted while previewing", "kind": "design", "engine": "voxcpm2",
        "vd_states": json.dumps(_VD),
        "design_recipe": json.dumps({"description": "soft narration", "picks": {}}),
    })
    assert created.status_code == 200, created.text
    pid = created.json()["id"]
    started, release = asyncio.Event(), asyncio.Event()

    async def render(row, path, engine_id):
        """Emulate a slow native engine that finishes after profile deletion."""
        started.set()
        await release.wait()
        _write_identity_wave(path, 1000)

    monkeypatch.setattr(profiles_router, "_render_native_design_sample", render)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        first = asyncio.create_task(http.get(f"/profiles/{pid}/audio"))
        try:
            await asyncio.wait_for(started.wait(), 4)
            deleted = await http.delete(f"/profiles/{pid}")
            assert deleted.status_code == 200, deleted.text
            release.set()
            stale_response = await asyncio.wait_for(first, 4)
            assert stale_response.status_code == 409, stale_response.text
            assert "changed" in stale_response.json()["detail"]
            assert (await http.get(f"/profiles/{pid}")).status_code == 404
            assert not (Path(cfg.VOICES_DIR) / f"{pid}.wav").exists()
            assert not list(Path(cfg.VOICES_DIR).glob(f".{pid}-preview-*.wav"))
        finally:
            release.set()
            if not first.done():
                await first


@pytest.mark.asyncio
async def test_native_preview_reuses_engine_cache_and_sends_seed(tmp_path, monkeypatch):
    """Pending samples use the shared engine and forward seeds to sidecars."""
    import torch
    from api.routers import engines as engines_router
    from api.routers import generation as generation_router
    from api.routers import profiles as profiles_router
    from services import engine_memory, gpu_gateway, tts_backend, watermark

    class StubBackend:
        """An in-memory engine with a real WAV tensor and seed-aware protocol."""
        id = "voxcpm2"
        supports_generation_seed = True
        instruct_vocabulary = "freeform"
        sample_rate = 24000

        @classmethod
        def is_available(cls):
            """Pretend a previously installed sidecar is healthy."""
            return True, "ready"

        def generate(self, text, **kw):
            """Record the actual kwargs a sidecar would receive."""
            calls.append(kw)
            return torch.ones(1, 24000) * 0.1

    calls = []
    engine = StubBackend()
    cached = []
    prewarmed = []
    evicted = []

    def get_cached(cls):
        """Return the same adapter instance across independent previews."""
        cached.append(cls)
        return engine

    async def prewarm(*, backend, engine, **kw):
        """Record model readiness without starting GPU workers."""
        prewarmed.append((backend, engine))

    async def evict(engine_id):
        """Track the single-active-engine eviction request."""
        evicted.append(engine_id)

    async def run_guarded(fn, **kw):
        """Execute stub synthesis instead of dispatching to the real GPU."""
        return fn()

    async def passthrough(wav, sample_rate, **kw):
        """Skip provenance cost while preserving the generated tensor."""
        return wav

    monkeypatch.setattr(tts_backend, "get_backend_class", lambda name: StubBackend)
    monkeypatch.setattr(engines_router, "_get_engine_instance", get_cached)
    monkeypatch.setattr(gpu_gateway, "prewarm", prewarm)
    monkeypatch.setattr(engine_memory, "evict_other_tts_engines", evict)
    monkeypatch.setattr(generation_router, "run_on_gpu_pool_guarded", run_guarded)
    monkeypatch.setattr(watermark, "mark_synthetic_async", passthrough)

    row = {
        "kind": "design", "ref_text": "Speak softly.", "language": "Auto",
        "instruct": "female", "vd_states": None, "seed": 123,
        "design_recipe": json.dumps({
            "description": "Warm and gentle narration.", "picks": {},
            "engine": "voxcpm2",
        }),
    }
    for i in range(2):
        await profiles_router._render_native_design_sample(
            row, str(tmp_path / f"sample-{i}.wav"), "voxcpm2",
        )
        assert (tmp_path / f"sample-{i}.wav").is_file()
    assert cached == [StubBackend, StubBackend]
    assert prewarmed == [(engine, "voxcpm2")] * 2
    assert evicted == ["voxcpm2", "voxcpm2"]
    assert len(calls) == 2
    assert all(call["seed"] == 123 for call in calls)
    assert all(call["instruct"] == "Warm and gentle narration." for call in calls)


def test_editing_native_design_invalidates_stale_recipe_and_reference(app_client):
    """Legacy tag edits intentionally replace stale prose and sample identity."""
    from core.db import db_conn

    client, _ = app_client
    recipe = {
        "description": "A warm Japanese narrator, slightly breathy and restrained.",
        "picks": {"Age": "young adult"},
    }
    response = client.post("/profiles", data={
        "name": "Edited native design", "kind": "design", "engine": "voxcpm2",
        "vd_states": json.dumps(_VD), "design_recipe": json.dumps(recipe),
    })
    assert response.status_code == 200, response.text
    pid = response.json()["id"]
    assert response.json()["design_recipe"]

    # Changing only the profile name or saving the same tags preserves recipe.
    unchanged = client.put(f"/profiles/{pid}", json={"name": "New name"})
    assert unchanged.status_code == 200
    assert json.loads(unchanged.json()["design_recipe"])["description"] == recipe["description"]
    tags = unchanged.json()["instruct"]
    unchanged_tags = client.put(f"/profiles/{pid}", json={"instruct": tags})
    assert unchanged_tags.status_code == 200
    assert unchanged_tags.json()["design_recipe"]

    # An actual tag change must not secretly reapply the old free-form prose.
    with db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET ref_audio_path=? WHERE id=?", (
            f"{pid}.wav", pid,
        ))
    changed = client.put(f"/profiles/{pid}", json={"instruct": "female, low pitch"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["instruct"] == "female, low pitch"
    assert changed.json()["design_recipe"] is None
    assert changed.json()["ref_audio_path"] is None
    assert json.loads(changed.json()["vd_states"])["Pitch"] == "low pitch"


def test_native_profile_rejects_invalid_recipe_and_reference_only_engine(app_client):
    """Disallow malformed freeform inputs and reference-only design engines."""
    client, _ = app_client
    data = {
        "name": "Invalid", "kind": "design", "engine": "voxcpm2",
        "vd_states": json.dumps(_VD),
    }
    assert client.post("/profiles", data={**data, "design_recipe": "{broken"}).status_code == 422
    assert client.post("/profiles", data={
        **data, "engine": "indextts2",
        "design_recipe": json.dumps({"description": "low smoky voice", "picks": {}}),
    }).status_code == 422


def test_design_rejects_malformed_vd_states(app_client):
    client, _ = app_client
    r = client.post(
        "/profiles",
        data={"name": "D", "kind": "design", "vd_states": "[1,2]", "instruct": "female"},
    )
    assert r.status_code == 422


def test_unknown_kind_rejected(app_client):
    client, _ = app_client
    r = client.post(
        "/profiles",
        data={"name": "X", "kind": "mystery"},
        files={"ref_audio": ("x.wav", _FAKE_AUDIO, "audio/wav")},
    )
    assert r.status_code == 422


# ── Create happy paths ───────────────────────────────────────────────────────

def test_clone_create_defaults_kind(app_client):
    client, _ = app_client
    r = client.post(
        "/profiles",
        data={"name": "Clone Me"},
        files={"ref_audio": ("me.wav", _FAKE_AUDIO, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    profile = client.get(f"/profiles/{pid}").json()
    assert profile["kind"] == "clone"
    assert profile["vd_states"] is None


def test_design_create_renders_sample_and_stores_params(app_client, fake_render):
    client, cfg = app_client
    r = client.post(
        "/profiles",
        data={
            "name": "Designed",
            "kind": "design",
            "vd_states": json.dumps(_VD),
            "instruct": "female, young adult, high pitch",
            "language": "English",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "design"
    profile = client.get(f"/profiles/{body['id']}").json()
    assert profile["kind"] == "design"
    # #983: the server now completes vd_states to all 6 known categories
    # (missing ones default to 'Auto') before persisting — _VD only sets 3,
    # so the stored value is a superset of it, not an exact match.
    stored = json.loads(profile["vd_states"])
    assert stored == {**_VD, "Style": "Auto", "EnglishAccent": "Auto", "ChineseDialect": "Auto"}
    assert profile["seed"] == 42  # deterministic identity sample
    wav = os.path.join(cfg.VOICES_DIR, profile["ref_audio_path"])
    assert os.path.exists(wav) and os.path.getsize(wav) > 0


def test_design_normalizes_partial_vd_states_to_all_categories(app_client, fake_render):
    """#983: a design profile must never persist with a partial vd_states shape.

    A client (older frontend build, hand-edited payload, third-party API
    caller) that only sends a subset of the 6 known category keys used to be
    saved as-is — selecting that profile later handed the frontend an
    incomplete vdStates object, crashing DesignMethodPanel's render
    ("Cannot read properties of undefined (reading 'replace')"). The server
    now fills every missing category with 'Auto' before persisting, so the
    stored vd_states is always complete regardless of which client wrote it.
    """
    client, _ = app_client
    r = client.post(
        "/profiles",
        data={"name": "Partial", "kind": "design", "vd_states": json.dumps({"Gender": "male"})},
    )
    assert r.status_code == 200, r.text
    profile = client.get(f"/profiles/{r.json()['id']}").json()
    stored = json.loads(profile["vd_states"])
    assert set(stored) == {"Gender", "Age", "Pitch", "Style", "EnglishAccent", "ChineseDialect"}
    assert stored["Gender"] == "male"
    for cat in ("Age", "Pitch", "Style", "EnglishAccent", "ChineseDialect"):
        assert stored[cat] == "Auto"


# ── Migration 0005 ───────────────────────────────────────────────────────────

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


def _make_0003_db(db):
    """A DB as it exists after 0003 (no kind/vd_states) with one legacy row."""
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """CREATE TABLE voice_profiles (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, ref_audio_path TEXT,
                ref_text TEXT DEFAULT '', instruct TEXT DEFAULT '',
                language TEXT DEFAULT 'Auto', locked_audio_path TEXT DEFAULT '',
                seed INTEGER DEFAULT NULL, is_locked INTEGER DEFAULT 0,
                personality TEXT DEFAULT '', description TEXT DEFAULT '',
                is_demo INTEGER DEFAULT 0, verified_own_voice INTEGER DEFAULT 0,
                consent_text TEXT DEFAULT '', consent_audio_path TEXT DEFAULT '',
                consent_recorded_at REAL DEFAULT NULL, created_at REAL
            )"""
        )
        conn.execute(
            "INSERT INTO voice_profiles (id, name, ref_audio_path, created_at) "
            "VALUES ('legacy01', 'Old Voice', 'legacy01.wav', 1.0)"
        )
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.execute(
            "INSERT INTO alembic_version VALUES ('0003_voice_profile_consent')"
        )


def test_migration_0005_adds_columns_and_backfills(tmp_path):
    db = tmp_path / "up.db"
    _make_0003_db(db)
    _run_alembic("upgrade", str(db))
    cols = _columns(db, "voice_profiles")
    assert {"kind", "vd_states"} <= cols
    with sqlite3.connect(str(db)) as conn:
        kind = conn.execute(
            "SELECT kind FROM voice_profiles WHERE id='legacy01'"
        ).fetchone()[0]
    assert kind == "clone"


def test_migration_0005_downgrade_drops_columns(tmp_path):
    db = tmp_path / "down.db"
    _make_0003_db(db)
    _run_alembic("upgrade", str(db))
    _run_alembic("downgrade", str(db), target="0003_voice_profile_consent")
    cols = _columns(db, "voice_profiles")
    assert "kind" not in cols and "vd_states" not in cols
