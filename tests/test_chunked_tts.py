"""Wave 1.2 — chunked long-form TTS (unlimited-length generation).

Unit tests for the splitter/concat (no model) + endpoint tests proving
/generate chunks long text per-sentence through the engine adapter and
keeps the single-shot path for short text. Engine stubbing mirrors
tests/test_generate_engine.py.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import importlib

import pytest
import torch

from services.chunked_tts import (
    concatenate_audio_chunks,
    split_text_into_chunks,
)


# ── Splitter ────────────────────────────────────────────────────────────────

def test_short_text_is_single_chunk():
    assert split_text_into_chunks("Hello world.", 800) == ["Hello world."]


def test_long_text_splits_at_sentence_boundaries():
    text = "First sentence here. " * 60  # ~1260 chars
    chunks = split_text_into_chunks(text.strip(), 800)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)
    # Every chunk ends on the sentence boundary, not mid-word.
    assert all(c.endswith(".") for c in chunks)


def test_abbreviations_do_not_split():
    # Real sentence ends are present, so the splitter must choose those —
    # never the period of an abbreviation (which it should skip entirely).
    text = ("Dr. Smith met Mr. Jones at the old lab. They talked there all day. " * 20).strip()
    chunks = split_text_into_chunks(text, 200)
    assert len(chunks) > 1
    for c in chunks:
        assert c.endswith(("lab.", "day.")), f"chunk ended off-sentence: {c[-12:]!r}"


def test_decimals_do_not_split():
    text = ("The value rose to 3.14159 which pleased everyone greatly today " * 16).strip()
    for c in split_text_into_chunks(text, 150):
        assert not c.endswith("3."), "split inside a decimal"


def test_bracket_tags_are_atomic():
    text = ("She paused [laugh] and went on speaking calmly " * 30).strip()
    for c in split_text_into_chunks(text, 120):
        assert c.count("[") == c.count("]"), f"tag split across chunks: {c!r}"


def test_clause_fallback_when_no_sentence_end():
    text = ("alpha beta gamma, delta epsilon zeta, " * 40).strip()
    chunks = split_text_into_chunks(text, 200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_hard_cut_when_no_boundaries():
    text = "x" * 2000
    chunks = split_text_into_chunks(text, 800)
    assert len(chunks) == 3
    assert "".join(chunks) == text


def test_zero_max_chars_disables_chunking():
    text = "word " * 500
    assert split_text_into_chunks(text.strip(), 0) == [text.strip()]


def test_empty_text():
    assert split_text_into_chunks("   ", 800) == []


# ── Concat ──────────────────────────────────────────────────────────────────

def test_crossfade_length_math():
    sr = 24000
    a = torch.ones(sr)            # 1 s
    b = torch.ones(sr)            # 1 s
    out = concatenate_audio_chunks([a, b], sr, crossfade_ms=50)
    overlap = int(sr * 50 / 1000)
    assert out.shape[-1] == 2 * sr - overlap


def test_hard_cut_concat():
    sr = 24000
    a, b = torch.ones(100), torch.ones(200)
    out = concatenate_audio_chunks([a, b], sr, crossfade_ms=0)
    assert out.shape[-1] == 300


def test_crossfade_is_smooth_at_seam():
    sr = 1000
    a = torch.ones(500)
    b = torch.zeros(500)
    out = concatenate_audio_chunks([a, b], sr, crossfade_ms=100)  # 100 samples
    seam = out[400:500]
    # Linear blend from 1.0 toward 0.0 across the overlap — monotone, no step.
    assert seam[0] == pytest.approx(1.0, abs=1e-5)
    assert seam[-1] == pytest.approx(0.0, abs=1e-2)
    assert torch.all(seam[:-1] >= seam[1:] - 1e-6)


def test_multichannel_chunks_concat_on_last_axis():
    sr = 24000
    a, b = torch.ones(2, 1000), torch.ones(2, 1000)
    out = concatenate_audio_chunks([a, b], sr, crossfade_ms=10)
    assert out.shape[0] == 2
    assert out.shape[-1] == 2000 - int(sr * 10 / 1000)


def test_overlap_clamped_to_short_chunk():
    sr = 24000
    a, b = torch.ones(10), torch.ones(5000)
    out = concatenate_audio_chunks([a, b], sr, crossfade_ms=50)  # 1200 > len(a)
    assert out.shape[-1] == 5000  # overlap clamped to 10


def test_single_and_empty_chunk_lists():
    sr = 24000
    only = torch.ones(123)
    assert concatenate_audio_chunks([only], sr).shape[-1] == 123
    assert concatenate_audio_chunks([], sr).shape[-1] == 1  # silent guard


# ── Mixed-rank chunks (#897) ─────────────────────────────────────────────────
# Engines emit (1, samples) per the TTSBackend contract while silence buffers
# were bare (samples,) — torch.cat crashed longform chapter renders with
# "RuntimeError: Tensors must have same number of dimensions: got 1 and 2".

def test_mixed_rank_chunks_hard_cut():
    out = concatenate_audio_chunks([torch.ones(1, 300), torch.zeros(200)],
                                   24000, crossfade_ms=0)
    assert out.shape == (1, 500)


def test_mixed_rank_chunks_hard_cut_1d_first():
    out = concatenate_audio_chunks([torch.zeros(200), torch.ones(1, 300)],
                                   24000, crossfade_ms=0)
    assert out.shape == (1, 500)


def test_mixed_rank_chunks_crossfade():
    sr = 1000
    out = concatenate_audio_chunks([torch.ones(1, 500), torch.zeros(500)],
                                   sr, crossfade_ms=100)  # 100-sample overlap
    assert out.shape == (1, 900)


def test_mono_chunk_broadcasts_to_stereo():
    # Honest channel handling: a mono chunk follows the widest channel count.
    out = concatenate_audio_chunks([torch.ones(2, 100), torch.zeros(1, 50)],
                                   24000, crossfade_ms=0)
    assert out.shape == (2, 150)
    out = concatenate_audio_chunks([torch.ones(2, 100), torch.zeros(60)],
                                   24000, crossfade_ms=0)  # 1-D mono, too
    assert out.shape == (2, 160)


def test_all_1d_output_stays_1d():
    out = concatenate_audio_chunks([torch.ones(100), torch.ones(100)],
                                   24000, crossfade_ms=0)
    assert out.dim() == 1 and out.shape[-1] == 200


def test_all_2d_output_stays_2d():
    out = concatenate_audio_chunks([torch.ones(1, 100), torch.ones(1, 100)],
                                   24000, crossfade_ms=0)
    assert out.shape == (1, 200)


# ── Endpoint integration (stubbed engine, pattern from test_generate_engine) ─

def _tts_mod():
    return importlib.import_module("services.tts_backend")


def _make_fake_engine(engine_id="fake-chunk-engine"):
    class _FakeEngine(_tts_mod().TTSBackend):
        id = engine_id
        display_name = "Fake Chunk Engine (test)"
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append((text, kw))
            return torch.zeros(1, 4800)  # 200 ms per chunk

    return _FakeEngine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


def test_long_text_is_chunked_through_engine(client, monkeypatch):
    fake = _make_fake_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-chunk-engine", fake)

    long_text = ("This is a complete sentence for the chunker. " * 40).strip()  # ~1840 chars
    res = client.post("/generate", data={
        "text": long_text, "engine": "fake-chunk-engine", "seed": "7",
    })

    assert res.status_code == 200, res.text
    assert len(fake.calls) >= 2, "long text should fan out into multiple engine calls"
    rejoined = " ".join(t for t, _ in fake.calls)
    assert rejoined.split() == long_text.split()  # no words lost or reordered
    # Chunked calls must not carry the un-splittable overall duration.
    assert all(kw.get("duration") is None for _, kw in fake.calls)


def test_short_text_stays_single_shot(client, monkeypatch):
    fake = _make_fake_engine("fake-single-engine")
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-single-engine", fake)

    res = client.post("/generate", data={
        "text": "Just one short line.", "engine": "fake-single-engine",
    })

    assert res.status_code == 200, res.text
    assert len(fake.calls) == 1


def test_chunking_disabled_with_zero(client, monkeypatch):
    fake = _make_fake_engine("fake-nochunk-engine")
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-nochunk-engine", fake)

    long_text = ("Another complete sentence goes here. " * 40).strip()
    res = client.post("/generate", data={
        "text": long_text, "engine": "fake-nochunk-engine", "max_chunk_chars": "0",
    })

    assert res.status_code == 200, res.text
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == long_text


# ── Arabic-script / Devanagari sentence enders ─────────────────────────────
# Written as escapes: U+06D4 Arabic full stop (Urdu), U+061F Arabic question
# mark, U+0964 Devanagari danda, U+060C Arabic comma.
_UR_STOP = "۔"
_UR_Q = "؟"
_UR_COMMA = "،"
# "yeh ek mukammal jumla hai" in Urdu script, no ender.
_UR_SENTENCE = "یہ ایک مکمل جملہ ہے"


def test_urdu_paragraph_splits_only_at_full_stops():
    # ~1,200 chars of Urdu sentences ending in the Arabic full stop. Before the
    # fix the splitter ignored it and cut at an arbitrary space mid-sentence.
    sentences = [f"{_UR_SENTENCE} {n}{_UR_STOP if n % 3 else _UR_Q}" for n in range(50)]
    text = " ".join(sentences)
    assert len(text) > 1100
    chunks = split_text_into_chunks(text, 800)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)
    assert all(c[-1] in (_UR_STOP, _UR_Q) for c in chunks), [c[-5:] for c in chunks]
    assert " ".join(chunks).split() == text.split()


def test_urdu_full_stop_without_following_space():
    sentence = _UR_SENTENCE * 3 + _UR_STOP  # no space after the ender
    text = sentence * 20
    chunks = split_text_into_chunks(text, 400)
    assert len(chunks) > 1
    assert all(c.endswith(_UR_STOP) for c in chunks)
    assert "".join(chunks) == text


def test_devanagari_danda_is_a_sentence_end():
    text = ("abc def ghi" + "।") * 80  # no space after the danda
    chunks = split_text_into_chunks(text, 300)
    assert len(chunks) > 1
    assert all(c.endswith("।") for c in chunks)


def test_arabic_comma_is_a_clause_boundary():
    text = (_UR_SENTENCE + _UR_COMMA + " ") * 40  # no sentence end at all
    chunks = split_text_into_chunks(text.strip(), 300)
    assert len(chunks) > 1
    assert all(c.endswith(_UR_COMMA) for c in chunks[:-1])


def test_long_pause_spans_are_chunked_through_engine(client, monkeypatch):
    # A real Urdu script had 1,000+ char spans between [pause] markers; each
    # went to the engine in one shot and the audio degraded. Each long span
    # must now fan out into sentence chunks that share the take's seed (#3730).
    fake = _make_fake_engine("fake-pause-chunk-engine")
    fake.calls = []
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-pause-chunk-engine", fake)
    fake.supports_generation_seed = True

    span = " ".join(f"{_UR_SENTENCE} {n}{_UR_STOP}" for n in range(50))  # ~1,200 chars
    assert len(span) > 1100
    text = f"{span} [pause 500ms] {span}"
    res = client.post("/generate", data={
        "text": text, "engine": "fake-pause-chunk-engine", "seed": "7",
    })

    assert res.status_code == 200, res.text
    assert len(fake.calls) >= 4, "each long span should fan out into chunks"
    assert all(len(t) <= 800 for t, _ in fake.calls)
    assert all(t.endswith(_UR_STOP) for t, _ in fake.calls)
    assert " ".join(t for t, _ in fake.calls).split() == (span + " " + span).split()
    assert [kw.get("seed") for _, kw in fake.calls] == [7] * len(fake.calls)


def test_dropped_chunk_log_scrubs_secrets_and_home_paths(monkeypatch, caplog):
    """Dropped-sentence text reaches the shareable log scrubbed; the caller's
    sink still gets the raw text for the user (#3021)."""
    import logging

    from services.chunked_tts import report_dropped_chunks

    secret = "s3cr3t-value-0123456789abcdef"
    monkeypatch.setenv("MY_API_TOKEN", secret)
    text = f"key {secret} at /Users/alice/notes.txt"
    sink: list = []
    with caplog.at_level(logging.WARNING):
        report_dropped_chunks([0], 1, texts=[text], sink=sink)
    logged = caplog.text
    assert secret not in logged
    assert "/Users/alice" not in logged
    assert "Dropped 1 of 1" in logged
    assert sink == [text]
