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

@pytest.fixture(autouse=True)
def _runtime_audiobook_symbols():
    from services import audiobook
    for name in ("Chapter", "ExpressiveOptions", "Span", "segment_seed", "synthesize_chapter"):
        globals()[name] = getattr(audiobook, name)



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
    "paragraph_gap_ms": 350,
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


@pytest.mark.usefixtures("cpu_host")
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


@pytest.mark.usefixtures("cpu_host")
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


# ── seamless joins ──────────────────────────────────────────────────────────
# Sample rate 1000 Hz throughout, so one sample is one millisecond.

def _burst(before, loud, after, level=0.5):
    """Silence, a constant-level burst, silence."""
    return torch.cat([torch.zeros(before), torch.full((loud,), level), torch.zeros(after)])


class TestEdgeTrim:
    def test_cuts_to_the_burst_plus_margin(self):
        from services.chunked_tts import trim_edge_silence

        out = trim_edge_silence(_burst(700, 250, 900), 1000)
        assert out.numel() == 250 + 2 * 40
        assert out[40:290].eq(0.5).all()

    @pytest.mark.parametrize("keep_ms, expected", [(0, 60), (10, 80), (25, 110)])
    def test_margin_is_configurable(self, keep_ms, expected):
        from services.chunked_tts import trim_edge_silence

        out = trim_edge_silence(_burst(300, 60, 300), 1000, keep_ms=keep_ms)
        assert out.numel() == expected

    def test_multichannel_uses_the_loudest_channel_and_clamps(self):
        from services.chunked_tts import trim_edge_silence

        wave = torch.zeros(2, 500)
        wave[0, 200:210] = 0.3
        wave[1, 205:230] = 0.3
        out = trim_edge_silence(wave, 1000)
        assert out.shape == (2, 30 + 40 + 40)
        near_edge = torch.zeros(1, 400)
        near_edge[0, 5:395] = 0.9
        assert trim_edge_silence(near_edge, 1000) is near_edge

    @pytest.mark.parametrize("maker", [lambda: torch.zeros(300), lambda: torch.zeros(0),
                                       lambda: torch.full((300,), 0.0005)])
    def test_nothing_to_cut_returns_same_object(self, maker):
        from services.chunked_tts import trim_edge_silence

        wave = maker()
        assert trim_edge_silence(wave, 1000) is wave

    def test_none_passes_through(self):
        from services.chunked_tts import trim_edge_silence

        assert trim_edge_silence(None, 1000) is None


@pytest.mark.parametrize("text, expected", [
    ("alpha\nbeta", ["alpha\nbeta"]),
    ("alpha\n\nbeta", ["alpha", "beta"]),
    ("  alpha  \n \t\n\n\n  beta\n", ["alpha", "beta"]),
    ("", []), (None, []), (" \n\n \n", []),
    ("alpha\r\n\r\nbeta", ["alpha", "beta"]),
    ("alpha\r\n \r\n\r\nbeta", ["alpha", "beta"]),
    ("alpha\r\nbeta", ["alpha\r\nbeta"]),
])
def test_split_paragraphs_cases(text, expected):
    from services.chunked_tts import split_paragraphs

    assert split_paragraphs(text) == expected


def test_join_rendered_chunks_trim_flag():
    from services.chunked_tts import join_rendered_chunks

    pieces = [_burst(100, 50, 100), _burst(100, 50, 100)]
    assert join_rendered_chunks(pieces, 1000, crossfade_ms=0).numel() == 500
    assert join_rendered_chunks(pieces, 1000, crossfade_ms=0, trim_edges=True).numel() == 2 * 130


class _Studio:
    """Renders a chapter with a stub engine that returns ``length`` samples of ones."""

    def __init__(self, length=10, sample_rate=1000):
        self.length, self.sample_rate, self.calls = length, sample_rate, []

    def engine(self, text, voice, speed=None):
        self.calls.append(text)
        return torch.ones(self.length)

    def render(self, spans, **kw):
        kw.setdefault("crossfade_ms", 0)
        return synthesize_chapter(spans, self.engine, self.sample_rate, **kw)[0]


def _spoken(*texts, **per_span):
    return [Span(voice_id=None, text=t, **per_span) for t in texts]


def test_defaults_match_explicit_zero_settings():
    studio = _Studio()
    spans = _spoken("one", "two", "three")
    assert torch.equal(studio.render(spans),
                       studio.render(spans, line_gap_ms=0, paragraph_gap_ms=0, trim_edges=False))
    assert studio.render(spans).numel() == 30


def test_line_gap_sits_between_trimmed_lines_only():
    padded = _burst(200, 30, 200).unsqueeze(0)
    audio = synthesize_chapter(_spoken("a", "b", "c"), lambda *_: padded, 1000,
                               crossfade_ms=0, line_gap_ms=120, trim_edges=True)[0]
    assert audio.shape == (1, 3 * 110 + 2 * 120)
    first_gap = audio[0, 110:230]
    assert not first_gap.any()
    assert audio[0, 230 + 40].item() == 0.5


def test_pause_wins_over_gap_and_silence_only_spans_get_none():
    studio = _Studio()
    spans = [Span(voice_id=None, text="a", pause_ms_after=25),
             Span(voice_id=None, text="b"),
             Span(voice_id=None, text="", pause_ms_after=15)]
    assert studio.render(spans, line_gap_ms=500).numel() == 10 + 25 + 10 + 15
    only_pause_next = _spoken("a") + [Span(voice_id=None, text="", pause_ms_after=7)]
    assert studio.render(only_pause_next, line_gap_ms=500).numel() == 10 + 7


def test_paragraph_gap_controls_engine_calls():
    studio = _Studio()
    script = _spoken("first\n\nsecond\n\nthird")
    merged = studio.render(script, paragraph_gap_ms=40)
    assert studio.calls == ["first", "second", "third"]
    assert merged.numel() == 30 + 2 * 40
    studio.calls.clear()
    studio.render(script)
    assert studio.calls == ["first\n\nsecond\n\nthird"]


def test_markup_pieces_of_one_line_are_not_separated():
    studio = _Studio()
    spans = [Span(voice_id=None, text="x", join="continue"),
             Span(voice_id=None, text="y", join="continue"),
             Span(voice_id=None, text="z"),
             Span(voice_id=None, text="next line")]
    assert studio.render(spans, line_gap_ms=90).numel() == 40 + 90


@pytest.mark.parametrize("line, para, expected_gap", [(100, 300, 300), (100, 0, 100), (0, 250, 250)])
def test_blank_line_boundary_prefers_paragraph_gap(line, para, expected_gap):
    studio = _Studio()
    spans = [Span(voice_id=None, text="p", join="paragraph"), Span(voice_id=None, text="q")]
    assert studio.render(spans, line_gap_ms=line, paragraph_gap_ms=para).numel() == 20 + expected_gap


def test_join_marker_serialisation():
    bare = Span(voice_id="v", text="t").to_dict()
    assert "join" not in bare
    marked = Span(voice_id="v", text="t", join="continue").to_dict()
    assert marked["join"] == "continue"
    assert list(marked).index("join") == list(marked).index("speed") + 1


def test_join_settings_in_options():
    opts = ExpressiveOptions(line_gap_ms=80, paragraph_gap_ms=160, trim_edges=True)
    assert tuple(opts.join_kwargs()) == ExpressiveOptions.JOIN_KEYS
    assert ExpressiveOptions.from_manifest(opts.to_manifest()) == opts
    assert ExpressiveOptions.from_manifest({"trim_edges": 1, "other": 9}).trim_edges is True
    assert ExpressiveOptions(seed=3).cache_signature() == \
        ExpressiveOptions(seed=3, line_gap_ms=0, paragraph_gap_ms=0, trim_edges=False).cache_signature()
    assert ExpressiveOptions(seed=3).cache_signature() != \
        ExpressiveOptions(seed=3, trim_edges=True).cache_signature()


def test_join_silence_budget_rejects_before_synthesis_or_cache(monkeypatch):
    import importlib
    audiobook = importlib.import_module("services.audiobook")
    synthesize_chapter = audiobook.synthesize_chapter
    Span = audiobook.Span
    from unittest.mock import Mock

    monkeypatch.setattr(audiobook, "MAX_JOIN_SILENCE_MS", 1000)
    synth = Mock(return_value=torch.ones(10))
    cache = Mock()
    cache.load.return_value = torch.ones(70)
    # Cached paragraph gaps must not bypass the budget. Shortening gaps during
    # synthesis made cached audio depend on the order/cache state of other spans.
    with pytest.raises(ValueError, match="join silence"):
        synthesize_chapter(
            [Span(voice_id=None, text="a\n\nb"), Span(voice_id=None, text="c")],
            synth, 100, paragraph_gap_ms=600, line_gap_ms=600, segment_cache=cache,
        )
    synth.assert_not_called()
    cache.load.assert_not_called()


def test_join_silence_budget_keeps_exact_gaps_and_pause_precedence(monkeypatch):
    import importlib
    audiobook = importlib.import_module("services.audiobook")
    synthesize_chapter = audiobook.synthesize_chapter
    Span = audiobook.Span

    monkeypatch.setattr(audiobook, "MAX_JOIN_SILENCE_MS", 1000)
    spans = [Span(voice_id=None, text="a\n\nb", pause_ms_after=200),
             Span(voice_id=None, text="c")]
    audio, _ = synthesize_chapter(spans, lambda *_: torch.ones(10), 100,
                                  paragraph_gap_ms=600, line_gap_ms=600)
    # The explicit pause replaces the line gap; no trailing line gap is added.
    assert audio.shape[-1] == 30 + 60 + 20


@pytest.mark.parametrize("field", ["line_gap_ms", "paragraph_gap_ms"])
@pytest.mark.parametrize("bad", [-5, 5001, 10 ** 6])
def test_request_gap_out_of_range_is_rejected(field, bad):
    from api.routers.audiobook import ExpressiveMixin

    with pytest.raises(ValueError):
        ExpressiveMixin(**{field: bad})


def test_request_join_fields_lower_into_options():
    from api.routers.audiobook import ExpressiveMixin, _expressive_opts

    top = _expressive_opts(ExpressiveMixin(line_gap_ms=5000, paragraph_gap_ms=0, trim_edges=True))
    assert (top.line_gap_ms, top.paragraph_gap_ms, top.trim_edges) == (5000, 0, True)
    assert top.cache_signature()
    assert _expressive_opts(ExpressiveMixin()).cache_signature() == ""


def test_chapter_cache_key_moves_with_a_span_join_and_is_legacy_without_one():
    from services.longform_render import chapter_cache_key

    kw = dict(sample_rate=24000, engine_id="e")
    plain4 = chapter_cache_key([("v", "a", 0, None), ("v", "b", 0, None)], **kw)
    plain3 = chapter_cache_key([("v", "a", 0), ("v", "b", 0)], **kw)
    assert plain3 == plain4                      # pre-existing keys untouched
    cont = chapter_cache_key([("v", "a", 0, None, "continue"), ("v", "b", 0, None)], **kw)
    para = chapter_cache_key([("v", "a", 0, None, "paragraph"), ("v", "b", 0, None)], **kw)
    assert len({plain4, cont, para}) == 3        # each join → its own audio → its own key


def test_disabled_joins_preserve_nondefault_legacy_cache_signatures():
    # Persisted before join controls existed: a seeded/emotive render must still
    # find its cached segments when the caller explicitly keeps legacy joins.
    expected = ('{"class_temperature": null, "emo_alpha": null, "emo_text": "calm", '
                '"emo_vector": null, "guidance_scale": null, "num_step": null, '
                '"position_temperature": null, "postprocess_output": null, '
                '"seed": 0, "vary_repeats": false}')
    assert ExpressiveOptions(seed=0, emo_text="calm").cache_signature() == expected


@pytest.mark.parametrize('trim, expected', [(True, 1160), (False, 1800)])
def test_multichunk_join_keeps_trimmed_audio_and_original_drop_indices(monkeypatch, trim, expected):
    import importlib
    chunks = importlib.import_module('services.chunked_tts')
    reports = []
    monkeypatch.setattr(chunks, 'report_dropped_chunks', lambda *args: reports.append(args))
    waveform = torch.cat([torch.zeros(200), torch.ones(500), torch.zeros(200)])
    output = chunks.join_rendered_chunks(
        [waveform, None, waveform], 1000, crossfade_ms=0,
        texts=['first', 'missing', 'last'], trim_edges=trim,
    )
    assert output.shape[-1] == expected
    assert len(reports) == 1
    assert reports[0][:3] == ([1], 3, ['first', 'missing', 'last'])


def test_omitted_request_join_options_preserve_existing_audio():
    from api.routers.audiobook import LongformRenderRequest
    request = LongformRenderRequest()
    assert (request.line_gap_ms, request.paragraph_gap_ms, request.trim_edges) == (0, 0, False)
