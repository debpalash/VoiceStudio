"""Single-active-TTS-engine memory discipline (the 16 GB-Mac OOM class).

Measured before this: a generate on ``omnivoice`` (~2.8 GB core) followed by a
generate on ``mlx-audio`` left BOTH resident (footprint 3.9 → 4.3 GB), because
the OmniVoice core lives in ``model_manager.model`` and the other engines in
``engines._ENGINE_INSTANCES`` — two caches with no coordination, and the latter
was never unloaded. That accumulation is the baseline that OOM-kills a 16 GB Mac.

These tests pin the fix: resolving an engine evicts every OTHER resident engine
first, across both stores, and the default ``unload()`` actually frees the held
model.
"""
from __future__ import annotations

import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from services import engine_memory as em


class _FakeEngine:
    """A backend holding a heavy model in `_model`, using the ABC unload()."""

    def __init__(self, eid):
        self.id = eid
        self._model = object()  # stand-in for multi-GB weights
        self.unloaded = 0

    # Reuse the real ABC default unload via delegation so we test THAT logic.
    def unload(self):
        from services.tts_backend import TTSBackend

        self.unloaded += 1
        TTSBackend.unload(self)


# ── ABC default unload actually frees the model ─────────────────────────────


def _concrete(tb):
    """A minimal concrete TTSBackend subclass (satisfies the ABC) that inherits
    the real default unload() under test."""

    class _Base(tb.TTSBackend):
        id = "fake"
        sample_rate = 24000
        supported_languages = ("en",)

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, *a, **k):  # never called in these tests
            raise NotImplementedError

    return _Base


def test_default_unload_clears_model_attrs_and_frees_vram(monkeypatch):
    from services import tts_backend as tb

    freed = {"n": 0}
    monkeypatch.setattr("services.model_manager.free_vram", lambda: freed.__setitem__("n", freed["n"] + 1))

    class Eng(_concrete(tb)):
        def __init__(self):
            self._model = object()

    e = Eng()
    e.unload()
    assert e._model is None
    assert freed["n"] == 1
    # Idempotent + safe when nothing is loaded: a second call frees nothing more.
    e.unload()
    assert freed["n"] == 1


def test_default_unload_handles_the_tts_attr_and_missing_attrs(monkeypatch):
    from services import tts_backend as tb

    monkeypatch.setattr("services.model_manager.free_vram", lambda: None)

    class Sherpa(_concrete(tb)):
        def __init__(self):
            self._tts = object()  # sherpa holds its model here, not _model

    s = Sherpa()
    s.unload()
    assert s._tts is None

    class External(_concrete(tb)):
        def __init__(self):
            pass  # no model attrs at all (e.g. an HTTP-server engine)

    External().unload()  # must not raise


# ── evict_other_tts_engines ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_core_model(monkeypatch):
    """Pin the OmniVoice core singleton to None so these tests are order-
    independent: evict_other_tts_engines() also frees model_manager.model when
    switching away from omnivoice, and an earlier full-suite test can leave a
    model loaded there. Tests that exercise the core eviction set it explicitly."""
    import services.model_manager as mm

    monkeypatch.setattr(mm, "model", None, raising=False)


@pytest.fixture
def instance_cache(monkeypatch):
    """A stand-in for engines._ENGINE_INSTANCES keyed by class."""
    import api.routers.engines as eng

    cache: dict = {}
    monkeypatch.setattr(eng, "_ENGINE_INSTANCES", cache, raising=False)
    return cache


async def _evict(keep):
    return await em.evict_other_tts_engines(keep)


@pytest.mark.asyncio
async def test_eviction_preserves_leased_engine_then_releases_it(instance_cache, monkeypatch):
    from services import tts_backend

    class Running:
        id = 'running-test'
        unloaded = False

        def unload(self):
            self.unloaded = True

    running = Running()
    instance_cache[Running] = running
    with tts_backend.engine_in_use(running):
        assert await _evict('kittentts') == []
        assert not running.unloaded
    assert await _evict('kittentts') == ['running-test']
    assert running.unloaded


@pytest.mark.asyncio
async def test_unknown_override_cannot_evict_core_or_cached_models(instance_cache, monkeypatch):
    import services.model_manager as mm

    sentinel = object()
    monkeypatch.setattr(mm, 'model', sentinel)
    assert await _evict('not-a-real-engine') == []
    assert mm.model is sentinel


@pytest.mark.asyncio
async def test_evicts_other_engine_instances_but_keeps_the_active_one(instance_cache, monkeypatch):
    class KittenTTSBackend:
        id = "kittentts"

    class MLXAudioBackend:
        id = "mlx-audio"

    keep = MLXAudioBackend()
    drop = KittenTTSBackend()
    drop.unloaded = 0
    drop.unload = lambda: setattr(drop, "unloaded", drop.unloaded + 1)
    instance_cache[MLXAudioBackend] = keep
    instance_cache[KittenTTSBackend] = drop

    monkeypatch.setattr(em, "get_backend_class", None, raising=False)
    monkeypatch.setattr(
        "services.tts_backend.get_backend_class",
        lambda i: MLXAudioBackend if i == "mlx-audio" else KittenTTSBackend,
    )

    evicted = await _evict("mlx-audio")

    assert evicted == ["kittentts"]
    assert drop.unloaded == 1
    assert KittenTTSBackend not in instance_cache  # dropped
    assert instance_cache[MLXAudioBackend] is keep  # kept


@pytest.mark.asyncio
async def test_evicts_the_omnivoice_core_when_switching_away_from_it(instance_cache, monkeypatch):
    import services.model_manager as mm

    monkeypatch.setattr(mm, "model", object(), raising=False)
    freed = {"n": 0}
    monkeypatch.setattr(mm, "free_vram", lambda: freed.__setitem__("n", freed["n"] + 1))
    monkeypatch.setattr("services.tts_backend.get_backend_class", lambda i: type("X", (), {"id": i}))

    evicted = await _evict("mlx-audio")

    assert "omnivoice" in evicted
    assert mm.model is None
    assert freed["n"] == 1


@pytest.mark.asyncio
async def test_keeps_the_omnivoice_core_when_it_IS_the_active_engine(instance_cache, monkeypatch):
    import services.model_manager as mm

    sentinel = object()
    monkeypatch.setattr(mm, "model", sentinel, raising=False)
    monkeypatch.setattr(mm, "free_vram", lambda: None)
    monkeypatch.setattr("services.tts_backend.get_backend_class", lambda i: type("X", (), {"id": i}))

    evicted = await _evict("omnivoice")

    assert "omnivoice" not in evicted
    assert mm.model is sentinel  # the active engine's model is NOT evicted


@pytest.mark.asyncio
async def test_policy_can_be_disabled(instance_cache, monkeypatch):
    import services.model_manager as mm

    monkeypatch.setenv("OMNIVOICE_SINGLE_ENGINE_RESIDENT", "0")
    monkeypatch.setattr(mm, "model", object(), raising=False)

    class Other:
        id = "kittentts"

    other = Other()
    other.unload = lambda: pytest.fail("must not unload when policy is off")
    instance_cache[Other] = other

    assert await _evict("mlx-audio") == []
    assert mm.model is not None  # untouched


@pytest.mark.asyncio
async def test_a_failing_unload_does_not_abort_the_eviction(instance_cache, monkeypatch):
    class A:
        id = "a"

    class B:
        id = "b"

    a, b = A(), B()
    a.unload = lambda: (_ for _ in ()).throw(RuntimeError("stuck"))
    b.unloaded = 0
    b.unload = lambda: setattr(b, "unloaded", b.unloaded + 1)
    instance_cache[A] = a
    instance_cache[B] = b
    monkeypatch.setattr("services.tts_backend.get_backend_class",
                        lambda i: type("keep", (), {"id": i}))

    evicted = await _evict("other")  # keep nothing in the cache

    # Both attempted; the raising one didn't stop the other from being freed.
    assert set(evicted) == {"a", "b"}
    assert b.unloaded == 1
    assert not instance_cache  # both dropped despite the failure


@pytest.mark.parametrize("method", ["generate", "generate_batch"])
def test_render_holds_engine_during_eviction(instance_cache, monkeypatch, method):
    import asyncio
    from services import tts_backend as tb

    class Rendering(_concrete(tb)):
        def generate(self, text, **kwargs):
            return self.render()

        def generate_batch(self, texts, **kwargs):
            return self.render()

        def render(self):
            asyncio.run(em.evict_other_tts_engines("other"))
            assert self._model is sentinel
            return sentinel

    engine = Rendering()
    sentinel = engine._model = object()
    instance_cache[Rendering] = engine
    monkeypatch.setattr(tb, "get_backend_class", lambda _: type("Other", (), {}))
    assert getattr(engine, method)("hello" if method == "generate" else ["hello"]) is sentinel
    assert not tb._ENGINE_IN_USE.get(Rendering)
    asyncio.run(em.evict_other_tts_engines("other"))
    assert engine._model is None
