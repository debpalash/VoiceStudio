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


def test_unload_during_a_render_clears_the_adapter_but_not_the_running_call(monkeypatch):
    """CodeRabbit on #4359: deferring the drop while a render ran left the
    weights resident, because a later unload stops at ``model is None``. The
    render now holds its own reference, so the drop is always safe."""
    ref, inst = _prime(monkeypatch, "active")
    monkeypatch.setattr(mm, "ensure_tts_on_device", lambda: None)
    seen = []

    def fake_generate(model, **_kw):
        assert mm.unload_shared_model() is True  # unload lands mid-render
        assert inst._model is None
        seen.append(model)
        return ["wav"]

    monkeypatch.setattr(tb, "generate_with_cached_ref", fake_generate)
    assert inst.generate("hello") == "wav"
    assert seen and seen[0] is not None  # the running call kept its model
    seen.clear()
    gc.collect()
    assert ref() is None  # freed once the render returned


def test_batch_fallback_after_an_unload_reuses_the_retained_model(monkeypatch):
    """CodeRabbit on #4359: when prompt preparation fails mid-batch after an
    unload cleared the adapter, the single-item fallback must not cold-load a
    replacement while the batch still holds the original weights."""
    ref, inst = _prime(monkeypatch, "active")
    monkeypatch.setattr(mm, "ensure_tts_on_device", lambda: None)
    original = inst._model

    def no_prompt(*_a, **_kw):
        assert mm.unload_shared_model() is True  # unload lands mid-batch
        return None

    monkeypatch.setattr(tb, "_get_clone_prompt", no_prompt)
    monkeypatch.setattr(inst, "_ensure_loaded", lambda: inst._model)
    used = []
    monkeypatch.setattr(
        tb, "generate_with_cached_ref", lambda model, **_kw: used.append(model) or ["wav"]
    )
    out = inst._generate_batch_on_model(["a", "b"], model=original, ref_audio=["r1", "r2"], ref_text=["t1", "t2"])
    assert out == ["wav", "wav"]
    assert used == [original, original]  # never None, never a reload
