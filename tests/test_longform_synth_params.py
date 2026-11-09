"""Longform synth generation params (#1139).

Two divergences between the Voice page (/generate) and the audiobook/longform
path made "the same profile + text + settings" behave differently:

* A profile's pinned ``seed`` was fetched by ``_resolve_voice`` but only ever
  used in the cache signature — generation itself ran unseeded, so a locked
  take's pinned seed silently did nothing in a book render.
* The omnivoice synth wrapper passed no ``num_step``/``guidance_scale``,
  silently inheriting the model-config defaults. That is the intended quality
  preset for longform, but it must be explicit (LONGFORM_NUM_STEP) so it can't
  drift with upstream config changes.

Engine layer stubbed throughout — no model loads, no GPU.
"""
import asyncio
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
import torch

from services.audiobook import segment_seed


# ── segment_seed: pure helper ────────────────────────────────────────────────

def test_segment_seed_deterministic_and_in_torch_range():
    a = segment_seed(1234, "xin chào")
    assert a == segment_seed(1234, "xin chào")  # stable across calls/runs
    assert 0 <= a < 2**31                        # valid torch.manual_seed input


def test_segment_seed_decorrelates_chunks_but_tracks_base_seed():
    # Different chunk text → different seed (mirrors /generate's used_seed + i);
    # different pinned seed → different seed for the same text.
    assert segment_seed(1234, "chunk one") != segment_seed(1234, "chunk two")
    assert segment_seed(1234, "chunk one") != segment_seed(99, "chunk one")


# ── generic-engine branch: pinned profile seed reaches torch ─────────────────

def _fake_backend_cls(calls):
    from services.tts_backend import TTSBackend

    class _Fake(TTSBackend):
        id = "fake-longform-engine"
        display_name = "Fake Longform Engine (test)"
        gpu_compat = ("cpu",)

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            calls.append((text, kw))
            return torch.zeros(1, 2400)

    return _Fake


def _patch_generic_engine(monkeypatch, calls):
    import services.tts_backend as tb
    fake = _fake_backend_cls(calls)
    monkeypatch.setattr(tb, "active_backend_id", lambda: "fake-longform-engine")
    monkeypatch.setattr(tb, "get_backend_class", lambda _id: fake)


def _record_manual_seed(monkeypatch):
    seeds = []
    real = torch.manual_seed
    monkeypatch.setattr(torch, "manual_seed", lambda s: (seeds.append(s), real(s))[1])
    return seeds


def test_generic_synth_applies_pinned_profile_seed(monkeypatch):
    import api.routers.audiobook as ab

    calls, seeds = [], _record_manual_seed(monkeypatch)
    _patch_generic_engine(monkeypatch, calls)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _vid: {
        "ref_audio": None, "ref_text": None, "instruct": None, "seed": 1234,
    })

    info = ab._build_synth("prof-1")
    info["synth"]("hello world", None)

    assert calls, "stub engine was not reached"
    assert seeds == [segment_seed(1234, "hello world")]  # fails before the fix


def test_generic_synth_without_pinned_seed_stays_unseeded(monkeypatch):
    import api.routers.audiobook as ab

    calls, seeds = [], _record_manual_seed(monkeypatch)
    _patch_generic_engine(monkeypatch, calls)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _vid: {
        "ref_audio": None, "ref_text": None, "instruct": None, "seed": None,
    })

    info = ab._build_synth(None)
    info["synth"]("hello world", None)

    assert calls
    assert seeds == []  # fresh-render variety unchanged when nothing is pinned


def test_mps_proxy_keeps_longform_quality_and_child_seed(monkeypatch):
    import api.routers.audiobook as ab
    import services.tts_backend as tb

    calls = []
    fake = _fake_backend_cls(calls)
    fake.id = "omnivoice"
    fake.supports_native_omnivoice_controls = True
    monkeypatch.setattr(tb, "active_backend_id", lambda: "omnivoice")
    monkeypatch.setattr(tb, "get_backend_class", lambda _id: fake)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _vid: {
        "ref_audio": None, "ref_text": None, "instruct": None, "seed": 42,
    })

    ab._build_synth("prof-1")["synth"]("một đoạn văn", None)

    _text, kwargs = calls[0]
    assert kwargs["num_step"] == ab.LONGFORM_NUM_STEP == 32
    assert kwargs["guidance_scale"] == ab.LONGFORM_GUIDANCE_SCALE == 2.0
    assert kwargs["seed"] == segment_seed(42, "một đoạn văn")


# ── omnivoice branch: explicit quality preset + pinned seed ──────────────────

@pytest.mark.usefixtures("cpu_host")
def test_omnivoice_synth_pins_quality_preset_and_seed(monkeypatch):
    import api.routers.audiobook as ab
    import services.model_manager as mm
    import services.tts_backend as tb

    gen_calls = []

    class _FakeModel:
        sampling_rate = 24000

        def generate(self, **kw):
            gen_calls.append(kw)
            return [torch.zeros(1, 2400)]

    async def fake_get_model():
        return _FakeModel()

    seeds = _record_manual_seed(monkeypatch)
    monkeypatch.setattr(tb, "active_backend_id", lambda: "omnivoice")
    monkeypatch.setattr(mm, "get_model", fake_get_model)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _vid: {
        "ref_audio": None, "ref_text": None, "instruct": None, "seed": 42,
    })

    synth, sr, _resolve, engine_id = asyncio.run(ab._prepare_synth("prof-1"))
    synth("một đoạn văn", None)

    assert sr == 24000 and engine_id == "omnivoice"
    assert len(gen_calls) == 1
    # The quality preset is explicit, not an accident of model defaults.
    assert gen_calls[0]["num_step"] == ab.LONGFORM_NUM_STEP == 32
    assert gen_calls[0]["guidance_scale"] == ab.LONGFORM_GUIDANCE_SCALE == 2.0
    # The pinned profile seed reached torch, decorrelated per chunk text.
    assert seeds == [segment_seed(42, "một đoạn văn")]
