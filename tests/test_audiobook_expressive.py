"""Audiobook Expressive Maturity (#1208).

Covers the v0.3.23 headline: Production Overrides + IndexTTS2 graded emotion +
the cache opt-out threaded through the shared longform render, plus the
CRITICAL cache-signature guard (every new knob must perturb every cache key, or
changing it silently replays stale audio).

Engine + model boundary stubbed throughout — no model loads, no GPU, no ffmpeg.
"""
import dataclasses
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
import torch

from services.audiobook import Chapter, ExpressiveOptions, Span, segment_seed, synthesize_chapter


_RESOLVE = lambda _vid: {  # noqa: E731
    "ref_audio": None, "ref_text": None, "instruct": None, "seed": None,
}
_ALL_FIELDS = {
    "num_step": 40,
    "guidance_scale": 3.5,
    "position_temperature": 1.0,
    "class_temperature": 0.5,
    "postprocess_output": False,
    "seed": 7,
    "emo_vector": (0.1, 0.2, 0, 0, 0, 0, 0, 0.7),
    "emo_text": "sounds exhausted",
    "emo_alpha": 0.4,
    "vary_repeats": True,
    "line_gap_ms": 250,
    "paragraph_gap_ms": 600,
    "trim_edges": True,
}


# ── ExpressiveOptions.cache_signature: the CRITICAL TRAP guard ───────────────

def test_default_options_have_empty_signature():
    # A default render must be byte-identical to pre-#1208: no signature, so no
    # perturbation of any cache key.
    assert ExpressiveOptions().is_default
    assert ExpressiveOptions().cache_signature() == ""


def test_every_expressive_field_perturbs_the_cache_signature_distinctly():
    """Loop over EVERY knob: each one, set alone, must change the signature —
    and produce a signature distinct from every other knob's. A future field
    added to the dataclass but forgotten in cache_signature() fails here."""
    base = ExpressiveOptions()
    seen = {base.cache_signature()}  # {""}
    for field, value in _ALL_FIELDS.items():
        opts = dataclasses.replace(base, **{field: value})
        sig = opts.cache_signature()
        assert sig, f"{field} did not produce a signature"
        assert sig not in seen, f"{field} collided with another field's signature"
        seen.add(sig)
    # Every declared field was exercised (guards against a field added to the
    # dataclass without a matching entry in _ALL_FIELDS / cache_signature).
    assert {f.name for f in dataclasses.fields(base)} == set(_ALL_FIELDS)


# ── segment_seed nonce (cache opt-out determinism) ──────────────────────────

def test_segment_seed_nonce_zero_is_backward_compatible():
    # nonce defaulting to 0 must reproduce the pre-#1208 value exactly.
    assert segment_seed(1234, "hi") == segment_seed(1234, "hi", 0)


def test_segment_seed_nonce_decorrelates_repeats():
    a = segment_seed(1234, "hi", 0)
    b = segment_seed(1234, "hi", 1)
    c = segment_seed(1234, "hi", 2)
    assert len({a, b, c}) == 3
    assert all(0 <= s < 2**31 for s in (a, b, c))


# ── Chapter cache key absorbs every knob (integration) ──────────────────────

def _render_key(tmp_path, opts):
    """Render a one-span chapter with a stub synth; return the cache WAV path
    (its basename is the content-addressed chapter key)."""
    from api.routers.audiobook import _render_chapter_cached

    ch = Chapter(title="C", spans=[Span(voice_id=None, text="hello", pause_ms_after=0)])
    synth = lambda text, vid, speed=None: torch.zeros(2400)  # noqa: E731
    wav_path, *_ = _render_chapter_cached(
        ch, synth, 24000, "eng", _RESOLVE, str(tmp_path), None, None, opts,
    )
    return os.path.basename(wav_path)


def test_chapter_cache_key_changes_for_every_expressive_field(tmp_path):
    """The end-to-end guard: two renders that differ only in one expressive knob
    must land in different cache slots (else the second silently replays the
    first). Default vs each single-field opts must all be distinct keys."""
    base_key = _render_key(tmp_path, ExpressiveOptions())
    keys = {base_key}
    for field, value in _ALL_FIELDS.items():
        opts = dataclasses.replace(ExpressiveOptions(), **{field: value})
        k = _render_key(tmp_path, opts)
        assert k not in keys, f"{field} did not change the chapter cache key"
        keys.add(k)


def test_default_opts_render_key_matches_no_opts(tmp_path):
    # Passing an explicit default ExpressiveOptions() must be byte-identical to
    # passing None (both == today's key) — backward compat for existing caches.
    from api.routers.audiobook import _render_chapter_cached

    ch = Chapter(title="C", spans=[Span(voice_id=None, text="hello", pause_ms_after=0)])
    synth = lambda text, vid, speed=None: torch.zeros(2400)  # noqa: E731
    a, *_ = _render_chapter_cached(ch, synth, 24000, "eng", _RESOLVE, str(tmp_path), None, None, None)
    b, *_ = _render_chapter_cached(
        ch, synth, 24000, "eng", _RESOLVE, str(tmp_path), None, None, ExpressiveOptions(),
    )
    assert os.path.basename(a) == os.path.basename(b)


# ── preview / render parity ──────────────────────────────────────────────────

def test_preview_and_render_derive_identical_opts_and_keys(tmp_path):
    from api.routers.audiobook import (
        AudiobookPreviewRequest, AudiobookRequest, _expressive_opts,
    )

    fields = dict(num_step=40, guidance_scale=3.0, position_temperature=1.0,
                  emo_text="sad", vary_repeats=True)
    render_opts = _expressive_opts(AudiobookRequest(text="# A\nhello", **fields))
    preview_opts = _expressive_opts(AudiobookPreviewRequest(text="# A\nhello", **fields))
    assert render_opts == preview_opts  # same knobs → same typed options
    # …and therefore the same chapter cache slot (preview warms exactly what the
    # full render reuses).
    assert _render_key(tmp_path, render_opts) == _render_key(tmp_path, preview_opts)


# ── input bounds: a loopback POST can't feed the sampler abuse values ────────

def test_expressive_knobs_reject_out_of_range_values():
    """CodeRabbit #1208: the expressive knobs are a loopback POST body (reachable
    by a browser-tab CSRF), so an absurd num_step could pin a GPU-pool worker.
    Each knob must be bounds-checked at the model boundary."""
    import pytest
    from pydantic import ValidationError

    from api.routers.audiobook import AudiobookRequest

    base = dict(text="# A\nhello")
    for bad in (
        dict(num_step=100_000),      # would tie up a worker
        dict(num_step=0),            # non-positive step count
        dict(guidance_scale=-1.0),
        dict(position_temperature=1e9),
        dict(class_temperature=-0.5),
        dict(emo_alpha=2.0),         # alpha is a 0..1 blend
        dict(emo_vector=[0.1, 0.2]),  # must be the 8-emotion vector
    ):
        with pytest.raises(ValidationError):
            AudiobookRequest(**base, **bad)

    # In-range values still construct fine (defaults/None unaffected).
    AudiobookRequest(**base, num_step=32, guidance_scale=2.0, emo_alpha=0.6,
                     emo_vector=[0.0] * 8)


# ── backward compat: default request → today's exact synth args ─────────────

def _record_manual_seed(monkeypatch):
    seeds = []
    real = torch.manual_seed
    monkeypatch.setattr(torch, "manual_seed", lambda s: (seeds.append(s), real(s))[1])
    return seeds


def test_omnivoice_default_opts_reproduce_todays_synth_args(monkeypatch):
    import asyncio

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

    monkeypatch.setattr(tb, "active_backend_id", lambda: "omnivoice")
    monkeypatch.setattr(mm, "get_model", fake_get_model)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _vid: {
        "ref_audio": None, "ref_text": None, "instruct": None, "seed": None,
    })

    synth, *_ = asyncio.run(ab._prepare_synth("p", opts=ExpressiveOptions()))
    synth("hello", None)

    kw = gen_calls[0]
    assert kw["num_step"] == 32 and kw["guidance_scale"] == 2.0
    # No temperature / postprocess kwargs on the default path — the model keeps
    # its own defaults, exactly as before #1208.
    for k in ("position_temperature", "class_temperature", "postprocess_output",
              "emo_vector", "emo_text"):
        assert k not in kw


def test_omnivoice_overrides_reach_model_but_emotion_never_does(monkeypatch):
    import asyncio

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

    monkeypatch.setattr(tb, "active_backend_id", lambda: "omnivoice")
    monkeypatch.setattr(mm, "get_model", fake_get_model)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _vid: {
        "ref_audio": None, "ref_text": None, "instruct": None, "seed": None,
    })

    opts = ExpressiveOptions(num_step=48, guidance_scale=3.0,
                             position_temperature=2.0, class_temperature=0.6,
                             postprocess_output=False, emo_text="sad",
                             emo_vector=(0.5,) * 8)
    synth, *_ = asyncio.run(ab._prepare_synth("p", opts=opts))
    synth("hello", None)

    kw = gen_calls[0]
    assert kw["num_step"] == 48 and kw["guidance_scale"] == 3.0
    assert kw["position_temperature"] == 2.0 and kw["class_temperature"] == 0.6
    assert kw["postprocess_output"] is False
    # The OmniVoice config rejects unknown kwargs — emotion must NEVER be
    # forwarded to it (it belongs only on the generic/IndexTTS2 path).
    assert "emo_vector" not in kw and "emo_text" not in kw


# ── generic engine: options-ignored contract + emotion reaches the engine ───

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


def test_generic_default_opts_pass_no_extra_kwargs(monkeypatch):
    import api.routers.audiobook as ab

    calls = []
    _patch_generic_engine(monkeypatch, calls)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _vid: _RESOLVE(_vid))

    ab._build_synth(None)["synth"]("hello", None)
    _text, kw = calls[0]
    for k in ("num_step", "guidance_scale", "position_temperature",
              "class_temperature", "postprocess_output", "emo_vector", "emo_text"):
        assert k not in kw  # byte-identical to the pre-#1208 generic call


def test_generic_emotion_and_overrides_reach_a_naive_backend(monkeypatch):
    """A backend whose generate(self, text, **kw) does not understand the new
    options must receive them without raising (the engine-options contract),
    and the IndexTTS2 emotion trio must arrive intact from the longform path."""
    import api.routers.audiobook as ab

    calls = []
    _patch_generic_engine(monkeypatch, calls)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _vid: _RESOLVE(_vid))

    opts = ExpressiveOptions(num_step=40, guidance_scale=3.0,
                             emo_vector=(0.9, 0, 0, 0, 0, 0, 0, 0.1),
                             emo_text="whispering", emo_alpha=0.5)
    # Must not raise even though the fake backend ignores every new kwarg.
    ab._build_synth(None, opts=opts)["synth"]("hello", None)

    _text, kw = calls[0]
    assert kw["num_step"] == 40 and kw["guidance_scale"] == 3.0
    assert kw["emo_vector"] == [0.9, 0, 0, 0, 0, 0, 0, 0.1]
    assert kw["emo_text"] == "whispering" and kw["use_emo_text"] is True
    assert kw["emo_alpha"] == 0.5


# ── cache opt-out (vary_repeats) behaviour ──────────────────────────────────

def _count_synth_calls(tmp_path, opts):
    from api.routers.audiobook import _render_chapter_cached

    ch = Chapter(title="C", spans=[
        Span(voice_id=None, text="same line", pause_ms_after=0),
        Span(voice_id=None, text="same line", pause_ms_after=0),
    ])
    n = {"c": 0}

    def synth(text, vid, speed=None):
        n["c"] += 1
        return torch.zeros(2400)

    _render_chapter_cached(ch, synth, 24000, "eng", _RESOLVE, str(tmp_path), None, None, opts)
    return n["c"]


def test_repeated_line_is_deduped_by_default(tmp_path):
    # Today's behaviour: the segment cache replays one WAV for identical spans,
    # so the repeated line synthesizes exactly once.
    assert _count_synth_calls(tmp_path, ExpressiveOptions()) == 1


def test_vary_repeats_gives_each_repeat_its_own_take(tmp_path):
    # Opt-out on: each occurrence gets a distinct cache slot → both synthesize.
    assert _count_synth_calls(tmp_path, ExpressiveOptions(vary_repeats=True)) == 2


# ── Seamless joins (#2216): edge trim + deliberate gaps ─────────────────────
def test_trim_edge_silence_strips_engine_padding_but_keeps_onset():
    from services.chunked_tts import trim_edge_silence

    sr = 1000
    speech = torch.full((500,), 0.5)
    padded = torch.cat([torch.zeros(70), speech, torch.zeros(300)])  # 70 ms / 300 ms
    out = trim_edge_silence(padded, sr, keep_ms=40)
    # 40 ms kept on each side of the loud region.
    assert out.shape[-1] == 40 + 500 + 40
    assert torch.equal(out[40:540], speech)
    # Stereo: trims on the last axis.
    st = torch.stack([padded, padded * 0.1])
    assert trim_edge_silence(st, sr, keep_ms=40).shape == (2, 580)
    # Silent throughout / empty: unchanged (caller decides what that means).
    assert trim_edge_silence(torch.zeros(100), sr).shape[-1] == 100
    assert trim_edge_silence(torch.zeros(0), sr).shape[-1] == 0
    # Already tight: same tensor back.
    assert trim_edge_silence(speech, sr) is speech


def test_split_paragraphs_on_blank_lines_only():
    from services.chunked_tts import split_paragraphs

    assert split_paragraphs("One.\nStill one.\n\nTwo.\n \n\nThree.") == [
        "One.\nStill one.", "Two.", "Three."]
    assert split_paragraphs("") == []
    assert split_paragraphs("   \n\n  ") == []


def test_synthesize_chapter_defaults_are_gap_free_and_untrimmed():
    """Zero gaps + no trim reproduce today's bytes exactly (cache stability)."""
    sr = 1000
    padded = torch.cat([torch.zeros(70), torch.ones(500), torch.zeros(300)])
    spans = [Span(text="a", voice_id="v"), Span(text="b", voice_id="v")]
    audio, _ = synthesize_chapter(spans, lambda *_: padded.clone(), sr)
    assert audio.shape[-1] == 2 * padded.shape[-1]


def test_synthesize_chapter_line_gap_trims_edges_and_inserts_gap():
    sr = 1000
    padded = torch.cat([torch.zeros(70), torch.ones(500), torch.zeros(300)])
    spans = [Span(text="a", voice_id="v"), Span(text="b", voice_id="v")]
    audio, _ = synthesize_chapter(spans, lambda *_: padded.clone(), sr,
                                  line_gap_ms=250, trim_edges=True)
    trimmed = 40 + 500 + 40
    assert audio.shape[-1] == trimmed + 250 + trimmed


def test_synthesize_chapter_explicit_pause_wins_over_line_gap():
    sr = 1000
    tone = torch.ones(500)
    spans = [Span(text="a", voice_id="v", pause_ms_after=1000),
             Span(text="b", voice_id="v")]
    audio, _ = synthesize_chapter(spans, lambda *_: tone.clone(), sr,
                                  line_gap_ms=250)
    # [pause] on the first line replaces the line gap, it is not added to it.
    assert audio.shape[-1] == 500 + 1000 + 500
    # Trailing silence-only span: no dangling line gap before it either.
    spans = [Span(text="a", voice_id="v"), Span(text="", voice_id="v", pause_ms_after=100)]
    audio, _ = synthesize_chapter(spans, lambda *_: tone.clone(), sr, line_gap_ms=250)
    assert audio.shape[-1] == 500 + 100


def test_synthesize_chapter_paragraph_gap_inside_one_span():
    sr = 1000
    tone = torch.ones(500)
    seen = []

    def synth(text, *_):
        seen.append(text)
        return tone.clone()

    spans = [Span(text="First para.\n\nSecond para.", voice_id="v")]
    audio, _ = synthesize_chapter(spans, synth, sr, paragraph_gap_ms=600)
    assert seen == ["First para.", "Second para."]
    assert audio.shape[-1] == 500 + 600 + 500
    # No paragraph gap asked for → the span stays ONE engine call with its text
    # untouched: the pre-existing bytes, seeds and prosody under the old key.
    seen.clear()
    audio, _ = synthesize_chapter(spans, synth, sr)
    assert seen == ["First para.\n\nSecond para."]
    assert audio.shape[-1] == 500


def test_line_gap_never_lands_inside_a_line_split_by_inline_markup():
    """`He was [emphasis]very[/emphasis] tired.` is three spans but ONE line."""
    from services.longform_parser import _parse_chapter_body as parse_chapter_body

    parsed = parse_chapter_body("He was [slow]very[/slow] tired.\n")
    assert [s.get("join") for s in parsed] == ["continue", "continue", None]
    # A plain line carries no key at all (byte-identical plans and cache keys).
    assert all("join" not in s for s in parse_chapter_body("Plain line."))

    sr = 1000
    tone = torch.ones(500)
    spans = [Span(**s) for s in parsed] + [Span(voice_id=None, text="Next line.")]
    audio, _ = synthesize_chapter(spans, lambda *_: tone.clone(), sr, line_gap_ms=250)
    # Exactly one gap: after the line, none between its three spans.
    assert audio.shape[-1] == 4 * 500 + 250
    # Round-trips through the manifest dict only when set.
    assert spans[0].to_dict()["join"] == "continue"
    assert "join" not in spans[-1].to_dict()

    # A blank line sitting ON the markup boundary is a paragraph break: it gets
    # the paragraph gap (the line gap when no paragraph gap is set), never none.
    parsed = parse_chapter_body("Intro [slow]slowly[/slow]\n\nNew paragraph.")
    assert [s.get("join") for s in parsed] == ["continue", "paragraph", None]
    spans = [Span(**s) for s in parsed]
    audio, _ = synthesize_chapter(spans, lambda *_: tone.clone(), sr,
                                  line_gap_ms=250, paragraph_gap_ms=600)
    assert audio.shape[-1] == 3 * 500 + 600
    audio, _ = synthesize_chapter(spans, lambda *_: tone.clone(), sr, line_gap_ms=250)
    assert audio.shape[-1] == 3 * 500 + 250


def test_join_silence_budget_caps_what_the_join_stage_may_add():
    from services.audiobook import MAX_JOIN_SILENCE_MS, _GapBudget

    b = _GapBudget(total_ms=1000)
    assert b.take(600) == 600
    assert b.take(600) == 400          # shortened, not dropped
    assert b.take(600) == 0            # spent
    assert _GapBudget(1000).take(600, count=4) == 250   # shared evenly
    assert _GapBudget().take(0) == 0 and _GapBudget().take(250, count=0) == 0

    # Thousands of one-word paragraphs at the maximum gap stay bounded.
    sr = 100
    body = "\n\n".join(["a"] * 4000)
    audio, _ = synthesize_chapter([Span(voice_id=None, text=body)],
                                  lambda *_: torch.ones(1), sr, paragraph_gap_ms=5000)
    assert audio.shape[-1] <= 4000 + sr * MAX_JOIN_SILENCE_MS // 1000


def test_render_request_gap_fields_are_bounded_and_reach_options():
    from api.routers.audiobook import LongformRenderRequest, _expressive_opts

    req = LongformRenderRequest()
    opts = _expressive_opts(req)
    assert (opts.line_gap_ms, opts.paragraph_gap_ms, opts.trim_edges) == (250, 350, True)
    assert opts.cache_signature()  # non-default vs the dataclass → cache key moves
    with pytest.raises(ValueError):
        LongformRenderRequest(line_gap_ms=99999)
    with pytest.raises(ValueError):
        LongformRenderRequest(paragraph_gap_ms=5001)
    off = _expressive_opts(LongformRenderRequest(
        line_gap_ms=0, paragraph_gap_ms=0, trim_edges=False))
    assert off.cache_signature() == ""  # explicit "old joins" = today's cache key


def test_chapter_cache_key_moves_with_a_span_join_and_is_legacy_without_one():
    from services.longform_render import chapter_cache_key

    kw = dict(sample_rate=24000, engine_id="e")
    plain4 = chapter_cache_key([("v", "a", 0, None), ("v", "b", 0, None)], **kw)
    plain3 = chapter_cache_key([("v", "a", 0), ("v", "b", 0)], **kw)
    assert plain3 == plain4                      # pre-existing keys untouched
    cont = chapter_cache_key([("v", "a", 0, None, "continue"), ("v", "b", 0, None)], **kw)
    para = chapter_cache_key([("v", "a", 0, None, "paragraph"), ("v", "b", 0, None)], **kw)
    assert len({plain4, cont, para}) == 3        # each join → its own audio → its own key
