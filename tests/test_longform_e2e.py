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
import threading
from pathlib import Path

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
    """Return a drop-in for `audiobook._build_synth` whose `synth` emits a 0.1s
    tone per span. `fail_on(text)` → raise, to exercise per-chapter faults;
    `exc` overrides what is raised (an engine can fail with an exception whose
    ``str()`` is empty — see the StopIteration case below)."""
    def _factory(default_voice=None, language=None, opts=None, voice_map=None, lease=None):
        def synth(text, voice_id, speed=None):
            if fail_on is not None and fail_on(text):
                raise exc if exc is not None else RuntimeError("stub synth deliberately failed")
            return 0.1 * torch.sin(torch.arange(2400) * (2 * torch.pi * 440 / 24000))
        return {"mode": "generic", "resolve": _resolve, "engine_id": "stub",
                "synth": synth, "sample_rate": 24000}
    return _factory


async def _until_event(stream, kind):
    """Advance an SSE render to its next ``kind`` frame. Per-chunk
    ``progress`` frames (#3417) may come first."""
    while True:
        frame = await anext(stream)
        if f'"type": "{kind}"' in frame:
            return frame
        assert '"type": "progress"' in frame, frame


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

def test_pruning_during_atomic_cache_publication_preserves_the_write(tmp_path, monkeypatch):
    from services.longform_render import LONGFORM_CACHE_SUBDIR, prune_cache_dir

    out = tmp_path / "outputs"
    cache_dir = out / LONGFORM_CACHE_SUBDIR
    real_replace = os.replace
    published = []

    def prune_before_replace(source, target):
        target = Path(target)
        if target.suffix == ".wav" and target.is_relative_to(cache_dir):
            prune_cache_dir(str(cache_dir), max_bytes=0)
            assert Path(source).is_file(), "pruning removed an active atomic write"
            published.append(target)
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", prune_before_replace)
    events = _collect_events(_plan(("One", "Hello world.")), monkeypatch, out)

    assert events[-1]["type"] == "done"
    assert any(path.parent == cache_dir for path in published)
    assert any(path.parent == cache_dir / "segments" for path in published)


def test_m4b_two_chapters_muxes_and_emits_full_sequence(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(
        _plan(("One", "Hello world."), ("Two", "Second chapter.")),
        monkeypatch, out, fmt="m4b",
    )
    # Live `progress` events (#3417) interleave; the outcome sequence is fixed.
    types = [e["type"] for e in events if e["type"] != "progress"]
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


def test_resume_larger_than_cache_budget_reuses_finished_chapters(tmp_path, monkeypatch):
    from api.routers import audiobook
    from services.longform_render import prune_cache_dir

    monkeypatch.setattr(audiobook, "prune_cache_dir",
                        lambda path: prune_cache_dir(path, max_bytes=0))
    polls = 0

    async def disconnect():
        nonlocal polls
        polls += 1
        return polls > 2

    events = _collect_events(
        _plan(("One", "First."), ("Two", "Second."), ("Three", "Third.")),
        monkeypatch, tmp_path, is_disconnected=disconnect,
    )
    assert events[-1]["type"] == "stopped"
    job_id = events[0]["job_id"]
    # A restart days later must not make completed chapters expendable.
    for path in tmp_path.rglob("*.wav"):
        os.utime(path, (1, 1))

    async def resume():
        response = await audiobook.resume_longform(job_id)
        return [json.loads(frame.removeprefix("data:"))
                async for frame in response.body_iterator]

    resumed = asyncio.run(asyncio.wait_for(resume(), timeout=120))
    assert resumed[-1]["type"] == "done", resumed
    assert [e["cached"] for e in resumed if e["type"] == "chapter"] == [True, True, False]
    assert (tmp_path / resumed[-1]["output"]).is_file()


def test_overlapping_render_cannot_evict_another_jobs_mux_inputs(tmp_path, monkeypatch):
    from api.routers import audiobook
    from services.longform_render import prune_cache_dir

    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth())
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(tmp_path))
    monkeypatch.setattr(audiobook, "prune_cache_dir",
                        lambda path: prune_cache_dir(path, max_bytes=0))

    async def run():
        first = audiobook._render_longform_sse(_plan(("One", "First book.")), default_voice=None)
        try:
            assert '"type": "started"' in await anext(first)
            await _until_event(first, "chapter")
            second = [json.loads(frame.removeprefix("data:")) async for frame in
                      audiobook._render_longform_sse(_plan(("Two", "Second book.")), default_voice=None)]
            assert second[-1]["type"] == "done", second
            return [json.loads(frame.removeprefix("data:")) async for frame in first]
        finally:
            await first.aclose()

    remaining = asyncio.run(asyncio.wait_for(run(), timeout=120))
    assert remaining[-1]["type"] == "done", remaining
    assert (tmp_path / remaining[-1]["output"]).is_file()
    prune_cache_dir(str(tmp_path / "longform_cache"), max_bytes=0)
    assert not list((tmp_path / "longform_cache").rglob("*.wav"))


@pytest.mark.parametrize("overlap_at", ["started", "preparing", "chapter"])
@pytest.mark.parametrize("reuse", ["chapters", "segments"])
def test_resume_reserves_later_inputs_before_overlapping_prune(
    tmp_path, monkeypatch, overlap_at, reuse,
):
    from api.routers import audiobook
    from services.longform_render import LONGFORM_CACHE_SUBDIR, prune_cache_dir

    monkeypatch.setattr(audiobook, "prune_cache_dir",
                        lambda path: prune_cache_dir(path, max_bytes=0))
    polls = 0

    async def disconnect():
        nonlocal polls
        polls += 1
        return polls > 2

    plan = _plan(("One", "First."), ("Two", "Second."), ("Three", "Third."))
    seeded = _collect_events(plan, monkeypatch, tmp_path, is_disconnected=disconnect)
    assert seeded[-1]["type"] == "stopped"
    cache = tmp_path / LONGFORM_CACHE_SUBDIR
    if reuse == "segments":
        for path in cache.glob("*.wav"):
            path.unlink()
    unrelated = cache / "unrelated.wav"
    unrelated.write_bytes(b"old unrelated audio")
    for path in cache.rglob("*.wav"):
        os.utime(path, (1, 1))

    async def other_render():
        events = [json.loads(frame.removeprefix("data:")) async for frame in
                  audiobook._render_longform_sse(
                      _plan(("Other", "A different book.")), default_voice=None)]
        assert events[-1]["type"] == "done", events

    prepare = audiobook._prepare_synth
    overlapped = False

    async def prepare_with_overlap(*args, **kwargs):
        nonlocal overlapped
        if overlap_at == "preparing" and not overlapped:
            overlapped = True
            await other_render()
        return await prepare(*args, **kwargs)

    monkeypatch.setattr(audiobook, "_prepare_synth", prepare_with_overlap)

    async def resume():
        response = await audiobook.resume_longform(seeded[0]["job_id"])
        events = []
        async for frame in response.body_iterator:
            event = json.loads(frame.removeprefix("data:"))
            events.append(event)
            if event["type"] == overlap_at and (
                overlap_at != "chapter" or event["index"] == 0
            ):
                await other_render()
            if event["type"] == "chapter" and event["index"] == 0:
                prune_cache_dir(str(cache), max_bytes=0)
                assert not unrelated.exists(), "startup pins outlived plan reservation"
        return events

    resumed = asyncio.run(asyncio.wait_for(resume(), timeout=120))
    assert resumed[-1]["type"] == "done", resumed
    chapters = [event for event in resumed if event["type"] == "chapter"]
    if reuse == "chapters":
        assert [chapter["cached"] for chapter in chapters] == [True, True, False]
    else:
        assert [chapter["cached_segments"] for chapter in chapters] == [1, 1, 0]
    assert (tmp_path / resumed[-1]["output"]).is_file()
    prune_cache_dir(str(cache), max_bytes=0)
    assert not list(cache.rglob("*.wav")), "a finished render leaked pins"


def test_closing_render_keeps_progress_without_leaking_cache_pins(tmp_path, monkeypatch):
    from api.routers import audiobook
    from services.longform_render import LONGFORM_CACHE_SUBDIR, prune_cache_dir

    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth())
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(tmp_path))
    monkeypatch.setattr(audiobook, "prune_cache_dir",
                        lambda path: prune_cache_dir(path, max_bytes=0))

    async def run():
        stream = audiobook._render_longform_sse(
            _plan(("One", "First."), ("Two", "Second.")), default_voice=None,
        )
        try:
            assert '"type": "started"' in await anext(stream)
            await _until_event(stream, "chapter")
        finally:
            await stream.aclose()

    asyncio.run(asyncio.wait_for(run(), timeout=120))
    cache = tmp_path / LONGFORM_CACHE_SUBDIR
    assert list(cache.glob("*.wav"))
    assert list((cache / "segments").glob("*.wav"))
    prune_cache_dir(str(cache), max_bytes=0)
    assert not list(cache.rglob("*.wav"))


def test_closing_before_preparation_keeps_existing_progress_without_leaking_pins(
    tmp_path, monkeypatch,
):
    from api.routers import audiobook
    from services.longform_render import LONGFORM_CACHE_SUBDIR, prune_cache_dir

    plan = _plan(("One", "First."), ("Two", "Second."))
    assert _collect_events(plan, monkeypatch, tmp_path)[-1]["type"] == "done"
    cache = tmp_path / LONGFORM_CACHE_SUBDIR
    existing = set(cache.rglob("*.wav"))
    monkeypatch.setattr(audiobook, "prune_cache_dir",
                        lambda path: prune_cache_dir(path, max_bytes=0))

    async def close_before_preparation():
        stream = audiobook._render_longform_sse(plan, default_voice=None)
        try:
            assert '"type": "started"' in await anext(stream)
            prune_cache_dir(str(cache), max_bytes=0)
            assert all(path.exists() for path in existing)
        finally:
            await stream.aclose()

    asyncio.run(close_before_preparation())
    assert all(path.exists() for path in existing)
    prune_cache_dir(str(cache), max_bytes=0)
    assert not list(cache.rglob("*.wav"))


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_cache_reservation_keeps_event_loop_responsive(tmp_path, monkeypatch, cancel):
    from api.routers import audiobook
    from services.longform_render import LONGFORM_CACHE_SUBDIR, prune_cache_dir

    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth())
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(tmp_path))
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    responsive = []
    original_inputs = audiobook._chapter_cache_inputs

    def slow_inputs(*args, **kwargs):
        if responsive:
            return original_inputs(*args, **kwargs)
        loop.call_soon_threadsafe(started.set)
        responsive.append(release.wait(5))
        try:
            return original_inputs(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(audiobook, "_chapter_cache_inputs", slow_inputs)

    async def render():
        return [frame async for frame in audiobook._render_longform_sse(
            _plan(("One", "First.")), default_voice=None,
        )]

    task = asyncio.create_task(render())
    try:
        await asyncio.wait_for(started.wait(), timeout=15)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 10)
        if not cancel:
            frames = await asyncio.wait_for(task, timeout=120)
            assert '"type": "done"' in frames[-1]
        assert responsive == [True], "cache reservation blocked the event loop"
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(finished.wait, 10)

    # A worker finishing after cancellation must not reacquire released pins.
    cache = tmp_path / LONGFORM_CACHE_SUBDIR
    prune_cache_dir(str(cache), max_bytes=0)
    assert not list(cache.rglob("*.wav"))
    from services import longform_render
    assert not longform_render._cache_pins


def test_failed_mux_keeps_checkpoint_and_reports_failure(tmp_path, monkeypatch):
    from api.routers import audiobook
    from core import db, job_store
    from services import longform_resume
    from services.longform_render import LONGFORM_CACHE_SUBDIR

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    db.init_db()
    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth())
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(tmp_path))

    async def run():
        events = []
        async for frame in audiobook._render_longform_sse(
            _plan(("One", "First book.")), default_voice=None,
        ):
            event = json.loads(frame.removeprefix("data:"))
            events.append(event)
            if event["type"] == "assembling":
                # External deletion/disk failure can still remove an input.
                # Exercise real ffmpeg's nonzero exit, not a raised exception.
                for path in (tmp_path / LONGFORM_CACHE_SUBDIR).glob("*.wav"):
                    path.unlink()
        return events

    events = asyncio.run(asyncio.wait_for(run(), timeout=120))
    assert events[-1]["type"] == "error", events
    job_id = events[0]["job_id"]
    assert job_store.get(job_id)["status"] == "failed"
    assert longform_resume.read_manifest("audiobook", job_id) is not None


# ── partial / total failure ─────────────────────────────────────────────────

@pytest.mark.parametrize("failure", ["nonzero", "exception", "cancelled"])
@pytest.mark.parametrize("fmt", ["m4b", "mp3"])
def test_failed_mux_removes_partial_output_and_keeps_resume(
    tmp_path, monkeypatch, failure, fmt,
):
    from api.routers import audiobook
    from services import longform_resume
    from services.longform_render import LONGFORM_CACHE_SUBDIR

    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth())
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(tmp_path))
    attempted = []

    async def partial_mux(argv, **kwargs):
        output = Path(argv[-1])
        output.write_bytes(b"incomplete encoded audio")
        attempted.append(output)
        if failure == "exception":
            raise RuntimeError("mux interrupted after writing")
        if failure == "cancelled":
            raise asyncio.CancelledError()
        return 1, "", "mux failed"

    monkeypatch.setattr("services.ffmpeg_utils.run_ffmpeg", partial_mux)
    events = []

    async def render():
        async for frame in audiobook._render_longform_sse(
            _plan(("One", "First book.")), default_voice=None, fmt=fmt,
        ):
            events.append(json.loads(frame.removeprefix("data:")))

    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(asyncio.wait_for(render(), timeout=120))
    else:
        asyncio.run(asyncio.wait_for(render(), timeout=120))
        assert events[-1]["type"] == "error", events
    assert len(attempted) == 1
    assert not attempted[0].exists(), "failed mux left an incomplete library output"
    assert not any(event["type"] == "done" for event in events)
    assert longform_resume.read_manifest("audiobook", events[0]["job_id"]) is not None
    assert list((tmp_path / LONGFORM_CACHE_SUBDIR).glob("*.wav"))


def test_plan_cache_reservation_keeps_bad_voice_failure_in_its_own_chapter(
    tmp_path, monkeypatch,
):
    from api.routers import audiobook

    def resolve(voice_id):
        if voice_id == "missing":
            raise ValueError("missing voice")
        return _resolve(voice_id)

    factory = _stub_build_synth()

    def build(*args, **kwargs):
        info = factory(*args, **kwargs)
        info["resolve"] = resolve
        return info

    monkeypatch.setattr(audiobook, "_build_synth", build)
    monkeypatch.setattr(audiobook, "_resolve_voice", resolve)
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(tmp_path))
    plan = _plan(("Good", "First."), ("Bad", "Missing."), ("Good too", "Third."))
    plan.chapters[1].spans[0].voice_id = "missing"

    async def run():
        return [json.loads(frame.removeprefix("data:")) async for frame in
                audiobook._render_longform_sse(plan, default_voice=None)]

    events = asyncio.run(asyncio.wait_for(run(), timeout=120))
    assert events[-1]["type"] == "done", events
    assert events[-1]["failed_chapters"] == [1]
    assert [event["index"] for event in events if event["type"] == "chapter"] == [0, 2]


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


def test_delivered_chapter_timings_match_muxed_metadata_after_a_failure(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    chapter_text = [
        ("Overture", "alpha"),
        ("Broken bridge", "reject-this"),
        ("Crossing", "gamma"),
        ("Homeward", "delta"),
    ]
    events = _collect_events(
        _plan(*chapter_text),
        monkeypatch,
        out,
        fmt="m4b",
        fail_on=lambda text: text.startswith("reject"),
    )
    chapters = [event for event in events if event["type"] == "chapter"]
    assert [(event["index"], event["title"]) for event in chapters] == [
        (0, "Overture"),
        (2, "Crossing"),
        (3, "Homeward"),
    ]
    timing_error = [
        abs(event["duration_ms"] - round(1000 * event["duration_s"]))
        for event in chapters
    ]
    assert all(type(event["duration_ms"]) is int for event in chapters)
    assert max(timing_error) <= 5
    total_ms = sum(map(lambda event: event["duration_ms"], chapters))
    done = next(event for event in events if event["type"] == "done")
    assert done["duration_s"] == pytest.approx(total_ms / 1000, abs=0.005)
    if _FFPROBE:
        embedded = _ffprobe(str(out / done["output"])).get("chapters", [])
        assert [item["tags"]["title"] for item in embedded] == [
            event["title"] for event in chapters
        ]
        expected_starts = [0]
        for event in chapters[:-1]:
            expected_starts.append(expected_starts[-1] + event["duration_ms"])
        actual_starts = [round(float(item["start_time"]) * 1000) for item in embedded]
        assert actual_starts == expected_starts

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
    `loudness` block to `done` (two-pass on measured signal; short tone → fallback,
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
    assert (out / done["output"]).stat().st_size > 0


def test_off_path_emits_no_loudness_block(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(_plan(("One", "hi")), monkeypatch, out, fmt="m4b")  # no loudness
    assert "mastering" not in [e["type"] for e in events]
    assert "loudness" not in events[-1]  # legacy done shape preserved


def _done_of(events):
    assert events[-1]["type"] == "done"
    return events[-1]


def test_done_summary_describes_the_render_but_never_quotes_it(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    done = _done_of(_collect_events(_plan(("Ch A", "quiet harbour lights")), monkeypatch, out,
                                    fmt="mp3", metadata={"title": "Harbour"}))
    assert done["title"] == "Harbour"
    detail = done["summary"]
    assert {k: detail[k] for k in ("lines", "words", "format", "speeds", "chapter_titles")} == {
        "lines": 1, "words": 3, "format": "mp3", "speeds": [1.0], "chapter_titles": ["Ch A"],
    }
    assert "harbour lights" not in json.dumps(done)


def test_done_title_defaults_to_first_chapter_and_summary_errors_are_swallowed(
        tmp_path, monkeypatch):
    from api.routers import audiobook
    out = tmp_path / "outputs"
    out.mkdir()
    plan = _plan(("Intro", "x"))
    assert _done_of(_collect_events(plan, monkeypatch, out, fmt="mp3"))["title"] == "Intro"
    monkeypatch.setattr(audiobook, "_render_summary",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("nope")))
    again = _done_of(_collect_events(plan, monkeypatch, out, fmt="mp3"))
    assert "summary" not in again and again["output"]


def test_done_summary_from_old_manifest_is_strict_json(tmp_path, monkeypatch):
    from services.audiobook import ExpressiveOptions
    out = tmp_path / "outputs"
    out.mkdir()
    events = _collect_events(_plan(("One", "hello")), monkeypatch, out, fmt="mp3",
                             opts=ExpressiveOptions(emo_vector=(float("nan"),) * 8))
    assert events[-1]["type"] == "done"
    json.dumps(events[-1], allow_nan=False)
