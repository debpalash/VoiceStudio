"""TTS engines refuse a gated model until its licence is accepted.

Gated at the shared choke points (``get_engine_instance``,
``get_active_tts_backend``, OmniVoice's ``get_model``), before any weights load
or sidecar starts, and on every call so a revocation also stops a cached
instance. Startup preload skips quietly instead of failing.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import importlib  # noqa: E402

pytestmark = pytest.mark.model_licence_gate  # real enforcement, see conftest

GATED_REPO = "IndexTeam/IndexTTS-2.5"  # indextts2: unknown category, gated


# Resolved per test, not at collection: a suite that purges and re-imports the
# backend packages would otherwise leave this module patching stale twins.
@pytest.fixture
def ma():
    return importlib.import_module("services.model_acceptance")


@pytest.fixture
def tts_backend():
    return importlib.import_module("services.tts_backend")


@pytest.fixture
def store(monkeypatch, ma):
    settings_store = importlib.import_module("services.settings_store")
    data: dict[str, str] = {}
    monkeypatch.setattr(settings_store, "get_text", lambda key, default=None: data.get(key, default))
    monkeypatch.setattr(settings_store, "set_text", lambda key, value: data.__setitem__(key, value))
    monkeypatch.setattr(settings_store, "get_license_accepted", lambda engine: False)
    return data


def _accept(ma, repo_id: str) -> None:
    ma.accept(repo_id, ma.status(repo_id)["fingerprint"])


def _engine(engine_id: str, identity: str | None = None):
    class FakeEngine:
        id = engine_id
        built = 0
        unloaded = 0

        def __init__(self):
            type(self).built += 1

        @classmethod
        def configured_identity(cls):
            return identity

        def built_identity(self):
            return identity

        def unload(self):
            type(self).unloaded += 1

    return FakeEngine


@pytest.fixture
def engine_cache(tts_backend):
    yield
    for cls in [c for c in tts_backend._ENGINE_INSTANCES if c.__name__ == "FakeEngine"]:
        tts_backend._ENGINE_INSTANCES.pop(cls, None)
        tts_backend._ENGINE_LAST_USED.pop(cls, None)


def test_unaccepted_engine_refused_before_construction(store, engine_cache, ma, tts_backend):
    cls = _engine("indextts2")
    with pytest.raises(ma.ModelLicenceNotAccepted) as err:
        tts_backend.get_engine_instance(cls)
    assert cls.built == 0
    assert err.value.detail()["code"] == "model_licence_required"
    assert [m["repo_id"] for m in err.value.detail()["models"]] == [GATED_REPO]


def test_accepted_engine_is_built_once(store, engine_cache, ma, tts_backend):
    cls = _engine("indextts2")
    _accept(ma, GATED_REPO)
    first = tts_backend.get_engine_instance(cls)
    assert tts_backend.get_engine_instance(cls) is first
    assert cls.built == 1


def test_commercial_engine_needs_no_acceptance(store, engine_cache, tts_backend):
    cls = _engine("kittentts")  # Apache-2.0
    assert tts_backend.get_engine_instance(cls) is not None
    assert cls.built == 1


def test_revocation_stops_a_cached_instance(store, engine_cache, ma, tts_backend):
    cls = _engine("indextts2")
    _accept(ma, GATED_REPO)
    tts_backend.get_engine_instance(cls)
    ma.revoke(GATED_REPO)
    with pytest.raises(ma.ModelLicenceNotAccepted):
        tts_backend.get_engine_instance(cls)


def test_engine_id_lookup_goes_through_the_gate(store, engine_cache, monkeypatch, ma, tts_backend):
    cls = _engine("indextts2")
    monkeypatch.setattr(tts_backend, "get_backend_class", lambda engine_id: cls)
    with pytest.raises(ma.ModelLicenceNotAccepted):
        tts_backend.get_engine_instance_for("indextts2")
    assert cls.built == 0


@pytest.mark.parametrize(("identity", "gated"), [
    ("mlx-community/Kokoro-82M-bf16", False),          # Apache-2.0
    ("mlx-community/Llama-OuteTTS-1.0-1B-4bit", True),  # non-commercial
])
def test_preference_dependent_engine_gates_the_selected_model(store, engine_cache, identity, gated, ma, tts_backend):
    cls = _engine("mlx-audio", identity)
    if gated:
        with pytest.raises(ma.ModelLicenceNotAccepted) as err:
            tts_backend.get_engine_instance(cls)
        assert [m["repo_id"] for m in err.value.models] == [identity]
    else:
        tts_backend.get_engine_instance(cls)


@pytest.fixture
def active_slot(monkeypatch, tts_backend):
    saved = (tts_backend._active_instance, tts_backend._active_instance_id,
             tts_backend._active_mlx_model_key)
    yield monkeypatch
    (tts_backend._active_instance, tts_backend._active_instance_id,
     tts_backend._active_mlx_model_key) = saved


def test_active_backend_refusal_keeps_the_working_engine(store, active_slot, ma, tts_backend):
    working = _engine("kittentts")
    current = working()
    tts_backend._active_instance, tts_backend._active_instance_id = current, "kittentts"
    gated = _engine("indextts2")
    active_slot.setattr(tts_backend, "active_backend_id", lambda: "indextts2")
    active_slot.setattr(tts_backend, "get_backend_class", lambda engine_id: gated)

    with pytest.raises(ma.ModelLicenceNotAccepted):
        tts_backend.get_active_tts_backend()
    assert gated.built == 0
    assert working.unloaded == 0
    assert tts_backend._active_instance is current


def test_active_backend_revocation_applies_to_cached_instance(store, active_slot, ma, tts_backend):
    gated = _engine("indextts2")
    active_slot.setattr(tts_backend, "active_backend_id", lambda: "indextts2")
    active_slot.setattr(tts_backend, "get_backend_class", lambda engine_id: gated)
    tts_backend._active_instance = tts_backend._active_instance_id = None
    _accept(ma, GATED_REPO)
    tts_backend.get_active_tts_backend()
    ma.revoke(GATED_REPO)
    with pytest.raises(ma.ModelLicenceNotAccepted):
        tts_backend.get_active_tts_backend()


def test_active_engine_preflight_ignores_unknown_engine(store, monkeypatch, tts_backend):
    monkeypatch.setattr(tts_backend, "active_backend_id", lambda: "no-such-engine")
    tts_backend.ensure_active_engine_licence()  # the caller reports the unknown id


# ── OmniVoice's own weight load (model_manager) ────────────────────────────


@pytest.fixture
def mm(monkeypatch):
    model_manager = importlib.import_module("services.model_manager")

    async def no_load():
        raise AssertionError("weights must not load")

    monkeypatch.setattr(model_manager, "_load_model_with_timeout", no_load)
    monkeypatch.setattr(model_manager, "_load_model_exclusive", lambda *a, **k: no_load())
    monkeypatch.setattr(model_manager, "running_on_gpu_pool", lambda: False)
    return model_manager


def test_get_model_refuses_unaccepted_omnivoice(store, mm, monkeypatch, ma):
    monkeypatch.setattr(mm, "model", None)
    with pytest.raises(ma.ModelLicenceNotAccepted) as err:
        asyncio.run(mm.get_model())
    assert "k2-fsa/OmniVoice" in [m["repo_id"] for m in err.value.models]


def test_get_model_revocation_stops_a_resident_model(store, mm, monkeypatch, ma):
    resident = object()
    monkeypatch.setattr(mm, "model", resident)

    async def healed():
        return None

    monkeypatch.setattr(mm, "_heal_tts_placement", healed)
    monkeypatch.setattr(mm, "make_room_before_generate", lambda: None)
    for repo in ma.repos_for_engine("omnivoice"):
        _accept(ma, repo)
    assert asyncio.run(mm.get_model()) is resident
    ma.revoke("k2-fsa/OmniVoice")
    with pytest.raises(ma.ModelLicenceNotAccepted):
        asyncio.run(mm.get_model())


def test_preload_skips_quietly_when_unaccepted(store, mm, monkeypatch, caplog):
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setattr(mm, "_headless_worker", lambda: False)
    caps = importlib.import_module("core.device_caps")

    monkeypatch.setattr(caps, "detect_host_caps", lambda: type("C", (), {"family": "cpu"})())
    monkeypatch.setattr(mm, "resolve_omnivoice_checkpoint",
                        lambda: pytest.fail("preload must stop before the checkpoint probe"))
    with caplog.at_level(logging.INFO):
        asyncio.run(mm.preload_model())
    assert mm.model is None
    assert "licence is not accepted" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ── Structured failure payloads (jobs / SSE / WebSocket) ───────────────────


def test_failure_payload_carries_code_and_models(ma):
    build_failure = importlib.import_module("core.failure").build_failure

    refusal = ma.ModelLicenceNotAccepted([{"repo_id": GATED_REPO}])
    try:
        try:
            raise refusal
        except ma.ModelLicenceNotAccepted as exc:
            raise RuntimeError("chapter failed") from exc
    except RuntimeError as wrapped:
        fields = build_failure(wrapped, stage="tts", include_diagnostic=False)
    assert fields["code"] == "model_licence_required"
    assert fields["models"] == [{"repo_id": GATED_REPO}]
    assert "code" not in build_failure(ValueError("x"), stage="tts", include_diagnostic=False)


def test_remote_worker_reports_licence_not_install(store, engine_cache, monkeypatch, tts_backend):
    executor = importlib.import_module("worker.executor")

    cls = _engine("indextts2")
    monkeypatch.setattr(tts_backend, "get_backend_class", lambda engine_id: cls)
    with pytest.raises(executor.TaskFailure) as err:
        executor.TaskExecutor._load_backend("indextts2")
    assert err.value.error.code == "MODEL_LICENCE_REQUIRED"
    assert cls.built == 0
