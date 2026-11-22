"""#4355: unloading the TTS model must drop every strong reference to it,
including the one cached OmniVoiceBackend adapters keep on ``_model``."""
import gc
import weakref

import services.model_manager as mm
import services.tts_backend as tb


class _FakeModel:
    pass


def _prime(monkeypatch, where):
    fake = _FakeModel()
    ref = weakref.ref(fake)
    inst = tb.OmniVoiceBackend(model=fake)
    if where == "active":
        monkeypatch.setattr(tb, "_active_instance", inst)
    else:
        monkeypatch.setitem(tb._ENGINE_INSTANCES, tb.OmniVoiceBackend, inst)
    monkeypatch.setattr(mm, "model", fake)
    monkeypatch.setattr(mm, "free_vram", lambda: None)
    return ref, inst


def test_unload_frees_model_held_by_active_adapter(monkeypatch):
    ref, inst = _prime(monkeypatch, "active")
    assert mm.unload_shared_model() is True
    gc.collect()
    assert ref() is None
    assert inst._model is None


def test_unload_frees_model_held_by_engine_cache_adapter(monkeypatch):
    ref, inst = _prime(monkeypatch, "cache")
    assert mm.unload_shared_model() is True
    gc.collect()
    assert ref() is None


def test_unload_keeps_adapter_model_while_a_render_holds_an_inference_slot(monkeypatch):
    ref, inst = _prime(monkeypatch, "active")
    with mm.tts_inference():
        assert mm.unload_shared_model() is True
        assert inst._model is not None  # the in-flight render still reads it
    monkeypatch.setattr(mm, "model", inst._model)
    assert mm.unload_shared_model() is True
    assert inst._model is None
