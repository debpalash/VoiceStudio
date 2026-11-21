"""plan-04 (#131) — pipeline error transparency regression tests.

Test-matrix from the issue (every failure → specific UI cause + logged
traceback). Written RED before the emit-site changes. Fixture-free: the worker
path is pure-Python; the dub-pipeline paths force failure via a missing file and
a monkeypatched downloader, so they don't need real media/network.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core.db import init_db
from core.tasks import TaskManager
from services import dub_pipeline as dp


@pytest.fixture(autouse=True)
def _db():
    init_db()
    yield


def _error_events(history) -> list[dict]:
    out = []
    for e in history:
        if e and isinstance(e, str) and e.startswith("data:") and '"type": "error"' in e:
            out.append(json.loads(e[len("data: "):]))
    return out


async def _empty_boom(*a, **k):
    """Async-generator task that raises with an EMPTY message (the cryptic case)."""
    if False:
        yield
    raise ValueError("")


async def _runtime_boom(*a, **k):
    if False:
        yield
    raise RuntimeError("ffprobe blew up")


async def _drain_failing_task(boom):
    """Run a failing task and return all SSE events the worker emits.

    Race-free: the listener is registered BEFORE the worker is started, so no
    event (including the terminal error + EOF) can be missed, and we drain to
    the EOF sentinel instead of cancelling the worker mid-push.
    """
    tm = TaskManager()
    tid = f"t_{uuid.uuid4().hex[:8]}"
    await tm.add_task(tid, "prep", boom)
    q: asyncio.Queue = asyncio.Queue()
    await tm.add_listener(tid, q)
    worker = asyncio.create_task(tm.worker())
    events: list = []
    try:
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            if ev is None:  # EOF sentinel pushed in the worker's finally
                break
            events.append(ev)
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            # Expected: we cancel the worker loop to tear it down after draining.
            pass
    return events


# ── US1 + US2: worker failure path (the #122 "unknown error" / silent log) ──

def test_worker_failure_emits_structured_nonempty_reason():
    """A task that raises with an EMPTY message must still surface a specific,
    non-empty reason + error_class + stage — not a bare/empty string — and log
    the real exception with a traceback (US2)."""

    # Capture directly on the task logger — robust against the app's logging
    # config (propagate flags) and asyncio task boundaries.
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):  # noqa: D401
            records.append(record)

    handler = _Capture(level=logging.ERROR)
    tlog = logging.getLogger("omnivoice.tasks")
    # The app may have run dictConfig(disable_existing_loggers=True) on import,
    # which leaves this logger disabled in the test process. Force it live so we
    # can assert the worker actually logs the traceback.
    tlog.disabled = False
    tlog.setLevel(logging.DEBUG)
    tlog.addHandler(handler)

    try:
        events = asyncio.run(_drain_failing_task(_empty_boom))
    finally:
        tlog.removeHandler(handler)

    errs = _error_events(events)
    assert errs, "worker must push a structured error event"
    evt = errs[-1]
    assert evt["reason"], "reason must be non-empty even for an empty-message exception"
    assert evt["error_class"] == "ValueError"
    assert evt["stage"] == "task"
    # US2: the real exception was logged with a traceback
    assert any(r.exc_info for r in records), "expected a logged traceback"


# ── US1: extract-fails-on-bad-input (Test-matrix #1) ────────────────────────

def test_extract_failure_yields_structured_error(tmp_path):
    async def _run():
        events = []
        src = {"path": str(tmp_path / "does_not_exist.mp4")}
        async for ev in dp.ingest_pipeline("j_ext", str(tmp_path), src):
            events.append(ev)
        return events

    errs = _error_events(asyncio.run(_run()))
    assert errs, "a failed extract must yield an error event"
    evt = errs[-1]
    assert evt["stage"] in ("extract", "ingest")
    assert evt["reason"]
    assert evt["error_class"]  # structured, not a bare string


# ── US1: remote/url ingest failure (Test-matrix #2) ─────────────────────────

def test_url_ingest_failure_yields_structured_error(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("yt-dlp: Video unavailable")

    monkeypatch.setattr(dp, "yt_download_sync", _boom)

    async def _run():
        events = []
        src = {"kind": "url", "url": "https://example.com/watch?v=x"}
        async for ev in dp.ingest_pipeline("j_url", str(tmp_path), src):
            events.append(ev)
        return events

    errs = _error_events(asyncio.run(_run()))
    assert errs, "a failed url ingest must yield an error event"
    evt = errs[-1]
    assert evt["stage"] in ("download", "ingest")
    assert evt["error_class"] == "RuntimeError"
    assert "unavailable" in evt["reason"].lower()


# ── US3: fatal error event carries a sanitized diagnostic block ─────────────

def test_fatal_error_event_carries_sanitized_diagnostic():
    leaked = "hf_" + "C" * 36
    prev = os.environ.get("HF_TOKEN")
    os.environ["HF_TOKEN"] = leaked
    try:
        events = asyncio.run(_drain_failing_task(_runtime_boom))
    finally:
        if prev is None:
            os.environ.pop("HF_TOKEN", None)
        else:
            os.environ["HF_TOKEN"] = prev

    errs = _error_events(events)
    assert errs, "fatal error must emit a structured event"
    evt = errs[-1]
    assert evt.get("diagnostic"), "fatal error must carry a copyable diagnostic block"
    assert "task" in evt["diagnostic"]
    assert leaked not in evt["diagnostic"], "diagnostic must not leak the HF token"


def test_demucs_separates_the_hq_stereo_extraction(tmp_path, monkeypatch):
    """The music bed's fidelity ceiling is set at INGEST: Demucs used to
    separate audio.wav — the 16 kHz MONO file extracted for ASR — so every
    dub's background inherited mono (stereo image gone; measured L/R
    correlation 1.000 vs the original's 0.754) and an 8 kHz bandwidth
    ceiling. Separation must run on the full-quality stereo extraction, with
    the ASR file only as the fallback when that extraction fails."""
    import asyncio
    from services import dub_pipeline as dp

    calls = []

    def _factory(job_id):
        async def run_proc(cmd, **kw):
            calls.append([str(c) for c in cmd])
            # Fake every subprocess as success; create expected outputs.
            for i, c in enumerate(cmd):
                if str(c).endswith(".wav") and str(cmd[0]).endswith(("ffmpeg", "ffmpeg.exe")) is False:
                    pass
            # ffmpeg extraction: last arg before -y or the .wav path
            for c in cmd:
                if str(c).endswith(".wav"):
                    open(c, "wb").write(b"RIFF")
            class P: returncode = 0
            return P(), b"", b""
        return run_proc

    async def _streaming(job_id, cmd, timeout=0):
        calls.append([str(c) for c in cmd])
        # Pretend demucs succeeded but wrote nothing — pipeline degrades to
        # mixed audio, which is fine: this test only inspects the COMMANDS.
        yield ("done", 0, b"")

    monkeypatch.setattr(dp, "run_proc_factory", _factory)
    monkeypatch.setattr(dp, "run_proc_streaming_stderr", _streaming)
    monkeypatch.setattr(dp, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dp, "get_best_device", lambda: "cpu")

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 52)

    async def _drain():
        async for _ in dp.ingest_pipeline(
            job_id="t_hq", job_dir=str(tmp_path),
            source={"kind": "path", "path": str(video)},
        ):
            pass

    try:
        asyncio.run(_drain())
    except Exception:
        pass  # later stages may bail on fake media; the commands are recorded

    extracts = [c for c in calls if c and c[0] == "ffmpeg" and "-vn" in c]
    # ASR (mono 16k) and HQ (stereo 44.1k) now come from ONE ffmpeg pass with
    # two outputs; the pair must both be present on the command line.
    assert len(extracts) == 1, f"expected one combined extraction, got {len(extracts)}"
    c = extracts[0]
    assert c[c.index("16000") + 2] == "-ac" or "16000" in c
    assert c[c.index("-ar", c.index("16000")) - 0] == "-ar"
    assert c[c.index("16000") + 1] == "-ac" and c[c.index("16000") + 2] == "1"   # ASR stays mono 16k
    assert c[c.index("44100") + 1] == "-ac" and c[c.index("44100") + 2] == "2"   # stereo 44.1k
    demucs = next((c for c in calls if "demucs.separate" in " ".join(c)), None)
    assert demucs is not None
    assert any(str(a).endswith("audio_hq.wav") for a in demucs), (
        "demucs must separate the HQ stereo extraction, not the mono ASR file"
    )


def test_pre_hq_stem_cache_is_not_reused(tmp_path, monkeypatch):
    """Content-hash cache gate (review finding on the HQ-extraction change):
    stems separated before the HQ change came from the 16 kHz mono ASR file.
    Reusing them would keep serving the narrow-band mono bed forever for that
    video — the cache must skip candidates whose job dir lacks the
    audio_hq.wav marker, and reuse ones that have it."""
    import json as _json
    from services import dub_pipeline as dp
    from core.db import db_conn

    def _seed(job_id):
        d = tmp_path / job_id
        d.mkdir()
        (d / "vocals.wav").write_bytes(b"RIFF")
        with db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dub_history (id, job_data, content_hash, created_at)"
                " VALUES (?, ?, ?, datetime('now'))",
                (job_id, _json.dumps({"vocals_path": str(d / "vocals.wav")}), "hash1"),
            )
            conn.commit()
        return d

    monkeypatch.setattr(dp, "safe_job_dir", lambda jid: str(tmp_path / jid))

    old = _seed("job_old")          # pre-HQ stems: no audio_hq.wav marker
    assert dp.find_cached_job("hash1", "someone_else") is None

    (old / "audio_hq.wav").write_bytes(b"RIFF")   # HQ marker present → reusable
    hit = dp.find_cached_job("hash1", "someone_else")
    assert hit is not None and hit["job_id"] == "job_old"
