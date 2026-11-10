"""#3751: a failed Demucs run on a long source must walk a retry ladder
(standard -> memory-lean -> CPU) before the dub falls back to mixed audio, and
the user is told at separation time that the background is gone."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from services import dub_pipeline as dp


@pytest.fixture(autouse=True)
def _jobs():
    dp._dub_jobs.clear()
    dp._dub_jobs["j"] = {}
    yield
    dp._dub_jobs.clear()


def _runner(monkeypatch, outcomes, device="cuda"):
    """Fake Demucs: `outcomes` is consumed one per spawn (rc, stderr | exception)."""
    calls = []

    async def fake(job_id, cmd, *, timeout=1800.0):
        calls.append({"cmd": cmd, "timeout": timeout})
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        rc, err = outcome
        yield ("stderr", " 42%|####|")
        yield ("done", rc, err)

    monkeypatch.setattr(dp, "run_proc_streaming_stderr", fake)
    monkeypatch.setattr(dp, "get_best_device", lambda: device)
    return calls


def _drain():
    async def go():
        return [e async for e in dp._separate_background("j", "/in/audio.wav", "/jobs/j")]
    return asyncio.run(go())


def _flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def test_first_attempt_success_runs_once(monkeypatch):
    calls = _runner(monkeypatch, [(0, b"")])
    events = _drain()
    assert len(calls) == 1
    assert ("progress", 42) in events
    assert not any(e[0] == "retry" for e in events)
    assert "--segment" not in calls[0]["cmd"]
    assert calls[0]["timeout"] == dp._DEMUCS_STEP_TIMEOUT_S


def test_lean_retry_on_same_device_succeeds(monkeypatch):
    calls = _runner(monkeypatch, [(1, b"CUDA out of memory"), (0, b"")])
    events = _drain()
    assert len(calls) == 2
    assert _flag(calls[1]["cmd"], "-d") == "cuda"
    assert _flag(calls[1]["cmd"], "--segment") == "4"
    assert _flag(calls[1]["cmd"], "--overlap") == "0.1"
    retry = [e for e in events if e[0] == "retry"]
    assert retry[0][1]["attempt"] == "lean"
    assert "out of memory" in retry[0][1]["reason"]


def test_cpu_retry_after_lean_fails(monkeypatch):
    calls = _runner(monkeypatch, [(1, b"oom"), (-9, b""), (0, b"")])
    _drain()
    assert [_flag(c["cmd"], "-d") for c in calls] == ["cuda", "cuda", "cpu"]
    assert "--segment" in calls[2]["cmd"]


def test_cpu_device_has_no_second_cpu_rung(monkeypatch):
    calls = _runner(monkeypatch, [(1, b"x"), (1, b"y")], device="cpu")
    with pytest.raises(Exception) as err:
        _drain()
    assert len(calls) == 2
    assert "[standard on cpu]" in str(err.value) and "[lean on cpu]" in str(err.value)


def test_all_attempts_fail_reports_every_tail(monkeypatch):
    calls = _runner(monkeypatch, [(1, b"first boom"), (1, b"second boom"), (1, b"third boom")])
    with pytest.raises(Exception) as err:
        _drain()
    assert len(calls) == 3
    text = str(err.value)
    assert "first boom" in text and "second boom" in text and "third boom" in text


def test_user_abort_is_not_retried(monkeypatch):
    calls = _runner(monkeypatch, [(-9, b"")])
    dp._dub_jobs["j"]["aborted"] = True
    with pytest.raises(Exception):
        _drain()
    assert len(calls) == 1


def test_deleted_job_is_not_retried(monkeypatch):
    calls = _runner(monkeypatch, [(-9, b"")])
    dp._dub_jobs.pop("j")
    with pytest.raises(Exception):
        _drain()
    assert len(calls) == 1


def test_timeout_is_not_retried(monkeypatch):
    calls = _runner(monkeypatch, [HTTPException(status_code=504, detail="subprocess timed out")])
    with pytest.raises(HTTPException):
        _drain()
    assert len(calls) == 1


def test_retries_share_a_bounded_budget(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(dp.time, "monotonic", lambda: clock["t"])
    timeouts = []

    async def fake(job_id, cmd, *, timeout=1800.0):
        timeouts.append(timeout)
        clock["t"] += 1500.0  # each attempt burns 1500 s before failing
        yield ("done", 1, b"boom")

    monkeypatch.setattr(dp, "run_proc_streaming_stderr", fake)
    monkeypatch.setattr(dp, "get_best_device", lambda: "cuda")
    with pytest.raises(Exception):
        _drain()
    # 3600 s total: attempt 1 and the lean retry get the full cap; the CPU rung
    # gets only the 600 s that remain.
    assert timeouts == [dp._DEMUCS_STEP_TIMEOUT_S, dp._DEMUCS_STEP_TIMEOUT_S, 600.0]


def test_exhausted_budget_skips_the_retry(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(dp.time, "monotonic", lambda: clock["t"])
    timeouts = []

    async def fake(job_id, cmd, *, timeout=1800.0):
        timeouts.append(timeout)
        clock["t"] += dp._DEMUCS_TOTAL_BUDGET_S - 10.0
        yield ("done", 1, b"boom")

    monkeypatch.setattr(dp, "run_proc_streaming_stderr", fake)
    monkeypatch.setattr(dp, "get_best_device", lambda: "cuda")
    with pytest.raises(Exception):
        _drain()
    assert len(timeouts) == 1


def test_ingest_falls_back_with_a_clear_notice_and_status():
    import inspect

    src = inspect.getsource(dp.ingest_pipeline)
    assert "_separate_background(" in src
    assert "_BACKGROUND_LOST_NOTICE" in src
    assert '"background_separation": "ok" if has_bg else "failed"' in src
    assert "speech only" in dp._BACKGROUND_LOST_NOTICE
