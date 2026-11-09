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


@pytest.fixture()
def pristine_backend(monkeypatch):
    """Import ``main`` and friends from scratch; put the old modules back after."""
    roots = {"main", "core", "api", "services"}

    def belongs(name):
        return name in roots or name.split(".", 1)[0] in roots

    stash = {k: v for k, v in sys.modules.items() if belongs(k)}
    for k in stash:
        del sys.modules[k]
    try:
        yield
    finally:
        for k in [k for k in sys.modules if belongs(k)]:
            del sys.modules[k]
        sys.modules.update(stash)


def test_shutdown_retires_sentinel_before_slow_teardown(pristine_backend, monkeypatch, tmp_path):
    """#1895: the shell SIGKILLs a quitting backend after ~2 s. A teardown step
    that dies partway must not strand the sentinel, or the next launch would
    invent a crash."""
    import asyncio

    import main as backend_main
    from core import run_sentinel as sentinel

    monkeypatch.setattr(sentinel, "SENTINEL_PATH", str(tmp_path / "run_sentinel.json"))
    monkeypatch.setattr(sentinel, "CRASH_RECORD_PATH", str(tmp_path / "last_run_crash.json"))
    monkeypatch.setattr(sentinel, "LOG_PATH", str(tmp_path / "omnivoice.log"))
    sentinel._reset_for_tests()

    def explode():
        raise RuntimeError("teardown blew up")

    monkeypatch.setattr(backend_main, "model_loads_begin_shutdown", explode)

    async def start_and_stop():
        async with backend_main.lifespan(FastAPI()):
            pass

    try:
        with pytest.raises(RuntimeError, match="teardown blew up"):
            asyncio.run(start_and_stop())
        assert not Path(sentinel.SENTINEL_PATH).exists()
        assert sentinel.detect_unclean_shutdown() is None
    finally:
        sentinel._reset_for_tests()


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


# ── crash context: what the shell can attach to a native crash ───────────


def _emitted_context(capsys):
    lines = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith(run_sentinel.CONTEXT_PREFIX)
    ]
    return [json.loads(line[len(run_sentinel.CONTEXT_PREFIX):]) for line in lines]


def test_activity_tells_the_shell_the_memory_at_that_moment(sentinel_env, monkeypatch, capsys):
    import psutil
    from types import SimpleNamespace

    gib = 1024 ** 3
    monkeypatch.setattr(
        psutil, "virtual_memory", lambda: SimpleNamespace(available=3 * gib, total=8 * gib)
    )
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(free=gib, total=2 * gib))
    run_sentinel._reset_for_tests()

    run_sentinel.touch_activity("model_load", "omnivoice-tts")

    context = _emitted_context(capsys)[-1]
    assert context["kind"] == "model_load"
    assert context["detail"] == "omnivoice-tts"
    assert context["ram_free_gb"] == 3.0
    assert context["ram_total_gb"] == 8.0
    assert context["swap_free_gb"] == 1.0
    assert context["swap_total_gb"] == 2.0
    # A browser or Docker session has no shell: the next run reads this instead.
    assert run_sentinel._state["last_activity"]["ram_free_gb"] == 3.0


def test_repeating_an_activity_does_not_flood_the_log(sentinel_env, capsys):
    run_sentinel._reset_for_tests()

    run_sentinel.touch_activity("task", "generate")
    run_sentinel.touch_activity("task", "generate")
    assert len(_emitted_context(capsys)) == 1

    run_sentinel.touch_activity("model_load", "omnivoice-tts")
    assert len(_emitted_context(capsys)) == 1


def test_a_broken_memory_probe_never_breaks_the_work(sentinel_env, monkeypatch, capsys):
    import psutil

    def boom():
        raise OSError("no")

    monkeypatch.setattr(psutil, "virtual_memory", boom)
    monkeypatch.setattr(psutil, "swap_memory", boom)
    run_sentinel._reset_for_tests()

    run_sentinel.touch_activity("task", "generate")

    context = _emitted_context(capsys)[-1]
    assert context["kind"] == "task"
    assert "ram_free_gb" not in context


def test_context_names_the_device_without_starting_a_probe(sentinel_env, monkeypatch, capsys):
    from core import device_caps

    device_caps.detect_host_caps.cache_clear()
    run_sentinel._reset_for_tests()
    monkeypatch.setattr(
        device_caps, "_probe", lambda: pytest.fail("the context must not start a GPU probe")
    )

    run_sentinel.touch_activity("task", "generate")

    assert "device" not in _emitted_context(capsys)[-1]


def test_the_next_run_keeps_the_memory_in_its_crash_record(sentinel_env):
    record = run_sentinel._build_crash_record(
        {
            "pid": 1,
            "started_at": 100.0,
            "version": "1",
            "last_activity": {
                "ts": 110.0,
                "kind": "model_load",
                "detail": "omnivoice-tts",
                "engine": "omnivoice",
                "device": "cpu",
                "ram_free_gb": 3.1,
                "ram_total_gb": 7.7,
                "note": "dropped",
            },
        },
        120.0,
    )
    activity = record["last_activity"]
    assert activity["ram_free_gb"] == 3.1
    assert activity["engine"] == "omnivoice"
    assert "note" not in activity


def test_context_includes_the_device_once_the_probe_has_run(sentinel_env, monkeypatch, capsys):
    from types import SimpleNamespace

    from core import device_caps

    device_caps.detect_host_caps.cache_clear()
    monkeypatch.setattr(device_caps, "_probe", lambda: SimpleNamespace(family="rocm"))
    device_caps.detect_host_caps()
    run_sentinel._reset_for_tests()
    try:
        run_sentinel.touch_activity("task", "generate")
    finally:
        device_caps.detect_host_caps.cache_clear()

    assert _emitted_context(capsys)[-1]["device"] == "rocm"


def test_a_blocked_stderr_does_not_hold_the_lock(sentinel_env, monkeypatch):
    """A full pipe would otherwise stop every thread that records activity."""
    import io

    held = []

    class Recording(io.StringIO):
        def write(self, text):
            held.append(run_sentinel._lock.locked())
            return super().write(text)

    monkeypatch.setattr(sys, "stderr", Recording())
    run_sentinel._reset_for_tests()

    run_sentinel.touch_activity("task", "generate")

    assert held and not any(held)


def test_a_full_disk_does_not_hide_the_context(sentinel_env, monkeypatch, capsys):
    def full(*_args):
        raise OSError(28, "No space left on device")

    run_sentinel._reset_for_tests()
    run_sentinel.write_sentinel()
    monkeypatch.setattr(run_sentinel, "_write_json_atomic", full)
    monkeypatch.setitem(run_sentinel._state, "last_write", 0.0)

    run_sentinel.touch_activity("model_load", "omnivoice-tts")

    assert _emitted_context(capsys)[-1]["kind"] == "model_load"
