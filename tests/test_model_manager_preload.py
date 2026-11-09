from __future__ import annotations

import asyncio
import importlib
import sys
import types
from unittest.mock import Mock

import pytest


@pytest.fixture
def manager(monkeypatch):
    for name in ("core.config", "services.model_manager"):
        module = sys.modules.get(name)
        if module is not None and not getattr(module, "__file__", None):
            sys.modules.pop(name, None)
    module = importlib.import_module("services.model_manager")
    module._torch = None
    module._OmniVoice = None
    module.model = None
    module._shutting_down.clear()
    monkeypatch.setenv("OMNIVOICE_MODEL", "example/test-checkpoint")
    return module


def test_preload_flag_recognises_each_enabled_spelling(manager, monkeypatch):
    observed = {}
    for spelling in ("on", " YES ", "TRUE", "1", "true"):
        monkeypatch.setenv("OMNIVOICE_PRELOAD_TTS_ASR", spelling)
        observed[spelling] = manager.should_preload_tts_asr()
    assert observed == dict.fromkeys(observed, True)


def test_preload_flag_defaults_off_and_rejects_zero(manager, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_PRELOAD_TTS_ASR", raising=False)
    assert manager.should_preload_tts_asr() is False
    monkeypatch.setenv("OMNIVOICE_PRELOAD_TTS_ASR", "0")
    assert manager.should_preload_tts_asr() is False


def _configure_sync_load(manager, monkeypatch):
    load_asr = Mock(name="load_asr_model")
    loaded = types.SimpleNamespace(llm=object(), load_asr_model=load_asr)
    calls = []

    def construct_model(*args, **kwargs):
        calls.append((args, kwargs))
        return loaded

    fake_torch = types.SimpleNamespace(float16="half", float32="full")
    monkeypatch.setattr(manager, "_lazy_torch", lambda: fake_torch)
    monkeypatch.setattr(
        manager,
        "_lazy_omnivoice",
        lambda: types.SimpleNamespace(from_pretrained=construct_model),
    )
    monkeypatch.setattr(manager, "get_best_device", lambda: "cuda")
    monkeypatch.setattr(manager, "_preflight_truncated_weights", lambda _checkpoint: None)
    return loaded, calls


def test_sync_load_keeps_asr_out_of_from_pretrained_by_default(manager, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_PRELOAD_TTS_ASR", raising=False)
    loaded, calls = _configure_sync_load(manager, monkeypatch)

    result = manager._load_model_sync()

    assert result is loaded
    assert calls == [(('example/test-checkpoint',), {
        "device_map": "cuda", "dtype": "half", "load_asr": False,
    })]
    assert result.llm is not None
    assert result._voicestudio_checkpoint == "example/test-checkpoint"
    assert result._voicestudio_loaded_at.endswith("Z")
    loaded.load_asr_model.assert_not_called()


def test_opt_in_loads_asr_after_tts(manager, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_PRELOAD_TTS_ASR", "yes")
    loaded, calls = _configure_sync_load(manager, monkeypatch)

    manager._load_model_sync()

    assert calls[0][1]["load_asr"] is False
    loaded.load_asr_model.assert_called_once_with()


def test_checkpoint_resolution_preserves_test_sentinel_and_repairs_engine_id(
    manager, monkeypatch
):
    monkeypatch.setenv("OMNIVOICE_MODEL", "test")
    assert manager.resolve_omnivoice_checkpoint() == "test"
    monkeypatch.setenv("OMNIVOICE_MODEL", "omnivoice")
    assert manager.resolve_omnivoice_checkpoint() == manager._DEFAULT_OMNIVOICE_CHECKPOINT


def _configure_preload(manager, monkeypatch, *, cached):
    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: types.SimpleNamespace(family="cpu"),
    )
    monkeypatch.setattr(manager, "_ensure_omnivoice_licence", lambda: None)
    monkeypatch.setattr(manager, "_checkpoint_in_local_cache", lambda _checkpoint: cached)


def test_preload_does_not_load_an_uncached_checkpoint(manager, monkeypatch):
    _configure_preload(manager, monkeypatch, cached=False)
    calls = []

    async def record_load():
        calls.append(True)

    monkeypatch.setattr(manager, "_load_model_with_timeout", record_load)
    asyncio.run(manager.preload_model())
    assert calls == []
    assert manager.model is None


def test_preload_stores_locally_cached_model(manager, monkeypatch):
    _configure_preload(manager, monkeypatch, cached=True)
    loaded = object()

    async def load():
        return loaded

    monkeypatch.setattr(manager, "_load_model_with_timeout", load)
    asyncio.run(manager.preload_model())
    assert manager.model is loaded


@pytest.fixture
def model_manager(manager, monkeypatch):
    """Isolate model state and select native OmniVoice regardless of host hardware."""
    from core import device_caps

    monkeypatch.setattr(
        device_caps, "detect_host_caps", lambda: types.SimpleNamespace(family="cpu")
    )
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "omnivoice")
    return manager


@pytest.mark.parametrize("family", ["cpu", "cuda", "rocm"])
def test_preload_warms_up_locally_installed_checkpoint(model_manager, monkeypatch, family):
    """The counterpart guard: a locally present checkpoint must still warm up
    (skipping it would make the first /generate eat the full weight load)."""
    from core import device_caps

    monkeypatch.setattr(
        device_caps, "detect_host_caps", lambda: types.SimpleNamespace(family=family)
    )
    monkeypatch.setattr(model_manager, "_checkpoint_in_local_cache", lambda c: True)

    loaded = types.SimpleNamespace(llm=object())

    async def _fake_load():
        """Return installed weights without loading a real model."""
        return loaded

    monkeypatch.setattr(model_manager, "_load_model_with_timeout", _fake_load)
    asyncio.run(model_manager.preload_model())

    assert model_manager.model is loaded


@pytest.mark.parametrize("family", ["cpu", "cuda", "rocm", "mps"])
@pytest.mark.parametrize("backend_id", ["voxcpm2", "omnivoice-subprocess", "mlx-audio", "unknown"])
def test_preload_skips_other_selected_engines(model_manager, monkeypatch, family, backend_id):
    """Other engine selections must skip all native OmniVoice preload work."""
    from core import device_caps, prefs

    monkeypatch.setattr(
        device_caps, "detect_host_caps", lambda: types.SimpleNamespace(family=family)
    )
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND")
    prefs.set_("tts_backend", backend_id)

    def unexpected(*args, **kwargs):
        """Fail if the unselected engine reaches licence, cache, or weight work."""
        pytest.fail("an unselected OmniVoice must not check its licence, cache or load weights")

    monkeypatch.setattr(model_manager, "_ensure_omnivoice_licence", unexpected)
    monkeypatch.setattr(model_manager, "_checkpoint_in_local_cache", unexpected)
    monkeypatch.setattr(model_manager, "_load_model_with_timeout", unexpected)

    asyncio.run(model_manager.preload_model())

    assert model_manager.model is None


@pytest.mark.parametrize("saved,override,should_load", [
    ("omnivoice", "voxcpm2", False),
    ("voxcpm2", "omnivoice", True),
])
def test_preload_respects_engine_environment_override(
    model_manager, monkeypatch, saved, override, should_load
):
    """An environment override wins over the saved engine in either direction."""
    from core import prefs

    prefs.set_("tts_backend", saved)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", override)
    monkeypatch.setattr(model_manager, "_checkpoint_in_local_cache", lambda c: True)
    loaded = types.SimpleNamespace(llm=object())

    async def fake_load():
        """Return a recognizable model to detect whether preload ran."""
        return loaded

    monkeypatch.setattr(model_manager, "_load_model_with_timeout", fake_load)
    asyncio.run(model_manager.preload_model())

    assert model_manager.model is (loaded if should_load else None)


def test_preload_keeps_default_omnivoice_warmup(model_manager, monkeypatch):
    """Missing engine preferences and overrides preserve the default warm-up."""
    from core import prefs

    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND")
    prefs.delete("tts_backend")
    monkeypatch.setattr(model_manager, "_checkpoint_in_local_cache", lambda c: True)
    loaded = types.SimpleNamespace(llm=object())

    async def fake_load():
        """Return a recognizable model without downloading or allocating weights."""
        return loaded

    monkeypatch.setattr(model_manager, "_load_model_with_timeout", fake_load)
    asyncio.run(model_manager.preload_model())

    assert model_manager.model is loaded
