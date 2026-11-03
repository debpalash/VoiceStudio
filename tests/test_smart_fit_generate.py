"""Smart Fit generate path — integration tests with a mocked TTS engine.

Exercises the `timing_strategy="smart_fit"` branch of
api.routers.dub_generate end-to-end (TTS loop → planner → mix →
persistence), hermetically: fake model, no DB, no ffmpeg (the atempo pipe
is replaced by deterministic linear interpolation), WAVs under tmp_path.

Covers:
  - audio-only stretch: a segment whose natural audio modestly overflows
    its slot is sped up in place, the track keeps the original duration;
  - hybrid: caps split the burden, the fitted track grows, fit_plans[lang]
    is persisted in the exact filter-graph dict shape with truthful
    fitted_segments cue times and a fit_fp;
  - video_stretch_plans stays untouched by smart_fit runs;
  - strategy-transition guard: strict_slot leaves slot-squeezed WAVs on
    disk (seg_wav_kind="slotted"); the next smart_fit partial regen forces
    one full re-TTS, after which fit-only re-mixes (regen_only=[]) reuse
    the natural WAVs without touching the model;
  - old-strategy back-compat: concise runs never write fit_plans.
"""
from __future__ import annotations

import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import asyncio
import json

import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from schemas.requests import DubRequest
from services.fit_planner import FitParams


SR = 24000


class _FakeModel:
    """Deterministic 'TTS engine': the text encodes its own natural duration
    as a `<seconds>:` prefix (e.g. "1.5:hola") so each test controls how
    much the dub overflows its slot."""

    sampling_rate = SR

    def __init__(self):
        self.calls: list[str] = []

    def generate(self, text=None, **kwargs):
        self.calls.append(text)
        dur = float(text.split(":", 1)[0])
        n = int(dur * SR)
        return [torch.full((1, n), 0.25)]


class _FakeBackend:
    """Adapts the list-returning _FakeModel above to the TTSBackend.generate()
    contract (a single tensor, not a list) that resolve_generation_backend()
    now hands dub_generate.py (issue #312 class)."""

    applies_own_mastering = False

    def __init__(self, model):
        self._model = model

    @property
    def sample_rate(self):
        return self._model.sampling_rate

    def generate(self, *a, **kw):
        return self._model.generate(*a, **kw)[0]


async def _fake_stretch(wav, target_samples, sr):
    """Stand-in for the ffmpeg atempo pipe: deterministic linear interp."""
    if target_samples <= 0 or wav.shape[-1] == target_samples:
        return wav
    return torch.nn.functional.interpolate(
        wav.unsqueeze(0), size=target_samples, mode="linear", align_corners=False,
    ).squeeze(0)


@pytest.fixture
def patched_generate(monkeypatch, tmp_path):
    import api.routers.dub_generate as dg
    from services import audio_io

    model = _FakeModel()

    async def _fake_resolve_generation_backend(**kwargs):
        return _FakeBackend(model)

    job = {
        "duration": 4.0,
        "dubbed_tracks": {},
        "speaker_clones": {},
    }
    job_dir = tmp_path / "jobX"
    job_dir.mkdir()

    monkeypatch.setattr(dg, "resolve_generation_backend", _fake_resolve_generation_backend)
    monkeypatch.setattr(dg, "_get_job", lambda job_id: job)
    monkeypatch.setattr(dg, "_save_job", lambda job_id, j: None)
    monkeypatch.setattr(dg, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(
        dg, "dub_seg_path",
        lambda job_id, seg_id: str(job_dir / f"seg_{seg_id}.wav"),
    )
    monkeypatch.setattr(dg, "rvc_is_enabled", lambda: False)
    monkeypatch.setattr(dg, "mark_synthetic", lambda wav, sr, **kw: wav)
    monkeypatch.setattr(dg, "apply_mastering", lambda a, sample_rate=None: a)
    monkeypatch.setattr(dg, "get_effect_chain", lambda preset: None)
    monkeypatch.setattr(dg, "apply_effects_chain", lambda a, **k: a)
    monkeypatch.setattr(dg, "normalize_audio", lambda a, target_dBFS=None: a)
    monkeypatch.setattr(dg, "_pitch_preserving_stretch", _fake_stretch)

    events: list[str] = []

    class _StubTaskManager:
        def is_cancelled(self, task_id):
            return False

        async def add_task(self, task_id, task_type, func, *args, **kwargs):
            async for evt in func(*args):
                events.append(evt)

    monkeypatch.setattr(dg, "task_manager", _StubTaskManager())

    def run(body: dict) -> list[dict]:
        events.clear()
        req = DubRequest(**body)
        asyncio.run(dg.dub_generate("jobX", req))
        parsed = []
        for e in events:
            line = e.strip()
            if line.startswith("data: "):
                parsed.append(json.loads(line[len("data: "):]))
        return parsed

    return run, model, job, job_dir


def _body(segments, **extra):
    return {
        "segments": segments,
        "segment_ids": [str(i) for i in range(len(segments))],
        "language": "Auto",
        "language_code": "es",
        "num_step": 4,
        **extra,
    }


def _done(parsed):
    done = [p for p in parsed if p.get("type") == "done"]
    assert done, f"no done event in {parsed}"
    return done[0]


def _track_samples(job_dir):
    import torchaudio
    wav, sr = torchaudio.load(str(job_dir / "dubbed_es.wav"))
    return wav.shape[-1], sr


# ── HTTP validation before synthesis ───────────────────────────────────


@pytest.fixture
def generate_client(patched_generate):
    """Exercise the real route without starting the application's models."""
    import api.routers.dub_generate as dg

    app = FastAPI()
    app.include_router(dg.router)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client


@pytest.mark.parametrize("field", [
    "max_audio_only_rate", "audio_rate_cap", "video_slow_cap", "gap_guard_s",
])
@pytest.mark.parametrize("value", [-0.01, "NaN", "Infinity", "-Infinity"])
def test_smart_fit_api_rejects_invalid_options_before_synthesis(
    generate_client, patched_generate, field, value,
):
    _run, model, job, job_dir = patched_generate
    response = generate_client.post("/dub/generate/jobX", json=_body(
        [{"start": 0.0, "end": 1.0, "text": "1.0:test"}],
        timing_strategy="smart_fit", fit_options={field: value},
    ))

    # Non-finite strings are valid JSON and exercise Pydantic's float
    # coercion; numeric non-finite values are also covered at schema level.
    assert response.status_code == 422, f"fake engine calls: {model.calls}"
    assert response.json()["detail"][0]["loc"] == ["body", "fit_options", field]
    assert model.calls == []
    assert job["dubbed_tracks"] == {}
    assert list(job_dir.iterdir()) == []


@pytest.mark.parametrize("field", ["max_audio_only_rate", "audio_rate_cap", "video_slow_cap"])
@pytest.mark.parametrize("value", [0.0, 0.99])
def test_smart_fit_api_rejects_rates_below_one_before_synthesis(
    generate_client, patched_generate, field, value,
):
    _run, model, _job, _job_dir = patched_generate
    response = generate_client.post("/dub/generate/jobX", json=_body(
        [{"start": 0.0, "end": 1.0, "text": "1.0:test"}],
        timing_strategy="smart_fit", fit_options={field: value},
    ))
    assert response.status_code == 422, f"fake engine calls: {model.calls}"
    assert response.json()["detail"][0]["loc"] == ["body", "fit_options", field]
    assert model.calls == []


@pytest.mark.parametrize("extra", [
    {}, {"fit_options": None}, {"fit_options": {}},
    {"fit_options": {
        "max_audio_only_rate": None, "audio_rate_cap": None,
        "video_slow_cap": None, "gap_guard_s": None, "allow_video_retime": None,
    }},
    {"fit_options": {
        "max_audio_only_rate": 1.0, "audio_rate_cap": 1.0,
        "video_slow_cap": 1.0, "gap_guard_s": 0.0, "allow_video_retime": False,
    }},
    {"fit_options": {
        "max_audio_only_rate": 10.0, "audio_rate_cap": 20.0,
        "video_slow_cap": 30.0, "gap_guard_s": 60.0,
    }},
], ids=["omitted", "null", "empty", "null-fields", "boundaries-and-false", "large-finite"])
def test_smart_fit_api_preserves_valid_options_and_defaults(
    generate_client, patched_generate, extra,
):
    _run, model, job, job_dir = patched_generate
    job["duration"] = 1.0
    response = generate_client.post("/dub/generate/jobX", json=_body(
        [{"start": 0.0, "end": 1.0, "text": "1.0:test"}],
        timing_strategy="smart_fit", **extra,
    ))

    assert response.status_code == 200
    assert response.json()["task_id"]
    assert model.calls == ["1.0:test"]
    defaults = FitParams()
    overrides = extra.get("fit_options") or {}
    expected = {"timing_strategy": "smart_fit"}
    for field in ("max_audio_only_rate", "audio_rate_cap", "video_slow_cap",
                  "gap_guard_s", "allow_video_retime", "min_audio_rate"):
        value = overrides.get(field)
        expected[field] = getattr(defaults, field) if value is None else value
    assert job["fit_plans"]["es"]["params"] == expected
    assert job["dubbed_tracks"]["es"]["duration"] == pytest.approx(1.0)
    assert (job_dir / "dubbed_es.wav").exists()


# ── Audio-only stretch ─────────────────────────────────────────────────


# On CI-Linux (never reproduced on macOS) something in this test's call chain
# flips torch's default dtype to float16 and leaks it into later tests. The
# fixture save/restores the dtype and logs the setter's captured stack trace
# so the CI log names the culprit call chain (see conftest.py).
@pytest.mark.usefixtures("torch_dtype_isolation")
def test_fresh_speech_is_watermarked_exactly_once(patched_generate, monkeypatch):
    import torchaudio

    import api.routers.dub_generate as dg
    import services.watermark as watermark

    run, _model, job, job_dir = patched_generate
    job["duration"] = 35.0
    mark_lengths: list[int] = []
    probe_at = 80_000

    def _fake_mark(wav, sr, **_kw):
        n = wav.shape[-1]
        mark_lengths.append(n)
        out = wav.clone()
        out[..., n - probe_at - 500:n - probe_at + 500] = -0.9
        return out

    def _fake_detect(waveform, sample_rate):
        found = bool((waveform < -0.5).any())
        return {"is_watermarked": found, "confidence": 1.0 if found else 0.0,
                "message_bits": "", "is_omnivoice": found}

    monkeypatch.setattr(dg, "mark_synthetic", _fake_mark)
    monkeypatch.setattr(watermark, "detect_watermark", _fake_detect)

    done = _done(run(_body(
        [{"start": 1.0, "end": 34.0, "text": "33.0:long line"}],
        timing_strategy="concise",
    )))
    assert done["segments_processed"] == 1

    track, sr = torchaudio.load(str(job_dir / "dubbed_es.wav"))
    assert track.shape[-1] == int(35.0 * sr)
    assert watermark.detect_watermark(track, sr)["is_watermarked"]
    # The probe lands in the second 30 s chunk of the final track.
    assert int(1.0 * sr) + int(33.0 * SR) - probe_at > 30 * sr

    seg, seg_sr = torchaudio.load(str(job_dir / "seg_es_0.wav"))
    assert watermark.detect_watermark(seg, seg_sr)["is_watermarked"]

    # One mark call, on the 33 s fresh segment: the 35 s track is not re-marked.
    assert mark_lengths == [int(33.0 * SR)]


def test_zero_and_negative_duration_segments_do_not_break_assembly(patched_generate):
    import torchaudio

    run, _model, job, job_dir = patched_generate
    job["duration"] = 5.0
    segs = [
        {"start": 0.0, "end": 1.0, "text": "0.5:hola"},
        {"start": 2.0, "end": 2.0, "text": ""},
        {"start": 3.0, "end": 4.0, "text": "   "},
        {"start": 4.5, "end": 4.0, "text": ""},
    ]
    done = _done(run(_body(segs, timing_strategy="concise")))
    assert done["segments_processed"] == 4

    track, sr = torchaudio.load(str(job_dir / "dubbed_es.wav"))
    assert track.shape[-1] == int(5.0 * sr)
    assert not list(job_dir.glob("seg_mix_*.wav"))


def test_smart_fit_audio_only_stretch_keeps_original_duration(patched_generate):
    run, model, job, job_dir = patched_generate
    # seg0 [0,1] natural 0.5s → far short of its slack-extended ~1.95s slot →
    # underrun fill slows it at the 0.85× floor (it still ends inside the
    # slot).  seg1 [2,3] is last → slot extends to 4.0s (2.0s); natural 2.2s
    # → need 1.1 → audio-only 1.1×, no video.
    segs = [
        {"start": 0.0, "end": 1.0, "text": "0.5:hola"},
        {"start": 2.0, "end": 3.0, "text": "2.2:buenos dias"},
    ]
    done = _done(run(_body(segs, timing_strategy="smart_fit")))

    assert done["timing_strategy"] == "smart_fit"
    fs = done["fit_status"]
    assert fs[0]["status"] == "audio_slowed"
    assert fs[0]["audio_rate"] == pytest.approx(0.85, abs=1e-3)
    assert fs[1]["status"] == "audio_stretched"
    assert fs[1]["audio_rate"] == pytest.approx(1.1, abs=1e-3)
    assert "video_ratio" not in fs[1]

    # No video retime needed → fitted timeline == original timeline.
    n, sr = _track_samples(job_dir)
    assert n == int(4.0 * sr)
    assert job["dubbed_tracks"]["es"]["duration"] == pytest.approx(4.0, abs=1e-3)

    plan = job["fit_plans"]["es"]
    assert plan["total_duration"] == pytest.approx(4.0, abs=1e-3)
    assert all(e["stretch_ratio"] == pytest.approx(1.0) for e in plan["plan"])
    # Cue end = new_start + stretched length (2.2/1.1 = 2.0s).
    cues = plan["fitted_segments"]
    assert cues[1]["start"] == pytest.approx(2.0, abs=1e-3)
    assert cues[1]["end"] == pytest.approx(4.0, abs=1e-2)

    # smart_fit never touches the stretch_video keyspace.
    assert "video_stretch_plans" not in job
    assert job["seg_wav_kind"] == "natural"
    # The on-disk per-segment WAV stays natural-rate (2.2s, not slot-squeezed).
    import torchaudio
    wav, sr2 = torchaudio.load(str(job_dir / "seg_es_1.wav"))
    assert wav.shape[-1] == int(2.2 * SR)


# ── Hybrid: audio + video split, fitted timeline grows ─────────────────


def test_smart_fit_hybrid_grows_timeline_and_persists_plan(patched_generate):
    run, model, job, job_dir = patched_generate
    job["duration"] = 1.0
    # One segment covering the whole 1s video, natural 2.0s → need 2.0 →
    # sqrt split: audio 1.4142×, video 1.4142×.
    segs = [{"start": 0.0, "end": 1.0, "text": "2.0:una frase muy larga"}]
    done = _done(run(_body(segs, timing_strategy="smart_fit")))

    fs = done["fit_status"][0]
    assert fs["status"] == "hybrid"
    assert fs["audio_rate"] == pytest.approx(2.0 ** 0.5, abs=1e-3)
    assert fs["video_ratio"] == pytest.approx(2.0 ** 0.5, abs=1e-3)

    plan = job["fit_plans"]["es"]
    assert plan["total_duration"] == pytest.approx(2.0 ** 0.5, abs=1e-2)
    entry = plan["plan"][0]
    assert set(entry) == {"orig_start", "orig_end", "new_start", "new_end", "stretch_ratio"}
    assert entry["stretch_ratio"] == pytest.approx(2.0 ** 0.5, abs=1e-3)
    assert plan["fit_fp"] and isinstance(plan["fit_fp"], str)
    assert plan["params"]["timing_strategy"] == "smart_fit"
    assert job["dubbed_tracks"]["es"]["fit_fp"] == plan["fit_fp"]

    # The plan is consumable by the export filter-graph builder as-is.
    from api.routers.dub_export import _build_video_stretch_filter_graph
    graph, label = _build_video_stretch_filter_graph(plan["plan"], orig_dur=plan["orig_duration"])
    assert label == "[vstretched]"
    assert "setpts=" in graph

    # The fitted dub track is longer than the source video.
    n, sr = _track_samples(job_dir)
    assert n == pytest.approx(int(2.0 ** 0.5 * sr), abs=sr // 100)


def test_smart_fit_fit_options_override(patched_generate):
    run, model, job, job_dir = patched_generate
    job["duration"] = 1.0
    segs = [{"start": 0.0, "end": 1.0, "text": "2.0:texto"}]
    parsed = run(_body(
        segs, timing_strategy="smart_fit",
        fit_options={"allow_video_retime": False},
    ))
    assert any(e.get("error_code") == "dub_timing_overflow" for e in parsed)
    assert not any(e.get("type") == "done" for e in parsed)
    assert not (job_dir / "dubbed_es.wav").exists()


def test_smart_fit_min_audio_rate_override_widens_and_disables_underrun_fill(patched_generate):
    run, model, job, job_dir = patched_generate
    # seg0 [0,1] natural 0.5s sits in a ~1.95s slot: the default 0.85 floor
    # slows it only to 0.85x; a 0.6 floor lets it stretch further, and 1.0
    # turns the fill off (#2896).
    segs = [
        {"start": 0.0, "end": 1.0, "text": "0.5:hola"},
        {"start": 2.0, "end": 3.0, "text": "2.2:buenos dias"},
    ]
    wide = _done(run(_body(segs, timing_strategy="smart_fit",
                           fit_options={"min_audio_rate": 0.6})))
    assert wide["fit_status"][0]["audio_rate"] == pytest.approx(0.6, abs=1e-3)
    off = _done(run(_body(segs, timing_strategy="smart_fit",
                          fit_options={"min_audio_rate": 1.0})))
    assert off["fit_status"][0]["status"] == "fits"


def test_smart_fit_rejects_out_of_range_min_audio_rate():
    from pydantic import ValidationError

    for bad in (0.2, 1.5, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            DubRequest(**_body([{"start": 0.0, "end": 1.0, "text": "x"}],
                               timing_strategy="smart_fit",
                               fit_options={"min_audio_rate": bad}))


# ── Strategy-transition guard + fit-only re-mix ────────────────────────


def test_strict_slot_to_smart_fit_forces_one_full_regen(patched_generate):
    run, model, job, job_dir = patched_generate
    segs = [
        {"start": 0.0, "end": 1.0, "text": "1.5:uno"},
        {"start": 2.0, "end": 3.0, "text": "1.5:dos"},
    ]

    # strict_slot run → slot-squeezed WAVs on disk.
    run(_body(segs, timing_strategy="strict_slot"))
    assert job["seg_wav_kind"] == "natural"
    job["seg_wav_kind"] = "slotted"
    job["seg_wav_kind_by_lang"]["es"] = "slotted"
    import torchaudio
    wav, _ = torchaudio.load(str(job_dir / "seg_es_0.wav"))
    assert wav.shape[-1] == int(1.5 * SR)  # current caches retain full speech

    # smart_fit "re-mix only" request — but the disk WAVs are slotted, so
    # the guard must force a full re-TTS instead of double-compressing.
    model.calls.clear()
    run(_body(segs, timing_strategy="smart_fit", regen_only=[]))
    assert model.calls == ["1.5:uno", "1.5:dos"]
    assert job["seg_wav_kind"] == "natural"
    wav, _ = torchaudio.load(str(job_dir / "seg_es_0.wav"))
    assert wav.shape[-1] == int(1.5 * SR)  # natural-rate now

    # Now a fit-only change (re-mix): natural WAVs are reusable — zero TTS.
    model.calls.clear()
    parsed = run(_body(
        segs,
        timing_strategy="smart_fit",
        regen_only=[],
        fit_options={"allow_video_retime": False},
    ))
    assert model.calls == []
    done = _done(parsed)
    assert done["timing_strategy"] == "smart_fit"
    assert job["fit_plans"]["es"]["params"]["allow_video_retime"] is False


@pytest.mark.parametrize("natural_strategy", ["concise", "stretch_video"])
def test_strict_slot_to_natural_mode_forces_one_full_regen(
    patched_generate, natural_strategy,
):
    """Every natural-rate timing mode needs the slotted-cache guard.

    A legacy strict-slot render destructively trimmed its durable segment WAVs.  A later
    natural-rate re-mix cannot recover the missing tails from those files, and
    must synthesize once before it may label the cache ``natural``.
    """
    run, model, job, job_dir = patched_generate
    segs = [
        {"start": 0.0, "end": 2.0, "text": "1.5:uno"},
        {"start": 2.0, "end": 4.0, "text": "1.5:dos"},
    ]

    run(_body(segs, timing_strategy="strict_slot"))
    assert job["seg_wav_kind"] == "natural"
    job["seg_wav_kind"] = "slotted"
    job["seg_wav_kind_by_lang"]["es"] = "slotted"

    model.calls.clear()
    run(_body(segs, timing_strategy=natural_strategy, regen_only=[]))

    assert model.calls == ["1.5:uno", "1.5:dos"]
    assert job["seg_wav_kind"] == "natural"
    import torchaudio
    wav, _ = torchaudio.load(str(job_dir / "seg_es_0.wav"))
    assert wav.shape[-1] == int(1.5 * SR)


@pytest.mark.parametrize("natural_strategy", ["concise", "stretch_video", "smart_fit"])
def test_natural_cache_remix_skips_mix_scratch_roundtrip(
    patched_generate, monkeypatch, natural_strategy,
):
    """A fit-only re-mix should decode each durable segment exactly once.

    The durable natural-rate WAV is already the assembly input.  Loading it,
    writing an identical ``mix_*`` scratch WAV, then loading that copy again
    doubles decode I/O and adds one synchronous write per unchanged segment.
    """
    run, model, _job, job_dir = patched_generate
    segs = [
        {"start": 0.0, "end": 1.0, "text": "0.8:uno"},
        {"start": 1.5, "end": 2.5, "text": "0.8:dos"},
    ]
    run(_body(segs, timing_strategy=natural_strategy))

    import api.routers.dub_generate as dg
    from services import audio_io

    original_load = audio_io.torchaudio.load
    original_save = dg.atomic_save_wav
    loaded_paths: list[str] = []
    saved_paths: list[str] = []

    def spy_load(path, *args, **kwargs):
        loaded_paths.append(os.fspath(path))
        return original_load(path, *args, **kwargs)

    def spy_save(path, *args, **kwargs):
        saved_paths.append(os.fspath(path))
        return original_save(path, *args, **kwargs)

    monkeypatch.setattr(audio_io.torchaudio, "load", spy_load)
    monkeypatch.setattr(dg, "atomic_save_wav", spy_save)
    model.calls.clear()

    parsed = run(_body(segs, timing_strategy=natural_strategy, regen_only=[]))

    cache_paths = [str(job_dir / f"seg_es_{i}.wav") for i in range(2)]
    _done(parsed)
    assert model.calls == []
    assert saved_paths == []
    assert loaded_paths == cache_paths
    samples, sample_rate = _track_samples(job_dir)
    assert samples == int(4.0 * sample_rate)


def test_foreign_rate_natural_cache_keeps_resample_scratch_fallback(
    patched_generate, monkeypatch,
):
    """A cache at another rate still takes the one-time transform path."""
    run, model, _job, job_dir = patched_generate
    segs = [{"start": 0.0, "end": 1.0, "text": "0.8:uno"}]
    run(_body(segs, timing_strategy="concise"))

    import api.routers.dub_generate as dg
    from services import audio_io
    import torchaudio
    import torchaudio.functional as AF

    cached_path = str(job_dir / "seg_es_0.wav")
    cached_wav, cached_sr = torchaudio.load(cached_path)
    foreign_sr = cached_sr // 2
    torchaudio.save(cached_path, AF.resample(cached_wav, cached_sr, foreign_sr), foreign_sr)

    original_load = audio_io.torchaudio.load
    original_save = dg.atomic_save_wav
    loaded_names: list[str] = []
    saved_names: list[str] = []

    def spy_load(path, *args, **kwargs):
        loaded_names.append(os.path.basename(os.fspath(path)))
        return original_load(path, *args, **kwargs)

    def spy_save(path, *args, **kwargs):
        saved_names.append(os.path.basename(os.fspath(path)))
        return original_save(path, *args, **kwargs)

    monkeypatch.setattr(audio_io.torchaudio, "load", spy_load)
    monkeypatch.setattr(dg, "atomic_save_wav", spy_save)
    model.calls.clear()

    parsed = run(_body(segs, timing_strategy="concise", regen_only=[]))

    _done(parsed)
    assert model.calls == []
    assert loaded_names == ["seg_es_0.wav", "seg_mix_0.wav"]
    assert saved_names == ["seg_mix_0.wav"]
    assert not (job_dir / "seg_mix_0.wav").exists()


def test_natural_cache_decode_failure_blocks_export(
    patched_generate, monkeypatch,
):
    """A valid WAV header must not move a corrupt payload outside recovery."""
    run, model, _job, job_dir = patched_generate
    segs = [{"start": 0.0, "end": 1.0, "text": "0.8:uno"}]
    run(_body(segs, timing_strategy="concise"))

    import api.routers.dub_generate as dg
    from services import audio_io

    original_load = audio_io.torchaudio.load
    cached_path = str(job_dir / "seg_es_0.wav")

    def fail_cached_decode(path, *args, **kwargs):
        if os.fspath(path) == cached_path:
            raise RuntimeError("truncated cached audio")
        return original_load(path, *args, **kwargs)

    monkeypatch.setattr(audio_io.torchaudio, "load", fail_cached_decode)
    model.calls.clear()

    parsed = run(_body(segs, timing_strategy="concise", regen_only=[]))

    assert model.calls == []
    assert any(event.get("error_code") == "dub_speech_missing" for event in parsed)
    assert not any(event.get("type") == "done" for event in parsed)


def test_rvc_keeps_natural_rate_audio_outside_strict_slot(
    patched_generate, monkeypatch,
):
    """RVC output obeys the same timing-mode cache invariant as plain TTS."""
    run, _model, job, job_dir = patched_generate
    import api.routers.dub_generate as dg
    from services import audio_io
    import torchaudio

    monkeypatch.setattr(dg, "rvc_is_enabled", lambda: True)
    monkeypatch.setattr(dg, "apply_rvc", lambda _path: None)

    run(_body(
        [{"start": 0.0, "end": 2.0, "text": "1.5:uno"}],
        timing_strategy="concise",
    ))

    wav, _ = torchaudio.load(str(job_dir / "seg_es_0.wav"))
    assert wav.shape[-1] == int(1.5 * SR)
    assert job["seg_wav_kind"] == "natural"


# ── Old-strategy back-compat ───────────────────────────────────────────


def test_concise_run_never_writes_fit_plans(patched_generate):
    run, model, job, job_dir = patched_generate
    segs = [{"start": 0.0, "end": 1.0, "text": "0.5:hola"}]
    done = _done(run(_body(segs, timing_strategy="concise")))
    assert done["timing_strategy"] == "concise"
    assert "fit_plans" not in job
    assert job["seg_wav_kind"] == "natural"
