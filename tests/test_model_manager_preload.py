from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def model_manager(monkeypatch):
    for mod_name in ("core.config", "services.model_manager"):
        if getattr(sys.modules.get(mod_name), "__file__", None) is None:
            sys.modules.pop(mod_name, None)

    import services.model_manager as mm

    monkeypatch.setattr(mm, "_torch", None)
    monkeypatch.setattr(mm, "_OmniVoice", None)
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setenv("OMNIVOICE_MODEL", "test/checkpoint")
    return mm


def test_tts_asr_preload_is_opt_in(model_manager, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_PRELOAD_TTS_ASR", raising=False)
    assert model_manager.should_preload_tts_asr() is False

    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("OMNIVOICE_PRELOAD_TTS_ASR", value)
        assert model_manager.should_preload_tts_asr() is True

    monkeypatch.setenv("OMNIVOICE_PRELOAD_TTS_ASR", "0")
    assert model_manager.should_preload_tts_asr() is False


def test_load_model_skips_pytorch_whisper_by_default(model_manager, monkeypatch):
    calls = []

    class DummyOmniVoice:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(llm=object())

    monkeypatch.delenv("OMNIVOICE_PRELOAD_TTS_ASR", raising=False)
    monkeypatch.setattr(model_manager, "_lazy_torch", lambda: SimpleNamespace(float16="float16", float32="float32"))
    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: DummyOmniVoice)
    monkeypatch.setattr(model_manager, "get_best_device", lambda: "mps")

    loaded = model_manager._load_model_sync()

    assert loaded.llm is not None
    assert loaded._voicestudio_checkpoint == "test/checkpoint"
    assert loaded._voicestudio_loaded_at.endswith("Z")
    assert calls == [
        (
            ("test/checkpoint",),
            {"device_map": "mps", "dtype": "float16", "load_asr": False},
        )
    ]


def test_load_model_can_preload_pytorch_whisper_when_requested(model_manager, monkeypatch):
    calls = []
    asr_loads = []

    class DummyModel:
        llm = object()

        def load_asr_model(self):
            asr_loads.append(True)

    class DummyOmniVoice:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            calls.append((args, kwargs))
            return DummyModel()

    monkeypatch.setenv("OMNIVOICE_PRELOAD_TTS_ASR", "1")
    monkeypatch.setattr(model_manager, "_lazy_torch", lambda: SimpleNamespace(float16="float16", float32="float32"))
    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: DummyOmniVoice)
    monkeypatch.setattr(model_manager, "get_best_device", lambda: "mps")

    model_manager._load_model_sync()

    assert calls[0][1]["load_asr"] is False
    assert asr_loads == [True]


def test_resolve_checkpoint_honors_test_sentinel(model_manager, monkeypatch):
    """`OMNIVOICE_MODEL=test` (the suite-wide sentinel from tests/conftest.py)
    must pass through verbatim — the #693 bare-token self-heal mapping it to
    the real k2-fsa/OmniVoice checkpoint is what let app-booting tests kick
    off a real multi-GB model download."""
    monkeypatch.setenv("OMNIVOICE_MODEL", "test")
    assert model_manager.resolve_omnivoice_checkpoint() == "test"

    # The #693 self-heal itself must keep working for actual engine-id leaks.
    monkeypatch.setenv("OMNIVOICE_MODEL", "omnivoice")
    assert (
        model_manager.resolve_omnivoice_checkpoint()
        == model_manager._DEFAULT_OMNIVOICE_CHECKPOINT
    )


def test_preload_never_loads_uninstalled_checkpoint(model_manager, monkeypatch):
    """Networked machine, repo exists on the Hub, model NOT installed locally:
    preload must skip. The old `model_info()` probe treated Hub reachability
    as "installed" and silently downloaded the full checkpoint in a
    background thread on every boot with an empty cache."""
    import huggingface_hub

    monkeypatch.setenv("OMNIVOICE_MODEL", "k2-fsa/OmniVoice")
    # Old-code determinism: make the Hub probe "succeed" without network.
    monkeypatch.setattr(
        huggingface_hub, "model_info", lambda *a, **k: object(), raising=False
    )
    monkeypatch.setattr(model_manager, "_checkpoint_in_local_cache", lambda c: False)

    loads = []

    async def _record_load():
        loads.append(True)
        return SimpleNamespace(llm=object())

    monkeypatch.setattr(model_manager, "_load_model_with_timeout", _record_load)
    asyncio.run(model_manager.preload_model())

    assert loads == [], "preload must never load/download an uninstalled checkpoint"
    assert model_manager.model is None


def test_preload_warms_up_locally_installed_checkpoint(model_manager, monkeypatch):
    """The counterpart guard: a locally present checkpoint must still warm up
    (skipping it would make the first /generate eat the full weight load)."""
    monkeypatch.setattr(model_manager, "_checkpoint_in_local_cache", lambda c: True)

    loaded = SimpleNamespace(llm=object())

    async def _fake_load():
        return loaded

    monkeypatch.setattr(model_manager, "_load_model_with_timeout", _fake_load)
    asyncio.run(model_manager.preload_model())

    assert model_manager.model is loaded
