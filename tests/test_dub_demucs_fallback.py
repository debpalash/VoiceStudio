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

    async def fake(job_id, cmd, *, timeout=1800.0, deadline=None):
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

    async def fake(job_id, cmd, *, timeout=1800.0, deadline=None):
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

    async def fake(job_id, cmd, *, timeout=1800.0, deadline=None):
        timeouts.append(timeout)
        clock["t"] += dp._DEMUCS_TOTAL_BUDGET_S - 10.0
        yield ("done", 1, b"boom")

    monkeypatch.setattr(dp, "run_proc_streaming_stderr", fake)
    monkeypatch.setattr(dp, "get_best_device", lambda: "cuda")
    with pytest.raises(Exception):
        _drain()
    assert len(timeouts) == 1


def test_background_lost_notice_names_the_speech_only_outcome():
    # Status values ("ok"/"failed"/"skipped") are covered behaviourally by
    # test_dub_optional_background.test_ingest_respects_background_choice.
    assert "speech only" in dp._BACKGROUND_LOST_NOTICE


def test_failed_separation_job_is_not_a_cache_hit(tmp_path, monkeypatch):
    """A retry must re-run separation, not reuse the mixed-audio fallback job."""
    import json as _json
    from core.db import db_conn, init_db

    init_db()

    d = tmp_path / "job_failed"
    d.mkdir()
    (d / "vocals.wav").write_bytes(b"RIFF")
    (d / "audio_hq.wav").write_bytes(b"RIFF")
    (d / "no_vocals.wav").write_bytes(b"RIFF")

    def seed(extra):
        with db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dub_history (id, job_data, content_hash, created_at)"
                " VALUES (?, ?, ?, datetime('now'))",
                ("job_failed", _json.dumps({"vocals_path": str(d / "vocals.wav"), "no_vocals_path": str(d / "no_vocals.wav"), **extra}), "hash_f"),
            )
            conn.commit()

    monkeypatch.setattr(dp, "safe_job_dir", lambda jid: str(tmp_path / jid))
    seed({"background_separation": "failed"})
    assert dp.find_cached_job("hash_f", "other") is None
    seed({"background_separation": "ok"})
    assert dp.find_cached_job("hash_f", "other")["job_id"] == "job_failed"


def test_retry_deadline_counts_slot_queue_time(monkeypatch):
    """The retry's absolute deadline is enforced after the semaphore wait."""
    clock = {"t": 0.0}
    monkeypatch.setattr(dp.time, "monotonic", lambda: clock["t"])

    async def go():
        sem = asyncio.Semaphore(1)
        monkeypatch.setattr(dp, "_get_semaphore", lambda: sem)
        spawned = []

        async def spawn(*a, **k):
            spawned.append(1)
            raise AssertionError("must not spawn past the deadline")

        monkeypatch.setattr(dp, "_spawn_with_retry", spawn)
        clock["t"] = 100.0  # time passed while queued
        return spawned, [e async for e in dp.run_proc_streaming_stderr(
            "j", ["x"], timeout=1800.0, deadline=100.5)]

    spawned, events = asyncio.run(go())
    assert not spawned
    assert events[0][0] == "done" and events[0][1] == -1


def test_retries_pass_the_whole_ladder_deadline(monkeypatch):
    seen = []

    async def fake(job_id, cmd, *, timeout=1800.0, deadline=None):
        seen.append(deadline)
        yield ("done", 1, b"boom")

    monkeypatch.setattr(dp, "run_proc_streaming_stderr", fake)
    monkeypatch.setattr(dp, "get_best_device", lambda: "cuda")
    with pytest.raises(Exception):
        _drain()
    assert seen[0] is None and all(d is not None for d in seen[1:]) and len(seen) == 3


def test_timeout_after_a_failed_attempt_keeps_the_earlier_failure(monkeypatch):
    calls = _runner(monkeypatch, [(1, b"CUDA out of memory"), HTTPException(status_code=504, detail="subprocess timed out after 900s")])
    with pytest.raises(HTTPException) as err:
        _drain()
    assert len(calls) == 2
    assert err.value.status_code == 504
    assert "out of memory" in err.value.detail and "timed out after 900s" in err.value.detail


def test_slot_wait_is_bounded_by_the_retry_deadline(monkeypatch):
    async def go():
        sem = asyncio.Semaphore(1)
        await sem.acquire()  # every slot busy
        monkeypatch.setattr(dp, "_get_semaphore", lambda: sem)

        async def spawn(*a, **k):
            raise AssertionError("must not spawn without a slot")

        monkeypatch.setattr(dp, "_spawn_with_retry", spawn)
        deadline = dp.time.monotonic() + 0.05
        return [e async for e in dp.run_proc_streaming_stderr("j", ["x"], deadline=deadline)]

    events = asyncio.run(asyncio.wait_for(go(), timeout=5))
    assert events[0][0] == "done" and events[0][1] == -1


def test_job_aborted_while_queued_never_spawns_the_retry(monkeypatch):
    async def go():
        sem = asyncio.Semaphore(1)
        monkeypatch.setattr(dp, "_get_semaphore", lambda: sem)

        async def spawn(*a, **k):
            raise AssertionError("aborted job must not spawn")

        monkeypatch.setattr(dp, "_spawn_with_retry", spawn)
        dp._dub_jobs["j"]["aborted"] = True
        events = [e async for e in dp.run_proc_streaming_stderr(
            "j", ["x"], deadline=dp.time.monotonic() + 600)]
        assert not sem.locked()  # the slot was released
        return events

    events = asyncio.run(go())
    assert events[0][0] == "done" and events[0][1] == -1


def _seed_cache(tmp_path, monkeypatch, name, chash, extra, files):
    import json as _json
    from core.db import db_conn, init_db

    init_db()
    d = tmp_path / name
    d.mkdir()
    for f in files:
        (d / f).write_bytes(b"RIFF")
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dub_history (id, job_data, content_hash, created_at)"
            " VALUES (?, ?, ?, datetime('now'))",
            (name, _json.dumps({"vocals_path": str(d / "vocals.wav"), **extra}), chash),
        )
        conn.commit()
    monkeypatch.setattr(dp, "safe_job_dir", lambda jid: str(tmp_path / jid))


def test_skipped_separation_job_is_reusable_but_failed_and_pre_marker_are_not(tmp_path, monkeypatch):
    # Deliberate skip: no bed, no audio_hq.wav, still a cache hit.
    _seed_cache(tmp_path, monkeypatch, "job_skip", "h_skip:voice-only",
                {"background_separation": "skipped", "no_vocals_path": None}, ["vocals.wav"])
    hit = dp.find_cached_job("h_skip:voice-only", "other")
    assert hit and hit["job_id"] == "job_skip" and hit["no_vocals_path"] is None
    # Failed separation keeps being rejected.
    _seed_cache(tmp_path, monkeypatch, "job_fail", "h_fail",
                {"background_separation": "failed", "no_vocals_path": None}, ["vocals.wav", "audio_hq.wav"])
    assert dp.find_cached_job("h_fail", "other") is None
    # Pre-marker job (no flag, no bed on disk) keeps being rejected.
    _seed_cache(tmp_path, monkeypatch, "job_old", "h_old", {"no_vocals_path": None}, ["vocals.wav", "audio_hq.wav"])
    assert dp.find_cached_job("h_old", "other") is None


def test_skipped_job_exports_speech_only_without_a_warning(caplog):
    import logging
    from api.routers import dub_export

    with caplog.at_level(logging.INFO, logger="omnivoice.api"):
        assert asyncio.run(dub_export._preserved_background(
            {"background_separation": "skipped", "no_vocals_path": None}, "j", "pt")) is None
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert asyncio.run(dub_export._preserved_background(
            {"background_separation": "failed", "no_vocals_path": None}, "j", "pt")) is None
        assert [r for r in caplog.records if r.levelno == logging.WARNING]
