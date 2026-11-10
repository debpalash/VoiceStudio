"""The voice-clone prompt cache is actually WIRED to the paths users take.

tests/test_clone_prompt_cache.py already proves the cache works in isolation —
and it passed, green, for every release in which the default engine never called
the cache at all. That is the bug this file exists to prevent recurring.

Background: #427/#473 added the reference-encode cache and wired it into
``OmniVoiceBackend`` (the *adapter* path). But ``/generate`` for the default
engine forks to the *native* model path (the fork predates the cache, #324), and
that path passed ``ref_audio=<path>`` straight to ``model.generate()``. The codec
encoder therefore re-ran the reference on **every generate call** — once per text
chunk, once per pause-span, and once per audiobook segment.

So these tests assert the thing the isolated cache tests structurally cannot: that
a real render encodes the reference ONCE no matter how many generate calls it
takes. Each one fails before the fix (encode count == generate count) and passes
after (encode count == 1).
"""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from api.routers import generation as gen  # noqa: E402


def _tb():
    """The *live* services.tts_backend.

    Several suites purge ``sys.modules["services.*"]`` for DB isolation
    (test_model_load_timeout, test_model_manager_preload), so a module-level
    ``import ... as tb`` can go stale mid-run. The production code imports this
    module at call time and therefore always sees the live one; binding it at
    import time here would leave us inspecting a *different* module's cache than
    the one the code under test just populated — the test would fail for a reason
    that has nothing to do with the behavior it checks.
    """
    import services.tts_backend as m
    return m


class _StubModel:
    """Counts reference encodes vs. generate calls — the whole point of the test.

    Mirrors the real model's contract: ``voice_clone_prompt`` and
    ``ref_audio``/``ref_text`` are mutually exclusive (omnivoice.py:957), so being
    handed both is an error here, not a warning. That makes a regression that
    "passes the prompt but keeps the ref too" fail loudly instead of silently
    re-encoding.
    """

    sampling_rate = 24000

    def __init__(self):
        self.encodes = 0
        self.generates = 0

    def create_voice_clone_prompt(self, ref_audio, ref_text=None, preprocess_prompt=True):
        self.encodes += 1
        return f"PROMPT::{ref_audio}::{ref_text}::{preprocess_prompt}"

    def generate(self, **kw):
        self.generates += 1
        if kw.get("voice_clone_prompt") is not None and (
            kw.get("ref_audio") is not None or kw.get("ref_text") is not None
        ):
            raise AssertionError(
                "voice_clone_prompt passed together with ref_audio/ref_text — "
                "the model would ignore the reference; they are mutually exclusive"
            )
        # 0.4 s of quiet non-silence, enough for the mastering chain to run on.
        return [torch.full((int(0.4 * self.sampling_rate),), 0.05)]


@pytest.fixture(autouse=True)
def _clear_cache():
    _tb().clear_clone_prompt_cache()
    yield
    _tb().clear_clone_prompt_cache()


@pytest.fixture()
def ref_wav(tmp_path):
    p = tmp_path / "ref.wav"
    p.write_bytes(b"\x00" * 256)
    return str(p)


def _run(model, text, ref, **over):
    kw = dict(
        model=model, text=text, language=None, ref_audio_path=ref, ref_text="hello",
        instruct=None, duration=None, num_step=4, guidance_scale=2.0, speed=1.0,
        t_shift=None, denoise=False, postprocess_output=False,
        layer_penalty_factor=None, position_temperature=None,
        class_temperature=None, used_seed=1234, effect_preset="raw",
    )
    kw.update(over)
    return gen._run_inference(**kw)


def test_single_chunk_encodes_reference_once(ref_wav):
    m = _StubModel()
    _run(m, "A short line.", ref_wav)
    assert (m.generates, m.encodes) == (1, 1)


def test_multi_chunk_render_encodes_reference_only_once(ref_wav):
    """The core regression: N chunks used to mean N reference encodes."""
    m = _StubModel()
    # max_chunk_chars=20 forces the splitter to produce several chunks.
    long_text = " ".join(f"Sentence number {i} here." for i in range(8))
    _run(m, long_text, ref_wav, max_chunk_chars=20)
    assert m.generates > 1, "test is vacuous unless the text actually chunked"
    assert m.encodes == 1, (
        f"reference re-encoded {m.encodes}x for {m.generates} chunks — "
        "the native path is bypassing the prompt cache again"
    )


def test_pause_marker_spans_encode_reference_only_once(ref_wav):
    """The [pause] stitcher is a third generate call site — same rule."""
    m = _StubModel()
    _run(m, "First part. [pause 300ms] Second part.", ref_wav)
    assert m.generates > 1, "test is vacuous unless the pause split into spans"
    assert m.encodes == 1


def test_repeat_requests_reuse_the_cached_reference(ref_wav):
    """Across requests too — the cache outlives a single render."""
    m = _StubModel()
    _run(m, "One.", ref_wav)
    _run(m, "Two.", ref_wav)
    assert (m.generates, m.encodes) == (2, 1)


def test_no_reference_still_generates(ref_wav):
    """Voice-design / instruct path has no reference — must not try to encode one."""
    m = _StubModel()
    _run(m, "Designed voice.", None, ref_text=None, instruct="calm narrator")
    assert (m.generates, m.encodes) == (1, 0)


def test_unloading_the_model_drops_its_cached_prompts(ref_wav):
    """An unload must mean unload (#1119).

    Cached prompts hold tensors belonging to the model instance. Only the adapter's
    unload() used to clear them — enough while the cache was adapter-only, but the
    native path now fills it and unloads through model_manager instead. A surviving
    prompt would sit in exactly the memory that offload_tts_for_asr() unloads the
    model to reclaim for ASR.
    """
    from services import model_manager as mm

    m = _StubModel()
    _run(m, "Warm the cache.", ref_wav)
    assert len(_tb()._prompt_cache) == 1

    mm.release_tts_side_caches()
    assert len(_tb()._prompt_cache) == 0, "model unloaded but its encoded prompts survived"


def test_encode_failure_falls_back_to_inline_reference(ref_wav, monkeypatch):
    """A cache failure must degrade to the old inline path, never break synthesis."""
    m = _StubModel()

    def _boom(*a, **k):
        raise RuntimeError("encode exploded")

    monkeypatch.setattr(m, "create_voice_clone_prompt", _boom)
    out = _run(m, "Still works.", ref_wav)
    assert out is not None
    assert m.generates == 1  # fell back to ref_audio=... and still produced audio


def test_model_rejecting_the_prompt_falls_back_to_inline_reference(ref_wav):
    """The prompt encodes fine, but the model refuses it.

    The cache is an optimization, so this must degrade to the inline reference and
    still produce audio — never turn a generation that would have succeeded into an
    error. (The adapter always did this; the shared helper has to as well, or moving
    the native path onto the cache would have made it *less* robust than before.)
    """
    class _RejectsPrompts(_StubModel):
        def __init__(self):
            super().__init__()
            self.attempts = []

        def generate(self, **kw):
            self.attempts.append(
                "prompt" if kw.get("voice_clone_prompt") is not None else "inline"
            )
            if kw.get("voice_clone_prompt") is not None:
                raise RuntimeError("this model does not accept precomputed prompts")
            return super().generate(**kw)

    m = _RejectsPrompts()
    out = _run(m, "Still works.", ref_wav)
    assert out is not None
    # It tried the cached prompt, was refused, and retried inline — which worked.
    assert m.attempts == ["prompt", "inline"]
    assert m.generates == 1  # exactly one call actually produced audio


def test_audiobook_native_synth_encodes_reference_once_per_voice(ref_wav):
    """The audiobook renderer is the worst case: hundreds of segments, one voice.

    It has its own native model.generate() call site, so it needs its own guard —
    the /generate tests above would not catch a regression here.
    """
    from services.tts_backend import generate_with_cached_ref

    m = _StubModel()
    # Mirrors audiobook.py's synth(): one generate per segment, same voice.
    for i in range(12):
        generate_with_cached_ref(
            m, ref_audio=ref_wav, ref_text="hello",
            text=f"Segment {i}.", language=None, instruct=None,
            duration=None, speed=1.0,
        )
    assert m.generates == 12
    assert m.encodes == 1, (
        f"reference re-encoded {m.encodes}x across 12 audiobook segments — "
        "a book would pay this hundreds of times"
    )


def test_cache_ref_false_is_popped_and_skips_the_insert(ref_wav):
    """The dub loop marks per-segment refs cache_ref=False. Two contracts:
    the flag must never reach model.generate (explicit signature → TypeError
    on the real model), and the encoded prompt must not enter the LRU (a dub's
    flood of one-shot clips would evict the per-speaker prompts that ARE
    reused — the scan-resistance this exists for)."""
    from services.tts_backend import generate_with_cached_ref

    class _RejectsUnknownKwargs(_StubModel):
        def generate(self, **kw):
            assert "cache_ref" not in kw, "cache_ref leaked through to the model"
            return super().generate(**kw)

    m = _RejectsUnknownKwargs()
    generate_with_cached_ref(
        m, ref_audio=ref_wav, ref_text="hello",
        text="One-shot segment.", language=None, instruct=None,
        duration=None, speed=1.0, cache_ref=False,
    )
    assert m.encodes == 1
    assert len(_tb()._prompt_cache) == 0, "single-use ref was inserted into the LRU"

    # Default (cache_ref absent) still caches — the /generate & audiobook paths.
    generate_with_cached_ref(
        m, ref_audio=ref_wav, ref_text="hello",
        text="Reused voice.", language=None, instruct=None,
        duration=None, speed=1.0,
    )
    assert len(_tb()._prompt_cache) == 1


def test_in_process_omnivoice_forwards_seed_and_isolates_random_draws(monkeypatch):
    from omnivoice.models.omnivoice import _gumbel_sample

    backend = _tb().OmniVoiceBackend(model=_StubModel())
    monkeypatch.setattr(backend, "_ensure_loaded", lambda: None)

    def generate(_model, *, seed, **kwargs):
        generator = torch.Generator().manual_seed(seed)
        return [_gumbel_sample(torch.zeros(128), 1.0, generator)]

    monkeypatch.setattr(_tb(), "generate_with_cached_ref", generate)
    state = torch.random.get_rng_state()
    first = backend.generate("Lana.", seed=42)
    second = backend.generate("Lana.", seed=42)
    different = backend.generate("Lana.", seed=7)
    assert torch.equal(state, torch.random.get_rng_state())
    assert torch.equal(first, second)
    assert not torch.equal(first, different)
