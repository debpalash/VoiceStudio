"""Queue wait is not compute time (#1190/#1202).

Field reports: a 22-chunk batch dies around chunk 3 with "the job was too heavy
for the available compute", on hardware that renders chunk 1 and 2 fine. Three
defects in `run_on_gpu_pool_guarded` / `_ResilientGpuPool` compound into that:

1. The 300s clock started at SUBMIT. `loop.run_in_executor()` returns
   immediately, so `asyncio.wait_for` was timing the queue wait, not the work.
   A job queued behind a busy 1-worker pool burned its entire budget without
   executing one instruction and then blamed the hardware.
2. `reset()` used `cancel_futures=True`, so one request's timeout cancelled
   innocent QUEUED peers, which surfaced to their own callers as a bare
   CancelledError.
3. The timeout message promised "Capacity was restored automatically". It
   isn't: Python cannot kill the abandoned worker thread, which keeps running
   (and holding the device) until it finishes.

Plus the v0.3.22 length-scaled budget was wired into only two of the eleven
GPU dispatches, which is why 0.3.22 users still saw "exceeded 300s".

Every test below fails on the pre-fix code and passes after.
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"


@pytest.fixture
def mm(fresh_model_manager):
    return fresh_model_manager()


# ── Defect 1: the execution clock must not start at submit ──────────────────


def test_queue_wait_is_not_charged_to_the_execution_budget(mm):
    """A job that waits in line longer than its execution budget, then runs
    quickly, must SUCCEED. Pre-fix this raised GpuJobTimeoutError without the
    job ever having started."""
    ex = ThreadPoolExecutor(max_workers=1)
    hog_started = threading.Event()
    release = threading.Event()
    ran = threading.Event()

    def _hog():
        hog_started.set()
        release.wait(10)

    def _quick():
        ran.set()
        return "done"

    async def _drive():
        loop = asyncio.get_running_loop()
        hog = loop.run_in_executor(ex, _hog)
        assert hog_started.wait(5), "blocker never occupied the single worker"

        task = asyncio.ensure_future(mm.run_on_gpu_pool_guarded(
            _quick, what="TTS generate",
            timeout=0.3,          # execution budget: tiny
            queue_timeout=30.0,   # queue budget: generous
            executor=ex,
        ))
        # Sit in the queue for 4x the execution budget.
        await asyncio.sleep(1.2)
        assert not ran.is_set(), "job started early — test is not exercising the queue"
        assert not task.done(), "queue wait was charged to the execution budget (#1190)"

        release.set()
        result = await asyncio.wait_for(task, timeout=10)
        await hog
        return result

    try:
        assert asyncio.run(_drive()) == "done"
    finally:
        release.set()
        ex.shutdown(wait=False)


def test_never_started_job_reports_saturation_not_too_heavy(mm):
    """Crossing the QUEUE budget is a retryable saturation error, never the
    "too heavy for the available compute" verdict — nothing was computed."""
    ex = ThreadPoolExecutor(max_workers=1)
    release = threading.Event()
    ran = threading.Event()

    def _hog():
        release.wait(10)

    def _never():
        ran.set()

    async def _drive():
        loop = asyncio.get_running_loop()
        loop.run_in_executor(ex, _hog)
        await asyncio.sleep(0.2)
        with pytest.raises(mm.GpuPoolBusyError) as exc:
            await mm.run_on_gpu_pool_guarded(
                _never, what="TTS generate", timeout=30.0,
                queue_timeout=0.3, executor=ex,
            )
        return exc.value

    try:
        err = asyncio.run(_drive())
        assert isinstance(err, TimeoutError)          # retryable by base class
        assert not isinstance(err, mm.GpuJobTimeoutError)
        assert "too heavy" not in str(err)
        assert "safe to retry" in str(err)
        assert err.retry_after >= 1
        # The job was pulled out of the queue, so no compute is wasted later.
        release.set()
        assert not ran.wait(1.0)
    finally:
        release.set()
        ex.shutdown(wait=False)


# ── Defect 2: a timeout must not cancel innocent peers ──────────────────────


def test_timeout_does_not_cancel_queued_peers(mm, monkeypatch):
    """reset() may drop the pool, but a peer already queued behind the wedged
    job must still run to completion. Pre-fix (`cancel_futures=True`) the peer
    came back cancelled — a bare CancelledError in an unrelated request."""
    monkeypatch.setattr(mm, "_build_gpu_pool",
                        lambda: ThreadPoolExecutor(max_workers=1))
    pool = mm._ResilientGpuPool()
    release = threading.Event()

    def _wedge():
        release.wait(10)
        return "late"

    def _peer():
        return "peer-ok"

    try:
        with pytest.raises(mm.GpuJobTimeoutError):
            asyncio.run(mm.run_on_gpu_pool_guarded(
                _wedge, what="TTS generate", timeout=0.3,
                queue_timeout=30.0, executor=pool,
            ))
        # Queue the peer while the wedged worker still holds the only thread,
        # then let the wedge finish — the peer must run, not be cancelled.
        peer_fut = pool.submit(_peer)
        release.set()
        assert peer_fut.result(timeout=10) == "peer-ok"
        assert not peer_fut.cancelled()
    finally:
        release.set()
        pool.shutdown(wait=False)


def test_reset_still_swaps_the_pool_for_new_work(mm, monkeypatch):
    """Dropping the poisoned pool (so new submits get a clean worker) is
    preserved — only the peer-cancelling part is gone."""
    monkeypatch.setattr(mm, "_build_gpu_pool",
                        lambda: ThreadPoolExecutor(max_workers=1))
    pool = mm._ResilientGpuPool()
    release = threading.Event()
    try:
        assert pool._live_pool() is not None
        with pytest.raises(mm.GpuJobTimeoutError):
            asyncio.run(mm.run_on_gpu_pool_guarded(
                lambda: release.wait(10), what="TTS generate",
                timeout=0.3, queue_timeout=30.0, executor=pool,
            ))
        assert pool._pool is None
        assert asyncio.run(mm.run_on_gpu_pool_guarded(
            lambda: "ok", what="TTS generate", timeout=10.0, executor=pool,
        )) == "ok"
    finally:
        release.set()
        pool.shutdown(wait=False)


# ── Defect 3: the message must tell the truth ───────────────────────────────


def test_timeout_guidance_does_not_claim_capacity_was_restored(mm):
    msg = mm._timeout_guidance("TTS generate", 300.0)
    assert "Capacity was restored automatically" not in msg
    assert "restored" not in msg
    # It says what actually happens instead.
    assert "cannot be killed" in msg
    assert "until it finishes" in msg
    # Actionable for interactive users AND scripted clients.
    assert "restart the backend" in msg
    # #1808: the budget moved into Settings → Performance & Device in #1797,
    # so the remedy names that control now. The env var still works and still
    # shadows the setting — only which one we point the user at changed.
    assert "Settings → Performance & Device" in msg


# ── Defect 4: every dispatch uses the shared length-scaled budget ───────────


def _guarded_calls():
    """(file, lineno, keywords) for every run_on_gpu_pool_guarded call in the
    backend."""
    out = []
    for path in sorted(_BACKEND.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name == "run_on_gpu_pool_guarded":
                out.append((path, node.lineno,
                            {kw.arg for kw in node.keywords if kw.arg}))
    return out


def test_no_gpu_dispatch_relies_on_the_flat_default_budget():
    """Structural: every guarded dispatch passes an explicit `timeout=`.

    The v0.3.22 length-scaled budget only reached 2 of the call sites; the
    streaming path the UI tries FIRST, /v1/audio/speech, batch, dub and
    archetype previews all silently kept the flat 300s. A new dispatch that
    forgets the budget fails here."""
    calls = _guarded_calls()
    assert len(calls) >= 10, "call-site scan found suspiciously few dispatches"
    missing = [f"{p.relative_to(_BACKEND)}:{line}"
               for p, line, kws in calls if "timeout" not in kws]
    assert not missing, (
        "GPU dispatches with no explicit timeout (they silently inherit the "
        f"flat GPU_JOB_TIMEOUT_S): {missing}"
    )


def test_scaled_budget_has_one_implementation(mm):
    """generation.py's helper delegates to the canonical one, so the routers
    that import it directly can't drift from /generate."""
    import api.routers.generation as g

    long_text = "x" * 41_200
    assert mm.generate_timeout_s("hi", execution_device="cuda") == mm.GPU_JOB_TIMEOUT_S
    assert mm.generate_timeout_s(long_text, execution_device="cuda") == pytest.approx(
        mm.GPU_JOB_TIMEOUT_S + 1000.0)
    assert g._generate_timeout_s(long_text, execution_device="cuda") == mm.generate_timeout_s(
        long_text, execution_device="cuda"
    )
    assert mm.generate_timeout_s(None, execution_device="cuda") == mm.GPU_JOB_TIMEOUT_S


def test_watermark_dispatches_leave_the_gpu_pool():
    """#1169 put an AudioSeal embed (CPU work, no VRAM) on the GPU pool for
    every producer including per-chunk stream previews; on a 1-worker host each
    one serialized ahead of the next generate."""
    for rel in ("api/routers/generation.py", "api/routers/batch.py",
                "api/routers/archetypes.py"):
        src = (_BACKEND / rel).read_text(encoding="utf-8")
        for idx in range(len(src)):
            if not src.startswith("run_in_executor(", idx):
                continue
            window = src[idx:idx + 400]
            if "mark_synthetic" in window:
                assert "get_watermark_pool" in window, (
                    f"{rel}: watermark embed still dispatched to the GPU pool")


# ── Defect 5: the scripted-client contract ──────────────────────────────────


def test_admission_refuses_only_a_backed_up_pool(mm, monkeypatch):
    """No worker free but nothing queued → admit (the ordinary interactive
    second request). A full wave already waiting → refuse with a usable
    Retry-After."""
    monkeypatch.setattr(mm, "gpu_pool_stats", lambda ex=None: {
        "queued": 0, "running": 1, "workers": 1, "avg_job_s": 12.0})
    mm.check_gpu_admission(what="OpenAI TTS generate")  # must not raise

    monkeypatch.setattr(mm, "gpu_pool_stats", lambda ex=None: {
        "queued": 3, "running": 1, "workers": 1, "avg_job_s": 12.0})
    with pytest.raises(mm.GpuPoolBusyError) as exc:
        mm.check_gpu_admission(what="OpenAI TTS generate")
    assert 5 <= exc.value.retry_after <= 300
    assert "saturated" in str(exc.value)


def test_pool_tracks_queue_depth(mm, monkeypatch):
    """Admission control is only as good as the accounting behind it."""
    monkeypatch.setattr(mm, "_build_gpu_pool",
                        lambda: ThreadPoolExecutor(max_workers=1))
    pool = mm._ResilientGpuPool()
    started = threading.Event()
    release = threading.Event()
    try:
        pool.submit(lambda: (started.set(), release.wait(10)))
        assert started.wait(5)
        queued = [pool.submit(lambda: "q") for _ in range(3)]
        stats = pool.stats()
        assert stats["running"] == 1
        assert stats["queued"] == 3
        assert stats["workers"] == 1
        release.set()
        for f in queued:
            assert f.result(timeout=10) == "q"
        # Depth drains back to zero — no leak that would 429 forever.
        assert pool.stats()["queued"] == 0
    finally:
        release.set()
        pool.shutdown(wait=False)


def test_openai_compat_maps_timeouts_to_retryable_503(mm):
    """A timed-out generate must not look like a server crash to a script."""
    import api.routers.openai_compat as oc

    for err in (mm.GpuJobTimeoutError("overran"),
                mm.GpuPoolBusyError("busy", retry_after=42)):
        http = oc._typed_speech_http_error(err)
        assert http is not None, "TimeoutError fell through to the generic 500"
        assert http.status_code == 503
        assert http.headers["X-OmniVoice-Retryable"] == "true"
        assert int(http.headers["Retry-After"]) >= 1
    assert oc._typed_speech_http_error(mm.GpuPoolBusyError(
        "busy", retry_after=42)).headers["Retry-After"] == "42"


def test_job_label_cannot_forge_log_lines(mm):
    """`what` embeds request-derived data (engine ids), and it reaches the
    timeout/saturation log lines — CodeQL py/log-injection."""
    forged = "TTS engine 'x\r\nERROR forged log line' model load"
    safe = mm._log_safe(forged)
    assert "\n" not in safe and "\r" not in safe
    assert len(mm._log_safe("y" * 500)) <= 120


def _register_engine(monkeypatch, engine_id, *, sleep_s=0.0):
    import importlib
    import torch
    tts = importlib.import_module("services.tts_backend")

    class _Fake(tts.TTSBackend):
        id = engine_id
        display_name = "Fake engine (test)"

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            if sleep_s:
                threading.Event().wait(sleep_s)
            return torch.zeros(1, 2400)

    monkeypatch.setitem(tts._REGISTRY, engine_id, _Fake)
    return _Fake


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


def test_speech_429s_at_submit_when_the_pool_is_saturated(mm, client, monkeypatch):
    """The scripted-client contract: refuse at SUBMIT with a real Retry-After
    instead of accepting the job and going quiet for minutes."""
    _register_engine(monkeypatch, "admission-fake")
    monkeypatch.setattr(mm, "gpu_pool_stats", lambda ex=None: {
        "queued": 5, "running": 1, "workers": 1, "avg_job_s": 9.0})

    res = client.post("/v1/audio/speech", json={
        "model": "admission-fake", "input": "Hello.", "response_format": "wav",
    })
    assert res.status_code == 429, res.text
    assert int(res.headers["Retry-After"]) >= 1
    assert res.headers["X-OmniVoice-Retryable"] == "true"
    assert "saturated" in res.json()["detail"]


def test_speech_timeout_is_a_retryable_503_not_a_500(mm, client, monkeypatch):
    """A generate that overran its budget used to reach the scripted client as
    a generic 500 — indistinguishable from a crash."""
    _register_engine(monkeypatch, "slow-gen-fake", sleep_s=3.0)
    # The route derives its budget from the shared helper at call time, so
    # shrinking the constant proves the route is actually using it (#1190).
    monkeypatch.setattr(mm, "GPU_JOB_TIMEOUT_S", 0.3)

    res = client.post("/v1/audio/speech", json={
        "model": "slow-gen-fake", "input": "Hello.", "response_format": "wav",
    })
    assert res.status_code == 503, res.text
    assert res.headers["X-OmniVoice-Retryable"] == "true"
    assert int(res.headers["Retry-After"]) >= 1


def test_batch_surfaces_a_timed_out_segment_instead_of_a_silent_gap():
    """batch.py used to swallow a timed-out segment into a silent stretch of
    the dubbed track: a finished-looking video with missing speech."""
    src = (_BACKEND / "api/routers/batch.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    handlers = [h for node in ast.walk(tree) if isinstance(node, ast.Try)
                for h in node.handlers
                if isinstance(h.type, ast.Name) and h.type.id == "TimeoutError"]
    assert handlers, "batch.py has no TimeoutError handler — timeouts are still swallowed"
    assert any(isinstance(n, ast.Raise) for h in handlers for n in ast.walk(h)), (
        "batch.py catches TimeoutError but does not surface it"
    )
