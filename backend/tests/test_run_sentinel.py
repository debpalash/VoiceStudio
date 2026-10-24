"""core.run_sentinel — deployment-agnostic crash forensics (#1164).

Fail-before/pass-after: before the sentinel module existed, a backend process
death in a browser/dev/Docker deployment left NO backend-side evidence at all
(crash_log.txt is only written on *caught* route exceptions), so the next run
had nothing to report and "Can't reach the local VoiceStudio backend" arrived
with zero diagnostics. These tests pin the whole forensics contract: a
leftover sentinel with a dead pid becomes a crash record; a clean shutdown
never does; a live second instance is never misreported; the record store
mirrors the desktop shell's marker semantics (cap 3, ack watermark,
version gate, read-only reads).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import run_sentinel


@pytest.fixture()
def sentinel_env(monkeypatch, tmp_path):
    """Redirect every on-disk artifact into tmp_path and reset module state."""
    # Re-resolve the module at TEST time and heal this file's global: earlier
    # suite members (tests/smoke/test_boot_smoke.py) purge ``core.*`` from
    # sys.modules, so the collection-time import above goes STALE in a
    # combined `pytest tests/ backend/tests/` run — the endpoint under test
    # (freshly imported by the `client` fixture) then reads the REAL
    # CRASH_RECORD_PATH while this fixture patches the stale twin, and the
    # ack/notification roundtrips fail. CI runs the two trees in isolated
    # invocations and never sees this; local combined runs do.
    global run_sentinel
    import core.run_sentinel as _fresh_run_sentinel
    run_sentinel = _fresh_run_sentinel
    monkeypatch.setattr(run_sentinel, "SENTINEL_PATH", str(tmp_path / "run_sentinel.json"))
    monkeypatch.setattr(
        run_sentinel, "CRASH_RECORD_PATH", str(tmp_path / "last_run_crash.json")
    )
    monkeypatch.setattr(run_sentinel, "LOG_PATH", str(tmp_path / "omnivoice.log"))
    run_sentinel._reset_for_tests()
    yield tmp_path
    run_sentinel._reset_for_tests()


def _dead_pid() -> int:
    """A real pid that is guaranteed dead: a subprocess that already exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Sentinel lifecycle ─────────────────────────────────────────────────────


def test_write_then_clean_clear_yields_no_crash(sentinel_env):
    """A clean run (write → clear, i.e. lifespan shutdown ran — including a
    `uvicorn --reload` restart) must leave nothing for the next startup."""
    assert run_sentinel.write_sentinel() is True
    assert os.path.exists(run_sentinel.SENTINEL_PATH)
    sent = _read(run_sentinel.SENTINEL_PATH)
    assert sent["pid"] == os.getpid()
    assert sent["version"]

    run_sentinel.clear_sentinel()
    assert not os.path.exists(run_sentinel.SENTINEL_PATH)
    assert run_sentinel.detect_unclean_shutdown() is None
    assert run_sentinel.newest_record() is None


def test_unclean_shutdown_yields_crash_record(sentinel_env, monkeypatch):
    """THE regression: a leftover sentinel whose pid is dead = the previous
    run died without shutdown → a crash record exists for the UI to surface.
    (Fail-before: without core.run_sentinel there was no record, ever.)"""
    started = time.time() - 120
    activity_ts = time.time() - 30
    with open(run_sentinel.SENTINEL_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "pid": _dead_pid(),
                "started_at": started,
                "version": run_sentinel.APP_VERSION,
                "last_activity": {"ts": activity_ts, "kind": "generate", "detail": "omnivoice"},
            },
            f,
        )

    record = run_sentinel.detect_unclean_shutdown()
    assert record is not None
    assert record["last_activity"]["kind"] == "generate"
    lo, hi = record["ended_between"]
    assert lo == pytest.approx(activity_ts)
    assert hi >= lo
    assert record["uptime_hint_s"] == pytest.approx(activity_ts - started)
    # Sentinel is consumed; a second detect must not double-report.
    assert not os.path.exists(run_sentinel.SENTINEL_PATH)
    assert run_sentinel.detect_unclean_shutdown() is None

    newest = run_sentinel.newest_record()
    assert newest is not None
    rec, acked = newest
    assert rec["detected_at"] == record["detected_at"]
    assert acked is False, "a fresh crash record must be unacknowledged"


def test_lifespan_clears_sentinel_even_if_later_shutdown_raises(monkeypatch, tmp_path):
    """THE #1895 regression: before this fix, ``clear_sentinel()`` was the
    LAST statement of ``main.py``'s lifespan shutdown, behind ~50s of bounded
    waits plus model unload / ``free_vram()`` / ``gc.collect()`` / httpx
    close. The desktop shell's quit path grants only a 2s grace before
    SIGKILL (``frontend/src-tauri/src/bootstrap.rs``
    ``terminate_process_tree``), and Windows grants no graceful phase at all
    (``tools.rs``) — nowhere near enough, so a deliberate, clean quit
    routinely got killed before reaching that last line, leaving the
    sentinel behind for the NEXT startup to misreport as "did not shut down
    cleanly... likely crashed".

    Simulates that class of interruption without an actual SIGKILL: a later
    shutdown step (``model_loads_begin_shutdown()``, called unguarded well
    after the sentinel clear) raises, so nothing past it in the shutdown
    body ever runs — for this purpose, the same effect as being killed
    mid-teardown.

    Fail-before/pass-after: with ``clear_sentinel()`` moved to the TOP of
    the shutdown block (immediately after ``yield``), the sentinel is
    already gone by the time this raise happens, so the next startup must
    not fabricate a crash record.
    """
    import asyncio
    from fastapi import FastAPI

    # Fresh `main`/`core`/`api`/`services` import, mirroring
    # tests/test_model_load_shutdown.py's `_reimported_backend_modules`: a
    # sibling suite may have purged these names from sys.modules, leaving a
    # collection-time alias stale. Purging and re-importing here makes this
    # test self-consistent in isolation, not dependent on suite order.
    purge_names = ("main", "core", "api", "services")
    purge_prefixes = ("core.", "api.", "services.")
    saved = {
        name: mod for name, mod in sys.modules.items()
        if name in purge_names or name.startswith(purge_prefixes)
    }

    def _purge():
        for name in [
            n for n in sys.modules
            if n in purge_names or n.startswith(purge_prefixes)
        ]:
            sys.modules.pop(name, None)

    _purge()
    try:
        import main as main_mod
        from core import run_sentinel as fresh_run_sentinel

        monkeypatch.setattr(
            fresh_run_sentinel, "SENTINEL_PATH", str(tmp_path / "run_sentinel.json")
        )
        monkeypatch.setattr(
            fresh_run_sentinel, "CRASH_RECORD_PATH", str(tmp_path / "last_run_crash.json")
        )
        monkeypatch.setattr(
            fresh_run_sentinel, "LOG_PATH", str(tmp_path / "omnivoice.log")
        )
        fresh_run_sentinel._reset_for_tests()

        def _boom():
            raise RuntimeError("simulated kill: interrupted after the early clear")

        monkeypatch.setattr(main_mod, "model_loads_begin_shutdown", _boom)

        async def scenario():
            app = FastAPI()
            async with main_mod.lifespan(app):
                pass

        with pytest.raises(RuntimeError, match="simulated kill"):
            asyncio.run(scenario())

        assert not os.path.exists(fresh_run_sentinel.SENTINEL_PATH), (
            "sentinel must already be cleared even though a later shutdown "
            "step raised before ever reaching the old clear-sentinel line"
        )
        assert fresh_run_sentinel.detect_unclean_shutdown() is None, (
            "a deliberate quit interrupted after the early clear must never "
            "be reported as a crash on the next startup"
        )
    finally:
        _purge()
        sys.modules.update(saved)


def test_live_pid_means_second_instance_not_a_crash(sentinel_env):
    """A sentinel owned by a LIVE process is a concurrent second instance
    sharing DATA_DIR — never a crash, and we must not take over or delete
    its sentinel."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        payload = {
            "pid": child.pid,
            # Plausible ownership: the sentinel's started_at is consistent
            # with the live process's birth (see the pid-reuse test for the
            # inconsistent case).
            "started_at": time.time(),
            "version": run_sentinel.APP_VERSION,
            "last_activity": None,
        }
        with open(run_sentinel.SENTINEL_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)

        assert run_sentinel.detect_unclean_shutdown() is None
        assert run_sentinel.newest_record() is None, "no crash record for a live instance"
        # The foreign sentinel is left alone and ours is never written over it.
        assert run_sentinel.write_sentinel() is False
        assert _read(run_sentinel.SENTINEL_PATH)["pid"] == payload["pid"]
        # And clear_sentinel (our clean shutdown) must not delete THEIR sentinel.
        run_sentinel.clear_sentinel()
        assert os.path.exists(run_sentinel.SENTINEL_PATH)
    finally:
        child.kill()
        child.wait()


def test_pid_reuse_is_defeated_by_create_time(sentinel_env):
    """A live pid whose process was born long AFTER the sentinel's own
    started_at cannot be the run that wrote it (the OS reused the pid) — the
    original process is dead → crash record."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with open(run_sentinel.SENTINEL_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "pid": child.pid,  # live, but born ~now…
                    "started_at": time.time() - 10_000,  # …not 10 000 s ago
                    "version": run_sentinel.APP_VERSION,
                    "last_activity": None,
                },
                f,
            )
        assert run_sentinel.detect_unclean_shutdown() is not None
    finally:
        child.kill()
        child.wait()


# ── Activity touches ───────────────────────────────────────────────────────


def test_touch_activity_throttles_and_never_raises(sentinel_env, monkeypatch):
    run_sentinel.write_sentinel()
    run_sentinel.touch_activity("generate", "omnivoice")
    first = _read(run_sentinel.SENTINEL_PATH)
    # write_sentinel just wrote; the immediate touch is throttled off disk…
    assert first["last_activity"] is None
    # …but once the throttle window passes, the touch persists.
    monkeypatch.setitem(run_sentinel._state, "last_write", 0.0)
    run_sentinel.touch_activity("transcribe", "dub")
    persisted = _read(run_sentinel.SENTINEL_PATH)
    assert persisted["last_activity"]["kind"] == "transcribe"
    assert persisted["last_activity"]["detail"] == "dub"

    # Exception safety: a broken disk write must never break the work.
    monkeypatch.setitem(run_sentinel._state, "last_write", 0.0)
    monkeypatch.setattr(
        run_sentinel, "_write_json_atomic", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    run_sentinel.touch_activity("generate", "x")  # must not raise


def test_touch_activity_without_ownership_never_writes(sentinel_env):
    """No sentinel written (e.g. foreign live instance) → touches stay
    in-memory and never create/overwrite the file."""
    run_sentinel.touch_activity("generate", "omnivoice")
    assert not os.path.exists(run_sentinel.SENTINEL_PATH)


def test_idle_shell_exit_is_retained_without_becoming_a_user_warning(sentinel_env):
    record = {
        "last_activity": None,
        "log_tail": [
            "INFO VoiceStudio model loaded successfully.",
            "INFO Preload complete - model ready.",
        ],
    }
    assert run_sentinel.warrants_user_notice(record) is False


def test_interrupted_work_or_fatal_startup_still_warrants_a_warning(sentinel_env):
    assert run_sentinel.warrants_user_notice(
        {"last_activity": {"kind": "generate"}, "log_tail": []}
    ) is True
    assert run_sentinel.warrants_user_notice(
        {"last_activity": None, "log_tail": ["CRITICAL: native runtime failed"]}
    ) is True


# ── Record store semantics (mirrors crash.rs) ──────────────────────────────


def _crash_once(kind="generate"):
    last_activity = None
    if kind is not None:
        last_activity = {"ts": time.time() - 5, "kind": kind, "detail": None}
    with open(run_sentinel.SENTINEL_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "pid": _dead_pid(),
                "started_at": time.time() - 60,
                "version": run_sentinel.APP_VERSION,
                "last_activity": last_activity,
            },
            f,
        )
    return run_sentinel.detect_unclean_shutdown()


def test_records_are_capped_at_three_newest_first(sentinel_env):
    for i in range(4):
        assert _crash_once(kind=f"k{i}") is not None
    store = _read(run_sentinel.CRASH_RECORD_PATH)
    assert len(store["records"]) == run_sentinel.MAX_RECORDS == 3
    kinds = [r["last_activity"]["kind"] for r in store["records"]]
    assert kinds == ["k3", "k2", "k1"], "newest first, oldest dropped"


def test_ack_is_a_watermark_not_a_delete(sentinel_env):
    _crash_once()
    rec, acked = run_sentinel.newest_record()
    assert not acked
    run_sentinel.acknowledge()
    rec2, acked2 = run_sentinel.newest_record()
    assert acked2 is True
    assert rec2["detected_at"] == rec["detected_at"], "ack never deletes the evidence"
    # A NEWER death re-arms the notice.
    _crash_once(kind="later")
    rec3, acked3 = run_sentinel.newest_record()
    assert rec3["last_activity"]["kind"] == "later"
    assert acked3 is False
    assert len(_read(run_sentinel.CRASH_RECORD_PATH)["records"]) == 2


def test_version_gate_hides_other_release_records_and_reads_never_write(sentinel_env):
    _crash_once()
    store = _read(run_sentinel.CRASH_RECORD_PATH)
    store["records"][0]["version"] = "0.0.1"
    with open(run_sentinel.CRASH_RECORD_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f)
    before = Path(run_sentinel.CRASH_RECORD_PATH).read_bytes()

    assert run_sentinel.newest_record("9.9.9") is None, "other release = stale"
    # Preview stamps match their base release (X.Y.Z-N == X.Y.Z).
    assert run_sentinel.newest_record("0.0.1-7") is not None
    actual = Path(run_sentinel.CRASH_RECORD_PATH).read_bytes()
    assert actual == before, (
        "the read path must never write (crash.rs read-only contract)"
    )
    # Versionless legacy records never surface either.
    store["records"][0]["version"] = ""
    with open(run_sentinel.CRASH_RECORD_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f)
    assert run_sentinel.newest_record("0.0.1") is None


def test_log_tail_is_captured_scrubbed_and_capped(sentinel_env):
    secret = "hf_" + "A" * 34
    lines = [f"line {i}" for i in range(60)] + [f"ERROR token={secret} at /Users/eve/x.wav"]
    with open(run_sentinel.LOG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    record = _crash_once()
    tail = record["log_tail"]
    assert len(tail) == run_sentinel.LOG_TAIL_LINES
    joined = "\n".join(tail)
    assert secret not in joined, "secrets must be scrubbed before they can reach a report"
    assert "/Users/eve" not in joined
    assert "line 59" in joined, "the newest lines are the ones kept"


def test_corrupt_sentinel_and_store_never_break_startup(sentinel_env):
    with open(run_sentinel.SENTINEL_PATH, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert run_sentinel.detect_unclean_shutdown() is None  # never raises
    with open(run_sentinel.CRASH_RECORD_PATH, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert run_sentinel.newest_record() is None
    run_sentinel.acknowledge()  # must not raise


# ── Endpoint contract ──────────────────────────────────────────────────────


@pytest.fixture()
def client(sentinel_env):
    from api.routers.system import router

    app = FastAPI()
    app.include_router(router)
    # client=127.0.0.1 satisfies the router-level require_loopback gate.
    return TestClient(app, client=("127.0.0.1", 50000))


def test_endpoint_reports_nothing_without_a_crash(client):
    r = client.get("/system/last-run-crash")
    assert r.status_code == 200
    assert r.json() == {"record": None, "acknowledged": True}


def test_endpoint_and_ack_roundtrip(client):
    _crash_once(kind="transcribe")
    body = client.get("/system/last-run-crash").json()
    assert body["acknowledged"] is False
    assert body["record"]["last_activity"]["kind"] == "transcribe"
    assert body["record"]["version"] == run_sentinel.APP_VERSION
    assert isinstance(body["record"]["log_tail"], list)

    assert client.post("/system/last-run-crash/ack").status_code == 200
    assert client.get("/system/last-run-crash").json()["acknowledged"] is True


def test_notification_surfaces_unacked_crash_and_reack(client):
    _crash_once(kind="generate")
    notes = client.get("/system/notifications").json()["notifications"]
    crash_notes = [n for n in notes if n["id"].startswith("last-run-crash-")]
    assert len(crash_notes) == 1
    assert crash_notes[0]["level"] == "error"

    client.post("/system/last-run-crash/ack")
    notes = client.get("/system/notifications").json()["notifications"]
    assert not [n for n in notes if n["id"].startswith("last-run-crash-")]

    # A NEW death re-notifies with a NEW id (detected_at is embedded).
    _crash_once(kind="again")
    notes = client.get("/system/notifications").json()["notifications"]
    fresh = [n for n in notes if n["id"].startswith("last-run-crash-")]
    assert len(fresh) == 1
    assert fresh[0]["id"] != crash_notes[0]["id"]


def test_notification_keeps_idle_exit_forensics_without_repeated_warning(client):
    record = _crash_once(kind=None)
    assert record is not None

    notes = client.get("/system/notifications").json()["notifications"]
    assert not [n for n in notes if n["id"].startswith("last-run-crash-")]

    details = client.get("/system/last-run-crash").json()
    assert details["record"]["detected_at"] == record["detected_at"]
    assert details["acknowledged"] is False


def test_crash_log_tail_memory_depends_on_tail_size(sentinel_env, monkeypatch):
    import tracemalloc
    import core.scrub

    line = "diagnostic line " + "x" * 112 + "\n"
    with open(run_sentinel.LOG_PATH, "w", encoding="utf-8") as handle:
        for _ in range(48_000):
            handle.write(line)
    monkeypatch.setattr(core.scrub, "scrub_text", lambda text: text)
    tracemalloc.start()
    try:
        result = run_sentinel._scrubbed_log_tail(5)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == [line.rstrip("\n")] * 5
    assert peak < 1_000_000, f"tail collection allocated {peak} bytes"
