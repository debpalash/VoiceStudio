"""#34 runtime-verify — Layer 1 e2e for the longform chapterized renderer.

Drives the REAL `_render_longform_sse` generator and REAL ffmpeg, but injects a
stub synth (a CPU tone) so no GPU/model is needed. Proves the SSE event
sequence AND that ffmpeg actually muxes a playable, chapter-tagged file — the
cheap regression net over the audiobook/stories convergence (#21-era work).

Gated on ffmpeg being present so it's a no-op on a runner without it; it MUST
run in CI (where ffmpeg is installed). Exercises the non-happy states too:
empty plan, no-ffmpeg, per-chapter partial failure, total failure.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess

import pytest
import torch

from services.ffmpeg_utils import find_ffmpeg

pytestmark = pytest.mark.skipif(
    find_ffmpeg() is None, reason="ffmpeg required for the longform e2e",
)

_FFPROBE = shutil.which("ffprobe")


# ── stubs + drivers ─────────────────────────────────────────────────────────

def _resolve(_voice_id):
    # The four keys the chapter-signature build reads (audiobook.py).
    return {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}


def _stub_build_synth(*, fail_on=None, exc=None):
    """Return a drop-in for `audiobook._build_synth` whose `synth` emits 0.1s of
    silence per span. `fail_on(text)` → raise, to exercise per-chapter faults;
    `exc` overrides what is raised (an engine can fail with an exception whose
    ``str()`` is empty — see the StopIteration case below)."""
    def _factory(default_voice=None, language=None, opts=None, voice_map=None):
        def synth(text, voice_id, speed=None):
            if fail_on is not None and fail_on(text):
                raise exc if exc is not None else RuntimeError("stub synth deliberately failed")
            return torch.zeros(2400)  # 0.1s @ 24k, 1-D float32
        return {"mode": "generic", "resolve": _resolve, "engine_id": "stub",
                "synth": synth, "sample_rate": 24000}
    return _factory


def _plan(*chapters):
    """chapters: (title, body) pairs → AudiobookPlan."""
    from services.audiobook import AudiobookPlan, Chapter, Span
    return AudiobookPlan(chapters=[
        Chapter(title=title, spans=[Span(voice_id=None, text=body)])
        for title, body in chapters
    ])


def _collect_events(plan, monkeypatch, outputs_dir, *, fail_on=None, exc=None,
                    timeout=120, **kw):
    """Patch the synth + OUTPUTS_DIR, drive the real generator, return parsed
    SSE events (list of dicts).

    Bounded on purpose. One of the failures this file covers — a bare
    StopIteration from an engine, #1321 — does not raise, it WEDGES: asyncio
    cannot put StopIteration into a Future, so the awaiting `run_in_executor`
    never completes. Unbounded, a regression in that guard would stall CI until
    the job timeout instead of failing the test. The bound is generous (real
    ffmpeg muxes run under here) — it is a deadlock detector, not a perf budget.
    """
    from api.routers import audiobook
    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth(fail_on=fail_on, exc=exc))
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(outputs_dir))

    async def _run():
        out = []
        async for frame in audiobook._render_longform_sse(plan, default_voice=None, **kw):
            assert frame.startswith("data:") and frame.endswith("\n\n")
            out.append(json.loads(frame[len("data:"):].strip()))
        return out

    return asyncio.run(asyncio.wait_for(_run(), timeout=timeout))


def _ffprobe(path):
    assert _FFPROBE, "ffprobe not found"
    out = subprocess.run(
        [_FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", "-show_chapters", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


# ── happy paths ─────────────────────────────────────────────────────────────

def test_m4b_two_chapters_muxes_and_emits_full_sequence(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(
        _plan(("One", "Hello world."), ("Two", "Second chapter.")),
        monkeypatch, out, fmt="m4b",
    )
    types = [e["type"] for e in events]
    assert types == ["started", "chapter", "chapter", "assembling", "done"]
    done = events[-1]
    assert done["chapters"] == 2 and done["failed_chapters"] == []

    out_path = out / done["output"]
    assert out_path.exists()
    if _FFPROBE:
        probe = _ffprobe(str(out_path))
        assert "mp4" in probe["format"]["format_name"]
        assert len(probe.get("chapters", [])) == 2  # both chapters tagged


def test_mp3_format_produces_mp3_container(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(_plan(("One", "Hi.")), monkeypatch, out, fmt="mp3")
    done = events[-1]
    assert done["type"] == "done"
    out_path = out / done["output"]
    assert out_path.suffix == ".mp3" and out_path.exists()
    if _FFPROBE:
        assert "mp3" in _ffprobe(str(out_path))["format"]["format_name"]


# ── partial / total failure ─────────────────────────────────────────────────

def test_partial_failure_isolates_bad_chapter(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    # Fail only the first chapter's text.
    events = _collect_events(
        _plan(("Bad", "FAILME please"), ("Good", "this one is fine")),
        monkeypatch, out, fmt="m4b", fail_on=lambda t: "FAILME" in t,
    )
    types = [e["type"] for e in events]
    assert "chapter_error" in types and "done" in types
    err = next(e for e in events if e["type"] == "chapter_error")
    assert err["index"] == 0
    # #1321: the event has to name the cause. It used to say only "chapter
    # failed to render", so the reason existed nowhere but the backend log and
    # a user could not tell a bad voice from an engine that can't read their
    # script. `error` keeps mirroring `reason` for older frontends.
    assert "stub synth deliberately failed" in err["reason"]
    assert err["error"] == err["reason"]
    assert err["error_class"] == "RuntimeError"
    assert err["stage"] == "audiobook_chapter"
    # Per-chapter events carry no env diagnostic — it is identical for every
    # chapter and a long book emits hundreds of these.
    assert "diagnostic" not in err
    done = events[-1]
    assert done["chapters"] == 1 and done["failed_chapters"] == [0]
    assert (out / done["output"]).exists()  # the surviving chapter still muxed


def test_chapter_duration_ms_reproduces_the_embedded_chapter_starts(tmp_path, monkeypatch):
    """The cue sheet (#2278) sums `duration_ms` over the chapters that rendered.
    That sum must land on the m4b's own chapter START offsets, with a failed
    chapter contributing neither a chapter nor any time."""
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(
        _plan(("One", "Hello there."), ("Bad", "FAILME now"), ("Three", "Third one."),
              ("Four", "Fourth one.")),
        monkeypatch, out, fmt="m4b", fail_on=lambda t: "FAILME" in t,
    )
    chapters = [e for e in events if e["type"] == "chapter"]
    assert [c["title"] for c in chapters] == ["One", "Three", "Four"]
    for c in chapters:
        assert isinstance(c["duration_ms"], int)
        assert c["duration_ms"] == pytest.approx(c["duration_s"] * 1000, abs=5)
    starts, elapsed = [], 0
    for c in chapters:
        starts.append(elapsed)
        elapsed += c["duration_ms"]
    done = events[-1]
    assert done["type"] == "done"
    assert done["duration_s"] == pytest.approx(elapsed / 1000, abs=0.005)
    if _FFPROBE:
        embedded = _ffprobe(str(out / done["output"]))["chapters"]
        assert [ch["tags"]["title"] for ch in embedded] == ["One", "Three", "Four"]
        assert [round(float(ch["start_time"]) * 1000) for ch in embedded] == starts


def test_all_chapters_fail_emits_error_and_no_file(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(
        _plan(("A", "x"), ("B", "y")), monkeypatch, out,
        fmt="m4b", fail_on=lambda t: True,
    )
    err = events[-1]
    assert err["type"] == "error"
    # Summary first, then the cause (#1321) — the old event was the summary
    # alone, which is the symptom the user already knew.
    assert err["reason"].startswith("all 2 chapters failed to render — ")
    assert "stub synth deliberately failed" in err["reason"]
    assert err["error"] == err["reason"]
    # Terminal event, so it does carry the env diagnostic the bug reporter reads.
    assert err["diagnostic"]
    # No output file was produced.
    assert not any(p.suffix in (".m4b", ".mp3") for p in out.iterdir())


def test_all_chapters_fail_marks_the_job_failed(tmp_path, monkeypatch):
    """The all-failed branch used to return without touching job history, so the
    row stayed `running`: the next startup's orphan sweep read it as an
    interrupted job, and the retained resume manifest offered an already-hopeless
    render as resumable (Greptile P1 on #1321).

    Spies on job_store rather than reading the DB — this module never runs the
    schema migration, so the real calls are swallowed by the renderer's
    best-effort guard and nothing would be observable."""
    from core import job_store

    failed_calls = []
    monkeypatch.setattr(job_store, "create", lambda *a, **k: None)
    monkeypatch.setattr(job_store, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(job_store, "mark_done", lambda *a, **k: None)
    monkeypatch.setattr(
        job_store, "mark_failed", lambda jid, err: failed_calls.append((jid, err))
    )

    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(
        _plan(("A", "x"), ("B", "y")), monkeypatch, out,
        fmt="m4b", fail_on=lambda t: True,
    )

    job_id = next(e for e in events if e["type"] == "started")["job_id"]
    assert len(failed_calls) == 1, failed_calls
    assert failed_calls[0][0] == job_id
    # Records WHY, not just that it failed — this is what job history shows.
    assert "stub synth deliberately failed" in failed_calls[0][1]


def test_a_successful_render_is_not_marked_failed(tmp_path, monkeypatch):
    """Guard the other side of the branch: the new mark_failed must not fire on
    a render that produced output."""
    from core import job_store

    failed_calls = []
    monkeypatch.setattr(job_store, "create", lambda *a, **k: None)
    monkeypatch.setattr(job_store, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(job_store, "mark_done", lambda *a, **k: None)
    monkeypatch.setattr(
        job_store, "mark_failed", lambda jid, err: failed_calls.append((jid, err))
    )

    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(_plan(("One", "Hi.")), monkeypatch, out, fmt="m4b")
    assert events[-1]["type"] == "done"
    assert failed_calls == []


def test_bare_stopiteration_from_an_engine_is_reported_not_hung(tmp_path, monkeypatch):
    """#1321: a generator-based engine (VoxCPM's ``next_and_close`` is a bare
    ``next(gen)``) raises ``StopIteration`` when its generator ends without
    yielding. asyncio cannot put that into a Future, so before the pool guard
    this call never returned at all — the render stopped emitting and waited
    forever. It must come back as an ordinary chapter failure with a reason.

    Without the guard in model_manager this test HANGS rather than fails, which
    is exactly the user-visible symptom."""
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(
        _plan(("A", "x")), monkeypatch, out,
        fmt="m4b", fail_on=lambda t: True, exc=StopIteration(),
    )
    chapter_err = next(e for e in events if e["type"] == "chapter_error")
    assert chapter_err["error_class"] == "WorkerStopIteration"
    assert "StopIteration" in chapter_err["reason"]
    assert chapter_err["error"] == chapter_err["reason"]
    assert events[-1]["reason"].startswith("all 1 chapters failed to render — ")


def test_chapter_error_names_an_exception_with_no_message(tmp_path, monkeypatch):
    """An exception whose ``str()`` is empty must still produce a reason — any
    emit site that formats ``str(e)`` would tell the user nothing at all. The
    class name is the floor (#1252/#1253 class, re-asserted here for the
    longform path)."""
    class SilentEngineError(Exception):
        pass

    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(
        _plan(("A", "x")), monkeypatch, out,
        fmt="m4b", fail_on=lambda t: True, exc=SilentEngineError(),
    )
    chapter_err = next(e for e in events if e["type"] == "chapter_error")
    assert chapter_err["reason"] == "SilentEngineError"
    assert chapter_err["error"] == "SilentEngineError"
    assert events[-1]["reason"] == "all 1 chapters failed to render — SilentEngineError"


# ── degenerate / environment ────────────────────────────────────────────────

def test_empty_plan_errors_cleanly(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(_plan(), monkeypatch, out)
    assert events == [{"type": "error", "error": "nothing to render (no chapters)"}]


def test_no_ffmpeg_errors_before_synth(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    # Force the no-ffmpeg branch even though the suite is gated on ffmpeg present.
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: None)
    events = _collect_events(_plan(("One", "hi")), monkeypatch, out)
    assert events[-1]["type"] == "error" and "ffmpeg" in events[-1]["error"]
    assert not list(p for p in out.iterdir() if p.is_file())


def test_acx_emits_mastering_event_and_loudness_block(tmp_path, monkeypatch):
    """#28: a known loudness preset fires a `mastering` event and adds a
    `loudness` block to `done` (two-pass on real signal; silent stub → fallback,
    two_pass False — either way the wiring + event shape are exercised)."""
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(_plan(("One", "hi")), monkeypatch, out, fmt="m4b", loudness="acx")
    types = [e["type"] for e in events]
    assert "mastering" in types
    done = events[-1]
    assert done["type"] == "done"
    assert done["loudness"]["preset"] == "acx"
    assert "two_pass" in done["loudness"]


def test_off_path_emits_no_loudness_block(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(_plan(("One", "hi")), monkeypatch, out, fmt="m4b")  # no loudness
    assert "mastering" not in [e["type"] for e in events]
    assert "loudness" not in events[-1]  # legacy done shape preserved


# ── the finished render says what it is ─────────────────────────────────────

def test_done_event_carries_the_title_and_how_it_was_rendered(tmp_path, monkeypatch):
    from services.audiobook import AudiobookPlan, Chapter, ExpressiveOptions, Span

    out = tmp_path / "outputs"
    out.mkdir()
    plan = AudiobookPlan(chapters=[Chapter(title="Chapter One", spans=[
        Span(voice_id=None, text="Zoe raced along the path.", speed=0.95)])])
    events = _collect_events(plan, monkeypatch, out, fmt="mp3", language="English",
                             metadata={"title": "The Super Sloth"},
                             opts=ExpressiveOptions(seed=0, postprocess_output=False))
    done = events[-1]
    assert done["type"] == "done" and done["title"] == "The Super Sloth"
    summary = done["summary"]
    assert summary["speeds"] == [0.95] and summary["lines"] == 1 and summary["words"] == 5
    assert summary["language"] == "English" and summary["format"] == "mp3"
    # Explicit falsy settings and effective sampling values are recorded.
    assert summary["options"]["seed"] == 0
    assert summary["options"]["postprocess_output"] is False
    assert summary["options"]["num_step"] > 0
    assert summary["options"]["guidance_scale"] == 2.0
    assert summary["chapter_titles"] == ["Chapter One"]
    assert "Zoe" not in json.dumps(summary)      # settings and counts, never the script


def test_a_summary_failure_never_costs_the_render(tmp_path, monkeypatch):
    from api.routers import audiobook

    def boom(*_a, **_k):
        raise RuntimeError("profile db unavailable")

    monkeypatch.setattr(audiobook, "_render_summary", boom)
    out = tmp_path / "outputs"
    out.mkdir()
    done = _collect_events(_plan(("One", "Hi.")), monkeypatch, out, fmt="mp3")[-1]
    assert done["type"] == "done" and "summary" not in done
    assert (out / done["output"]).exists()


def test_summary_describes_only_the_chapters_that_made_it_into_the_file(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(
        _plan(("Good", "Hello world."), ("Bad", "BOOM please fail here.")),
        monkeypatch, out, fmt="mp3", fail_on=lambda text: "BOOM" in text)
    done = events[-1]
    assert done["type"] == "done" and done["failed_chapters"]
    assert done["summary"]["chapter_titles"] == ["Good"]
    assert done["summary"]["words"] == 2


def test_done_summary_from_old_manifest_is_strict_json(tmp_path, monkeypatch):
    from services.audiobook import ExpressiveOptions
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(_plan(("One", "hello")), monkeypatch, out, fmt="mp3",
                             opts=ExpressiveOptions(emo_vector=(float("nan"),) * 8))
    assert events[-1]["type"] == "done"
    json.dumps(events[-1], allow_nan=False)
