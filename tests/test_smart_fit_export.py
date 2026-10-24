"""Smart Fit Phase B — video-retime export pipeline.

Unit tier (hermetic, no ffmpeg):
  - ``_video_retime_plan_for`` resolution across the legacy
    ``video_stretch_plans`` and Phase A ``fit_plans`` keyspaces, including
    the staleness gate (track re-generated under another strategy);
  - batch partitioning math and the ≤48-chunk single-pass threshold;
  - filter-graph parity (the legacy builder now delegates to
    ``services.video_retime``) plus the new fps-normalisation and
    tail-pad stages;
  - fitted-cue selection for SRT/VTT/burn-in (fit_plans present vs absent);
  - burn-in + smart_fit allowed, burn-in + legacy stretch_video rejected;
  - VFR detection from ffprobe rate strings.

Integration tier (skipped without ffmpeg/ffprobe): renders a synthetic
testsrc video + sine dub track through the retime executor (both tiers)
and the real ``/dub/download`` endpoint, then ffprobes the result —
durations within tolerance, both drift-absorption branches (apad / tpad)
exercised, fitted SRT cues bounded by the video.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

from services.ffmpeg_utils import find_ffmpeg, find_ffprobe
from services.video_retime import (
    RETIME_BATCH_SIZE,
    RETIME_SINGLE_PASS_MAX_CHUNKS,
    RetimeError,
    build_chunk_filter_graph,
    expand_retime_chunks,
    is_vfr,
    partition_batches,
    prepare_smart_fit_video,
    render_retimed_video,
)

_FFMPEG = find_ffmpeg()
_FFPROBE = find_ffprobe()
needs_ffmpeg = pytest.mark.skipif(
    not (_FFMPEG and _FFPROBE), reason="ffmpeg/ffprobe not available",
)


# ---------------------------------------------------------------------------
# Shared plan fixtures (dict shape persisted by Phase A)
# ---------------------------------------------------------------------------

_PLAN = [
    {"orig_start": 1.0, "orig_end": 3.0, "new_start": 1.0, "new_end": 4.0, "stretch_ratio": 1.5},
    {"orig_start": 5.0, "orig_end": 7.0, "new_start": 6.0, "new_end": 8.5, "stretch_ratio": 1.25},
]
_ORIG_DUR = 10.0
# preroll 1.0 + 2*1.5 + gap 2.0 + 2*1.25 + tail 3.0
_EXPECTED_DUR = 11.5

_FITTED = [
    {"id": "a", "start": 1.0, "end": 3.75},
    {"id": "b", "start": 6.0, "end": 8.25},
]


def _smart_fit_job(track_dur: float = _EXPECTED_DUR) -> dict:
    return {
        "video_path": "/nonexistent/original.mp4",
        "duration": _ORIG_DUR,
        "filename": "clip.mp4",
        "timing_strategy": "smart_fit",
        "segments": [
            {"id": "a", "start": 1.0, "end": 3.0, "text": "Hello there",
             "text_original": "hola", "speaker_id": "Speaker 1"},
            {"id": "b", "start": 5.0, "end": 7.0, "text": "General Kenobi",
             "text_original": "general", "speaker_id": "Speaker 1"},
        ],
        "dubbed_tracks": {
            "de": {"path": "/nonexistent/dubbed_de.wav", "language": "German",
                   "language_code": "de", "duration": track_dur,
                   "timing_strategy": "smart_fit"},
        },
        "fit_plans": {
            "de": {
                "plan": [dict(p) for p in _PLAN],
                "fitted_segments": [dict(f) for f in _FITTED],
                "total_duration": track_dur,
                "orig_duration": _ORIG_DUR,
                "params": {"allow_video_retime": True},
                "fit_fp": "test-fp",
            },
        },
    }


# ---------------------------------------------------------------------------
# _video_retime_plan_for — resolution across both keyspaces
# ---------------------------------------------------------------------------

class TestVideoRetimePlanFor:
    def test_legacy_stretch_video_entry_resolves_with_kind(self):
        from api.routers.dub_export import _video_retime_plan_for, _video_stretch_plan_for
        entry = {"plan": [dict(_PLAN[0])], "total_duration": 4.0, "orig_duration": 3.0}
        job = {"timing_strategy": "stretch_video", "video_stretch_plans": {"bn": entry}}
        got = _video_retime_plan_for(job, "bn")
        assert got == ("stretch_video", entry)
        # Resolution must stay byte-identical to the legacy helper.
        assert got[1] is _video_stretch_plan_for(job, "bn")

    def test_smart_fit_entry_resolves_with_kind(self):
        from api.routers.dub_export import _video_retime_plan_for
        job = _smart_fit_job()
        got = _video_retime_plan_for(job, "de")
        assert got is not None
        kind, entry = got
        assert kind == "smart_fit"
        assert entry is job["fit_plans"]["de"]

    def test_neither_keyspace_returns_none(self):
        from api.routers.dub_export import _video_retime_plan_for
        assert _video_retime_plan_for({"timing_strategy": "concise"}, "de") is None
        assert _video_retime_plan_for(_smart_fit_job(), "fr") is None

    def test_legacy_wins_when_job_strategy_is_stretch_video(self):
        from api.routers.dub_export import _video_retime_plan_for
        job = _smart_fit_job()
        job["timing_strategy"] = "stretch_video"
        legacy_entry = {"plan": [dict(_PLAN[0])]}
        job["video_stretch_plans"] = {"de": legacy_entry}
        kind, entry = _video_retime_plan_for(job, "de")
        assert kind == "stretch_video"
        assert entry is legacy_entry

    def test_stale_fit_plan_ignored_after_track_regenerated_concise(self):
        """fit_plans persists across runs; a track re-generated under
        another strategy must not be retimed by the stale plan."""
        from api.routers.dub_export import _video_retime_plan_for
        job = _smart_fit_job()
        job["dubbed_tracks"]["de"]["timing_strategy"] = "concise"
        assert _video_retime_plan_for(job, "de") is None

    def test_empty_plan_returns_none(self):
        from api.routers.dub_export import _video_retime_plan_for
        job = _smart_fit_job()
        job["fit_plans"]["de"]["plan"] = []
        assert _video_retime_plan_for(job, "de") is None


# ---------------------------------------------------------------------------
# Batch partitioning + chunk expansion math
# ---------------------------------------------------------------------------

class TestBatchMath:
    def _chunks(self, n):
        return [(float(i), float(i) + 1.0, 1.0 + 0.01 * i) for i in range(n)]

    def test_empty_chunks_no_batches(self):
        assert partition_batches([]) == []

    def test_exact_multiple_boundary(self):
        batches = partition_batches(self._chunks(80), batch_size=40)
        assert [len(b) for b in batches] == [40, 40]

    def test_remainder_goes_in_final_batch(self):
        batches = partition_batches(self._chunks(81), batch_size=40)
        assert [len(b) for b in batches] == [40, 40, 1]

    def test_order_preserved_across_batches(self):
        chunks = self._chunks(7)
        batches = partition_batches(chunks, batch_size=3)
        flat = [c for b in batches for c in b]
        assert flat == chunks

    def test_invalid_batch_size_raises(self):
        with pytest.raises(ValueError):
            partition_batches(self._chunks(3), batch_size=0)

    def test_default_constants_sane(self):
        # Batches must fit comfortably under the single-pass graph ceiling.
        assert 0 < RETIME_BATCH_SIZE <= RETIME_SINGLE_PASS_MAX_CHUNKS

    def test_expand_emits_preroll_gap_and_tail_at_native_rate(self):
        chunks = expand_retime_chunks(_PLAN, _ORIG_DUR)
        assert chunks == [
            (0.0, 1.0, 1.0),
            (1.0, 3.0, 1.5),
            (3.0, 5.0, 1.0),
            (5.0, 7.0, 1.25),
            (7.0, 10.0, 1.0),
        ]
        assert sum((b - a) * r for a, b, r in chunks) == pytest.approx(_EXPECTED_DUR)


# ---------------------------------------------------------------------------
# Filter graph — parity + new stages
# ---------------------------------------------------------------------------

class TestChunkFilterGraph:
    def test_parity_with_legacy_builder(self):
        from api.routers.dub_export import _build_video_stretch_filter_graph
        legacy_graph, legacy_label = _build_video_stretch_filter_graph(_PLAN, _ORIG_DUR)
        chunks = expand_retime_chunks(_PLAN, _ORIG_DUR)
        graph, label = build_chunk_filter_graph(chunks, "[0:v]")
        assert (graph, label) == (legacy_graph, legacy_label)
        assert label == "[vstretched]"

    def test_fps_norm_prepends_normalisation_stage(self):
        chunks = expand_retime_chunks(_PLAN, _ORIG_DUR)
        graph, _ = build_chunk_filter_graph(chunks, "[0:v]", fps_norm="30000/1001")
        assert graph.startswith("[0:v]fps=30000/1001[vcfr];[vcfr]split=")

    def test_tail_pad_appends_freeze_frame_stage(self):
        chunks = expand_retime_chunks(_PLAN, _ORIG_DUR)
        graph, label = build_chunk_filter_graph(chunks, "[0:v]", tail_pad_s=0.5)
        assert label == "[vstretched]"
        assert graph.endswith(
            "concat=n=5:v=1:a=0[vcat];[vcat]tpad=stop_mode=clone:stop_duration=0.5000[vstretched]"
        )

    def test_no_tail_pad_keeps_legacy_concat_terminal(self):
        chunks = expand_retime_chunks(_PLAN, _ORIG_DUR)
        graph, _ = build_chunk_filter_graph(chunks, "[0:v]")
        assert graph.endswith("concat=n=5:v=1:a=0[vstretched]")
        assert "tpad" not in graph and "fps=" not in graph


# ---------------------------------------------------------------------------
# VFR detection
# ---------------------------------------------------------------------------

class TestVfrDetection:
    def test_matching_rates_are_cfr(self):
        assert is_vfr("30000/1001", "30000/1001") is False
        assert is_vfr("25", "25/1") is False

    def test_mismatched_rates_are_vfr(self):
        assert is_vfr("120/1", "30/1") is True

    def test_unparseable_rates_treated_as_cfr(self):
        assert is_vfr("0/0", "30/1") is False
        assert is_vfr(None, "30/1") is False
        assert is_vfr("garbage", "30/1") is False


# ---------------------------------------------------------------------------
# Burn-in policy + fitted-cue overlay
# ---------------------------------------------------------------------------

class TestBurnPolicyAndFittedCues:
    def test_burn_allowed_for_smart_fit_and_plain(self):
        from api.routers.dub_export import _burn_subs_allowed
        assert _burn_subs_allowed(None) is True
        assert _burn_subs_allowed("smart_fit") is True

    def test_burn_still_rejected_for_legacy_stretch_video(self):
        from api.routers.dub_export import _burn_subs_allowed
        assert _burn_subs_allowed("stretch_video") is False

    def test_apply_fitted_times_matches_by_id(self):
        from api.routers.dub_export import _apply_fitted_times
        job = _smart_fit_job()
        out = _apply_fitted_times(job["segments"], _FITTED)
        assert [(s["start"], s["end"]) for s in out] == [(1.0, 3.75), (6.0, 8.25)]
        # Non-destructive: the originals keep their timings.
        assert job["segments"][0]["end"] == 3.0
        # Text untouched.
        assert out[0]["text"] == "Hello there"

    def test_apply_fitted_times_unmatched_keeps_original(self):
        from api.routers.dub_export import _apply_fitted_times
        segs = [{"id": "zz", "start": 0.0, "end": 1.0, "text": "x"}]
        out = _apply_fitted_times(segs, _FITTED)
        assert (out[0]["start"], out[0]["end"]) == (0.0, 1.0)

    def test_apply_fitted_times_positional_fallback_without_ids(self):
        from api.routers.dub_export import _apply_fitted_times
        segs = [{"start": 1.0, "end": 3.0, "text": "x"}]
        fitted = [{"start": 1.0, "end": 3.8}]
        out = _apply_fitted_times(segs, fitted)
        assert out[0]["end"] == 3.8

    def test_apply_fitted_times_scales_word_times_linearly(self):
        """Karaoke burn-in: persisted word times ride onto the fitted span
        with the same id matching the SRT burn uses."""
        from api.routers.dub_export import _apply_fitted_times
        job = _smart_fit_job()
        job["segments"][0]["words"] = [
            {"text": "Hello", "start": 1.0, "end": 2.0},
            {"text": "there", "start": 2.0, "end": 3.0},
        ]
        out = _apply_fitted_times(job["segments"], _FITTED)
        # [1.0, 3.0] → [1.0, 3.75]: ratio 1.375.
        assert out[0]["words"] == [
            {"text": "Hello", "start": 1.0, "end": 2.375},
            {"text": "there", "start": 2.375, "end": 3.75},
        ]
        # Non-destructive: the source words keep original times.
        assert job["segments"][0]["words"][0]["end"] == 2.0

    def test_apply_fitted_times_drops_words_on_degenerate_span(self):
        from api.routers.dub_export import _apply_fitted_times
        segs = [{"id": "a", "start": 1.0, "end": 1.0, "text": "x",
                 "words": [{"text": "x", "start": 1.0, "end": 1.0}]}]
        out = _apply_fitted_times(segs, _FITTED)
        assert "words" not in out[0]

    def test_write_burn_ass_uses_fitted_word_times(self, tmp_path):
        from api.routers.dub_export import _write_burn_ass
        job = _smart_fit_job()
        job["segments"][0]["words"] = [
            {"text": "Hello", "start": 1.0, "end": 2.0},
            {"text": "there", "start": 2.0, "end": 3.0},
        ]
        path = _write_burn_ass(job, str(tmp_path), "stamp",
                               fitted_segments=_FITTED)
        assert path.endswith("burn_subs_stamp.ass")
        content = open(path, encoding="utf-8").read()
        assert "Dialogue: 0,0:00:01.00,0:00:03.75,Default," in content
        # "Hello" sweeps until "there" starts on the FITTED timeline:
        # 2.375 s rounds to 2.38 s, so "Hello" gets 138 cs and "there" the
        # 137 cs left before the 3.75 s cue end (275 cs in all).
        assert "{\\kf138}Hello {\\kf137}there" in content

    def test_write_burn_srt_uses_fitted_times(self, tmp_path):
        from api.routers.dub_export import _write_burn_srt
        job = _smart_fit_job()
        path = _write_burn_srt(job, str(tmp_path), "stamp", dual=False,
                               fitted_segments=_FITTED)
        content = open(path, encoding="utf-8").read()
        assert "00:00:01,000 --> 00:00:03,750" in content
        assert "00:00:06,000 --> 00:00:08,250" in content
        assert "Hello there" in content

    def test_write_burn_srt_without_fitted_keeps_original_times(self, tmp_path):
        from api.routers.dub_export import _write_burn_srt
        job = _smart_fit_job()
        path = _write_burn_srt(job, str(tmp_path), "stamp", dual=False)
        content = open(path, encoding="utf-8").read()
        assert "00:00:01,000 --> 00:00:03,000" in content


# ---------------------------------------------------------------------------
# SRT / VTT endpoints — fitted-cue selection
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def seeded_smart_fit_job():
    from services.dub_pipeline import _dub_jobs
    job_id = f"sfx_{uuid.uuid4().hex[:8]}"
    job = _smart_fit_job()
    _dub_jobs[job_id] = job
    yield job_id, job
    _dub_jobs.pop(job_id, None)


class TestSubtitleEndpointsFittedCues:
    def test_srt_with_lang_uses_fitted_times(self, client, seeded_smart_fit_job):
        job_id, _ = seeded_smart_fit_job
        res = client.get(f"/dub/srt/{job_id}", params={"lang": "de"})
        assert res.status_code == 200
        assert "00:00:01,000 --> 00:00:03,750" in res.text
        assert "00:00:06,000 --> 00:00:08,250" in res.text

    def test_srt_without_lang_keeps_original_times(self, client, seeded_smart_fit_job):
        job_id, _ = seeded_smart_fit_job
        res = client.get(f"/dub/srt/{job_id}")
        assert res.status_code == 200
        assert "00:00:01,000 --> 00:00:03,000" in res.text
        assert "3,750" not in res.text

    def test_srt_lang_param_inert_without_fit_plan(self, client, seeded_smart_fit_job):
        job_id, job = seeded_smart_fit_job
        job.pop("fit_plans")
        res = client.get(f"/dub/srt/{job_id}", params={"lang": "de"})
        assert res.status_code == 200
        assert "00:00:01,000 --> 00:00:03,000" in res.text

    def test_srt_lang_param_inert_for_non_smart_fit_track(self, client, seeded_smart_fit_job):
        job_id, job = seeded_smart_fit_job
        job["dubbed_tracks"]["de"]["timing_strategy"] = "concise"
        res = client.get(f"/dub/srt/{job_id}", params={"lang": "de"})
        assert res.status_code == 200
        assert "00:00:01,000 --> 00:00:03,000" in res.text

    def test_vtt_with_lang_uses_fitted_times(self, client, seeded_smart_fit_job):
        job_id, _ = seeded_smart_fit_job
        res = client.get(f"/dub/vtt/{job_id}", params={"lang": "de"})
        assert res.status_code == 200
        assert res.text.startswith("WEBVTT")
        assert "00:00:01.000 --> 00:00:03.750" in res.text

    def test_vtt_without_lang_unchanged(self, client, seeded_smart_fit_job):
        job_id, _ = seeded_smart_fit_job
        res = client.get(f"/dub/vtt/{job_id}")
        assert res.status_code == 200
        assert "00:00:01.000 --> 00:00:03.000" in res.text


# ---------------------------------------------------------------------------
# Integration — real ffmpeg renders (skipped when ffmpeg/ffprobe missing)
# ---------------------------------------------------------------------------

def _make_test_video(path: Path, duration: float = 10.0) -> None:
    subprocess.run(
        [_FFMPEG, "-y", "-f", "lavfi",
         "-i", f"testsrc=duration={duration}:size=160x120:rate=30",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(path)],
        check=True, capture_output=True, timeout=120,
    )


def _make_sine_wav(path: Path, duration: float) -> None:
    subprocess.run(
        [_FFMPEG, "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={duration}",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True, timeout=120,
    )


def _probe_stream_durations(path: str) -> dict:
    out = subprocess.run(
        [_FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,duration",
         "-of", "json", path],
        check=True, capture_output=True, text=True, timeout=60,
    )
    durs: dict[str, float] = {}
    for s in json.loads(out.stdout).get("streams", []):
        try:
            durs[s["codec_type"]] = float(s.get("duration") or 0.0)
        except (TypeError, ValueError):
            pass
    return durs


@pytest.fixture(scope="module")
def test_video(tmp_path_factory):
    if not (_FFMPEG and _FFPROBE):
        pytest.skip("ffmpeg/ffprobe not available")
    path = tmp_path_factory.mktemp("retime_media") / "source.mp4"
    _make_test_video(path)
    return path


@needs_ffmpeg
class TestRetimeExecutorIntegration:
    @pytest.fixture(autouse=True)
    def _sandbox_dub_dir(self, tmp_path, monkeypatch):
        """The retime entry points containment-check work/out paths against
        DUB_DIR (path-injection hardening) — point it at the test sandbox.
        The guard resolves DUB_DIR live from sys.modules at call time, so
        patching the canonical core.config module is sufficient and survives
        the full-suite reload ordering."""
        import core.config as _cfg
        monkeypatch.setattr(_cfg, "DUB_DIR", str(tmp_path))

    def test_single_pass_decision_renders_to_expected_duration(self, test_video, tmp_path):
        decision = asyncio.run(prepare_smart_fit_video(
            job_id=None, ffmpeg=_FFMPEG, video_path=str(test_video),
            plan=_PLAN, orig_dur=_ORIG_DUR, track_dur=_EXPECTED_DUR,
            work_path=str(tmp_path / "retimed.mp4"),
        ))
        assert decision is not None and decision.mode == "filter"
        assert decision.video_dur == pytest.approx(_EXPECTED_DUR, abs=0.05)
        out = tmp_path / "single_pass.mp4"
        subprocess.run(
            [_FFMPEG, "-y", "-i", str(test_video),
             "-filter_complex", decision.graph, "-map", decision.label, "-an",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(out)],
            check=True, capture_output=True, timeout=300,
        )
        durs = _probe_stream_durations(str(out))
        assert durs["video"] == pytest.approx(_EXPECTED_DUR, abs=0.05)

    def test_batched_path_with_batch_size_two(self, test_video, tmp_path):
        out_path = tmp_path / "retimed_batched.mp4"
        decision = asyncio.run(prepare_smart_fit_video(
            job_id=None, ffmpeg=_FFMPEG, video_path=str(test_video),
            plan=_PLAN, orig_dur=_ORIG_DUR, track_dur=_EXPECTED_DUR,
            work_path=str(out_path),
            single_pass_max=1, batch_size=2,  # force the batched tier
        ))
        assert decision is not None and decision.mode == "file"
        assert os.path.exists(decision.file_path)
        durs = _probe_stream_durations(decision.file_path)
        assert durs["video"] == pytest.approx(_EXPECTED_DUR, abs=0.05)
        # Temp slices cleaned on success.
        assert not os.path.exists(str(out_path) + ".slices")

    def test_batched_tail_pad_branch_freezes_last_frame(self, test_video, tmp_path):
        """Track outruns the retimed video → tpad extends the final slice."""
        out_path = tmp_path / "retimed_tpad.mp4"
        track_dur = _EXPECTED_DUR + 0.6
        decision = asyncio.run(prepare_smart_fit_video(
            job_id=None, ffmpeg=_FFMPEG, video_path=str(test_video),
            plan=_PLAN, orig_dur=_ORIG_DUR, track_dur=track_dur,
            work_path=str(out_path),
            single_pass_max=1, batch_size=2,
        ))
        assert decision is not None and decision.mode == "file"
        durs = _probe_stream_durations(decision.file_path)
        assert durs["video"] == pytest.approx(track_dur, abs=0.05)

    def test_render_cleans_slices_on_failure(self, tmp_path):
        out_path = tmp_path / "broken.mp4"
        chunks = expand_retime_chunks(_PLAN, _ORIG_DUR)
        with pytest.raises(RetimeError):
            asyncio.run(render_retimed_video(
                job_id=None, ffmpeg=_FFMPEG,
                video_path=str(tmp_path / "missing_input.mp4"),
                chunks=chunks, out_path=str(out_path), batch_size=2,
            ))
        assert not os.path.exists(str(out_path) + ".slices")
        assert not os.path.exists(out_path)


@pytest.fixture
def export_app(tmp_path, monkeypatch):
    """App with OMNIVOICE_DATA_DIR sandboxed so /dub/download writes under tmp."""
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    import core.config as _cfg
    importlib.reload(_cfg)
    import core.tasks as _tasks
    importlib.reload(_tasks)
    from api.routers import dub_core as _dc
    importlib.reload(_dc)
    from api.routers import dub_export as _dx
    importlib.reload(_dx)
    import main as _main
    importlib.reload(_main)
    from fastapi.testclient import TestClient
    with TestClient(_main.app) as c:
        yield c, _dc, tmp_path


def _seed_retime_job(dc, tmp_path: Path, video: Path, track_dur: float) -> str:
    job_id = f"sfe_{uuid.uuid4().hex[:8]}"
    job_dir = tmp_path / "dub_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    track_wav = job_dir / "dubbed_de.wav"
    _make_sine_wav(track_wav, track_dur)
    local_video = job_dir / "source.mp4"
    local_video.write_bytes(video.read_bytes())
    job = _smart_fit_job(track_dur=track_dur)
    job["video_path"] = str(local_video)
    job["dubbed_tracks"]["de"]["path"] = str(track_wav)
    dc._dub_jobs[job_id] = job
    return job_id


_SRT_CUE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def _last_cue_end_s(srt_text: str) -> float:
    ends = []
    for m in _SRT_CUE.finditer(srt_text):
        h, mnt, s, ms = (int(m.group(i)) for i in (5, 6, 7, 8))
        ends.append(h * 3600 + mnt * 60 + s + ms / 1000.0)
    assert ends, "no cues parsed"
    return max(ends)


@needs_ffmpeg
class TestDownloadEndpointIntegration:
    def test_export_apad_branch_video_longer_than_track(self, export_app, test_video):
        """Track shorter than the retimed video → audio silence-padded."""
        client, dc, tmp_path = export_app
        track_dur = _EXPECTED_DUR - 0.5  # 11.0 < 11.5 expected video
        job_id = _seed_retime_job(dc, tmp_path, test_video, track_dur)
        res = client.get(
            f"/dub/download/{job_id}",
            params={"default_track": "de", "include_tracks": "de", "preserve_bg": 0},
        )
        assert res.status_code == 200, res.text[:500]
        exports = list((tmp_path / "dub_jobs" / job_id / "exports").glob("dubbed_video_*.mp4"))
        assert len(exports) == 1
        durs = _probe_stream_durations(str(exports[0]))
        assert durs["video"] == pytest.approx(_EXPECTED_DUR, abs=0.05)
        # apad stretched the audio out to (at least) the video length.
        assert durs["audio"] >= durs["video"] - 0.05

    def test_export_tpad_branch_track_longer_than_video(self, export_app, test_video):
        """Track outruns the retimed video → freeze-frame tail on the video."""
        client, dc, tmp_path = export_app
        track_dur = _EXPECTED_DUR + 0.5  # 12.0 > 11.5 expected video
        job_id = _seed_retime_job(dc, tmp_path, test_video, track_dur)
        res = client.get(
            f"/dub/download/{job_id}",
            params={"default_track": "de", "include_tracks": "de", "preserve_bg": 0},
        )
        assert res.status_code == 200, res.text[:500]
        exports = list((tmp_path / "dub_jobs" / job_id / "exports").glob("dubbed_video_*.mp4"))
        assert len(exports) == 1
        durs = _probe_stream_durations(str(exports[0]))
        assert durs["video"] == pytest.approx(track_dur, abs=0.05)
        # No retime intermediates left behind.
        leftovers = list((tmp_path / "dub_jobs" / job_id / "exports").glob("retimed_*"))
        assert leftovers == []

    def test_fitted_srt_last_cue_within_video(self, export_app, test_video):
        client, dc, tmp_path = export_app
        job_id = _seed_retime_job(dc, tmp_path, test_video, _EXPECTED_DUR)
        res = client.get(f"/dub/srt/{job_id}", params={"lang": "de"})
        assert res.status_code == 200
        assert _last_cue_end_s(res.text) <= _EXPECTED_DUR + 0.05
