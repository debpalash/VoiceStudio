"""Transient storage faults preserve accepted diagnostics until durable publication."""
import errno
import importlib
import json
import os
import stat
import threading
from pathlib import Path

import pytest

from test_diagnostic_managed_handoff import managed_reporter  # noqa: F401
from test_diagnostic_reports import reporter  # noqa: F401


def test_atomic_publication_syncs_file_before_rename_and_parent_after(tmp_path, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    events = []
    sync, replace = os.fsync, os.replace
    def tracked_sync(fd):
        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return sync(fd)
    def tracked_replace(source, target):
        events.append("rename")
        return replace(source, target)
    monkeypatch.setattr(os, "fsync", tracked_sync)
    monkeypatch.setattr(os, "replace", tracked_replace)
    reports._atomic(tmp_path / "revoked.json", {"version": 1})
    assert events == ["file", "rename"] + (["directory"] if os.name != "nt" else [])


@pytest.mark.parametrize("fail_directory", [False, True])
def test_atomic_sync_failure_propagates_and_closes_owned_descriptors(tmp_path, monkeypatch, fail_directory):
    if fail_directory and os.name == "nt":
        pytest.skip("Windows has no supported directory fsync")
    reports = importlib.import_module("core.diagnostic_reports")
    sync = os.fsync
    failed_fds = []
    def fail_sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) == fail_directory:
            failed_fds.append(fd)
            raise OSError("controlled durability failure")
        return sync(fd)
    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(OSError, match="controlled durability failure"):
        reports._atomic(tmp_path / "revoked.json", {"version": 1})
    assert len(failed_fds) == 1
    with pytest.raises(OSError):
        os.fstat(failed_fds[0])
    assert (tmp_path / "revoked.json").exists() == fail_directory
    assert not list(tmp_path.glob(".diagnostic-*"))


@pytest.mark.parametrize("coalesce", [False, True])
def test_post_rename_sync_failure_does_not_count_a_retained_capture_twice(reporter, monkeypatch, coalesce):
    if os.name == "nt":
        pytest.skip("Directory sync is POSIX-only")
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True); reporter._tick()
    if coalesce:
        assert reporter.capture("engine_failed", stage="engine")
        reporter._tick()
    sync = os.fsync
    blocked = True
    def fail_directory_sync(fd):
        if blocked and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("rename visible but directory publication not synced")
        return sync(fd)
    monkeypatch.setattr(os, "fsync", fail_directory_sync)
    assert reporter.capture("engine_failed", stage="engine")
    reporter._tick()
    assert reporter.intake.qsize() == 1
    visible = reporter._files()
    assert sum(record["props"]["occurrences"] for record in visible) == 1 + int(coalesce)
    blocked = False
    reporter.storage_retry_at = 0
    reporter._tick()
    saved = reporter._files()
    assert sum(record["props"]["occurrences"] for record in saved) == 1 + int(coalesce)
    assert reporter.intake.empty()


@pytest.mark.parametrize("elapsed_ms,expected_records", [(500, 1), (2000, 2)])
@pytest.mark.parametrize("same_error", [False, True])
def test_coalescing_respects_configured_window_without_renewing_evidence(reporter, monkeypatch, elapsed_ms, expected_records, same_error):
    reports = importlib.import_module("core.diagnostic_reports")
    clock = [reports.time.time()]
    monotonic = [100.0]
    monkeypatch.setattr(reports.time, "time", lambda: clock[0])
    monkeypatch.setattr(reports.time, "monotonic", lambda: monotonic[0])
    reporter.consent(True)
    reporter.schema["limits"]["dedupe_ms"] = 1000
    error = RuntimeError() if same_error else None
    assert reporter.capture("engine_failed", stage="engine", error=error)
    reporter._tick()
    old, = reporter._files()
    clock[0] += elapsed_ms / 1000
    monotonic[0] += elapsed_ms / 1000
    assert reporter.capture("engine_failed", stage="engine", error=error)
    reporter._tick()
    saved = reporter._files()
    assert len(saved) == expected_records
    retained = next(row for row in saved if row["id"] == old["id"])
    assert retained["timestamp_ms"] == old["timestamp_ms"]
    assert retained["props"]["occurrences"] == (2 if expected_records == 1 and not same_error else 1)
    if expected_records == 2:
        fresh, = [row for row in saved if row["id"] != old["id"]]
        clock[0] = (old["timestamp_ms"] + reporter.schema["limits"]["ttl_ms"] + 1) / 1000
        reporter._tick()
        remaining, = reporter._files()
        assert remaining["id"] == fresh["id"]
        assert remaining["timestamp_ms"] == fresh["timestamp_ms"]


@pytest.fixture(params=[False, True], ids=["standalone", "managed"])
def storage_reporter(request, reporter):
    if request.param:
        return request.getfixturevalue("managed_reporter")
    reporter.consent(True)
    reporter._tick()
    return reporter


def worker_tick(instance):
    # The real worker isolates storage exceptions from application execution.
    try:
        instance._tick()
    except OSError:
        pass


@pytest.mark.parametrize("fail_after_rename", [False, True])
def test_recovered_crash_keeps_its_marker_until_durable_publication(reporter, monkeypatch, fail_after_rename):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import run_sentinel
    reporter.consent(True)
    monkeypatch.setattr(run_sentinel, "_previous_run_alive", lambda *args: False)
    previous = {"version": 1, "epoch": reporter.epoch, "pid": 42,
                "started_at": reports.time.time() - 15, "last_active_at": reports.time.time() - 5,
                "app_version": "0.5.8"}
    marker = reporter.root / "session.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(previous), encoding="utf-8")
    original = marker.read_bytes()
    atomic = reports._atomic
    blocked = True
    attempts = []

    def fail_outbox(path, value):
        if path.parent == reporter.directory and blocked:
            attempts.append(path)
            if fail_after_rename:
                atomic(path, value)
            raise OSError("controlled recovered-crash publication failure")
        return atomic(path, value)

    monkeypatch.setattr(reports, "_atomic", fail_outbox)
    worker_tick(reporter)
    assert attempts, "recovery must reach actual durable publication"
    assert marker.read_bytes() == original, "unsaved crash evidence was replaced"
    assert not reporter.marker_owned
    worker_tick(reporter)
    assert len(attempts) == 1, "recovery must respect the storage retry deadline"

    # A new reporter has none of the previous process's in-memory capture state.
    blocked = False
    restarted = reports.Reporter()
    restarted.schema = reports._schema()
    try:
        worker_tick(restarted)
        saved = restarted._files()
        assert len(saved) == 1
        assert saved[0]["props"]["category"] == "backend_crash"
        assert saved[0]["props"]["occurrences"] == 1
        assert saved[0]["props"]["app_version"] == previous["app_version"]
        assert saved[0]["timestamp_ms"] == int(previous["last_active_at"] * 1000)
        if fail_after_rename:
            assert saved[0]["id"] == attempts[0].stem
        assert json.loads(marker.read_text(encoding="utf-8"))["pid"] == os.getpid()
        assert restarted.marker_owned
    finally:
        restarted.shutdown()


def test_full_outbox_defers_recovered_marker_without_exceeding_capacity(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import run_sentinel
    reporter.consent(True)
    assert reporter.capture("download_failed", stage="download")
    reporter._tick()
    first, = reporter._files()
    monkeypatch.setattr(run_sentinel, "_previous_run_alive", lambda *args: False)
    marker = reporter.root / "session.json"
    previous = {"epoch": reporter.epoch, "pid": 42, "started_at": reports.time.time() - 15}
    marker.write_text(json.dumps(previous), encoding="utf-8")
    original = marker.read_bytes()
    restarted = reports.Reporter()
    restarted.schema = reports._schema()
    restarted.schema["limits"]["queue_count"] = 1
    try:
        worker_tick(restarted)
        assert restarted._files() == [first]
        assert marker.read_bytes() == original
        assert not restarted.marker_owned
        (restarted.directory / (first["id"] + ".json")).unlink()
        worker_tick(restarted)
        recovered, = restarted._files()
        assert recovered["props"]["category"] == "backend_crash"
        assert recovered["props"]["occurrences"] == 1
        assert restarted.marker_owned
    finally:
        restarted.shutdown()


def choice_thread(instance, enabled):
    results, failures, completed = [], [], threading.Event()

    def run():
        try:
            results.append(instance.consent(enabled))
        except OSError as error:
            failures.append(error)
        finally:
            completed.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results, failures, completed


@pytest.mark.parametrize("save_fails", [False, True])
def test_withdrawal_cancels_immediately_but_completes_only_after_persistence(reporter, monkeypatch, save_fails):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import analytics_transport, prefs
    reporter.consent(True)
    old_epoch = reporter.epoch
    reporter._tick()
    entered, release = threading.Event(), threading.Event()
    sending, cancelled, withdrawn = threading.Event(), threading.Event(), threading.Event()
    atomic, save = reports._atomic, prefs._save

    def blocked_atomic(path, value):
        if path.parent == reporter.directory:
            entered.set()
            assert release.wait(5), "test did not release controlled outbox storage"
        if save_fails and path.name == "revoked.json":
            raise PermissionError("controlled revocation failure")
        return atomic(path, value)

    def save_choice(data):
        if save_fails and not data["diagnostics_enabled"]:
            raise PermissionError("controlled OFF persistence failure")
        return save(data)

    def blocked_send(transport, body):
        sending.set()
        assert transport._stopped.wait(5), "withdrawal did not cancel the independent sender"
        cancelled.set()
        return 0

    monkeypatch.setattr(reports, "_atomic", blocked_atomic)
    monkeypatch.setattr(prefs, "_save", save_choice)
    monkeypatch.setattr(analytics_transport.SupermetryTransport, "_send", blocked_send)
    transport = analytics_transport.SupermetryTransport(
        {"site": "testdestination", "host": "https://collector.example", "appUrl": "https://app.example"},
        lambda: reporter.enabled and reporter.epoch == old_epoch,
        on_result=lambda identity, status: reporter._delivery_result(old_epoch, identity, status),
    )
    reporter.transport = transport
    assert transport.capture("diagnostic_report", {}, reporter.profile)
    assert sending.wait(5)
    assert reporter.capture("engine_failed", stage="engine")
    reporter.start()
    assert entered.wait(5)
    result, failures = [], []

    def withdraw():
        try:
            result.append(reporter.consent(False))
        except OSError as error:
            failures.append(error)
        finally:
            withdrawn.set()

    choice = threading.Thread(target=withdraw, daemon=True)
    choice.start()
    try:
        assert cancelled.wait(5), "OFF left the independent sender active"
        assert not withdrawn.is_set(), "OFF reported success before storage could persist it"
        assert not reporter.capture("generation_failed")
        assert not transport.capture("diagnostic_report", {}, reporter.profile)
        assert reporter.epoch != old_epoch
    finally:
        release.set()
        choice.join(5)
        transport.shutdown()
        monkeypatch.setattr(prefs, "_save", save)
        monkeypatch.setattr(reports, "_atomic", atomic)
    assert withdrawn.is_set()
    if save_fails:
        assert not result and len(failures) == 1 and isinstance(failures[0], PermissionError)
        assert not reporter.enabled and reporter.consent_failed
        reporter.consent(False)  # Explicit retry after the reported failure.
    else:
        assert not failures and result[0]["enabled"] is False
        assert result[0]["grant_epoch"] is None
    with reporter.lock:
        reporter._tick()
        assert not reporter.enabled and reporter.intake.empty() and reporter.acks.empty()
        assert list(reporter.directory.glob("*.json")) == []
        assert prefs.get("diagnostics_enabled") is False
        reporter.consent(True)
        assert reporter.epoch != old_epoch
        assert reporter.capture("download_failed", stage="download")
        reporter._tick()
        saved, = reporter._files()
        assert saved["epoch"] == reporter.epoch
        assert saved["props"]["category"] == "download_failed"
    reporter.consent(False)
    reporter.shutdown()
    restarted = reports.Reporter()
    restarted.schema = reports._schema()
    try:
        restarted.refresh()
        assert not restarted.enabled, "an acknowledged OFF was lost across shutdown"
    finally:
        restarted.shutdown()


def test_withdrawal_fences_a_worker_consent_read_already_in_progress(reporter, monkeypatch):
    from core import prefs
    reporter.consent(True)
    old_epoch = reporter.epoch
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    load = prefs._load
    saved = load()

    def blocked_load(**options):
        if threading.current_thread().name == "blocked-diagnostic-read":
            entered.set()
            assert release.wait(5)
            return dict(saved)
        return load(**options)

    def tick():
        with reporter.lock:
            reporter._tick()

    monkeypatch.setattr(prefs, "_load", blocked_load)
    worker = threading.Thread(target=tick, name="blocked-diagnostic-read", daemon=True)
    worker.start()
    assert entered.wait(5)

    class Transport:
        def shutdown(self):
            cancelled.set()

    reporter.transport = Transport()
    choice, results, failures, completed = choice_thread(reporter, False)
    try:
        assert cancelled.wait(5)
        assert not completed.is_set()
        assert reporter.epoch != old_epoch
        assert not reporter.capture("generation_failed")
    finally:
        release.set()
        worker.join(5)
        choice.join(5)
    assert not worker.is_alive()
    assert completed.is_set() and not failures and results[0]["enabled"] is False
    assert not reporter.enabled and reporter.epoch is None
    reporter._tick()
    assert prefs.get("diagnostics_enabled") is False
    reporter.consent(True)
    assert reporter.enabled and reporter.epoch != old_epoch


@pytest.mark.parametrize("save_fails", [False, True])
def test_withdrawal_during_enable_write_preserves_existing_revocation(reporter, monkeypatch, save_fails):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(False)
    revoked = reporter.root / "revoked.json"
    original = revoked.read_bytes()
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    save, atomic = prefs._save, reports._atomic
    result = []

    def blocked_enable(data):
        if save_fails and not data["diagnostics_enabled"]:
            raise PermissionError("controlled OFF preferences failure")
        save(data)
        if data["diagnostics_enabled"]:
            entered.set()
            assert release.wait(5)

    def fail_revocation(path, value):
        if save_fails and path.name == "revoked.json":
            raise PermissionError("controlled OFF marker failure")
        return atomic(path, value)

    class Transport:
        def shutdown(self):
            cancelled.set()

    monkeypatch.setattr(prefs, "_save", blocked_enable)
    monkeypatch.setattr(reports, "_atomic", fail_revocation)
    worker = threading.Thread(target=lambda: result.append(reporter.consent(True)), daemon=True)
    worker.start()
    assert entered.wait(5)
    reporter.transport = Transport()
    choice, results, failures, completed = choice_thread(reporter, False)
    try:
        assert cancelled.wait(5)
        assert not completed.is_set()
        assert not reporter.capture("generation_failed")
    finally:
        release.set()
        worker.join(5)
        choice.join(5)
    assert not worker.is_alive() and result[0]["enabled"] is False
    assert revoked.read_bytes() == original, "superseded ON removed the durable OFF marker"
    assert completed.is_set() and not reporter.enabled
    if save_fails:
        assert not results and len(failures) == 1
        restarted = reports.Reporter()
        restarted.schema = reports._schema()
        try:
            restarted.refresh()
            assert not restarted.enabled
        finally:
            restarted.shutdown()
    else:
        assert not failures and results[0]["enabled"] is False
        assert prefs.get("diagnostics_enabled") is False


@pytest.mark.parametrize("save_fails", [False, True])
def test_withdrawal_overlapping_on_unlink_completes_durably_or_reports_failure(reporter, monkeypatch, save_fails):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(False)
    revoked = reporter.root / "revoked.json"
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    unlink, atomic, save = Path.unlink, reports._atomic, prefs._save

    def blocked_unlink(path, *args, **kwargs):
        if path == revoked and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return unlink(path, *args, **kwargs)

    def controlled_atomic(path, value):
        if save_fails and cancelled.is_set():
            raise PermissionError("controlled OFF marker restoration failure")
        return atomic(path, value)

    def controlled_save(data):
        if save_fails and not data["diagnostics_enabled"]:
            raise PermissionError("controlled OFF preference failure")
        return save(data)

    class Transport:
        def shutdown(self):
            cancelled.set()

    monkeypatch.setattr(Path, "unlink", blocked_unlink)
    monkeypatch.setattr(reports, "_atomic", controlled_atomic)
    monkeypatch.setattr(prefs, "_save", controlled_save)
    enable, enabled_results, enable_failures, enabled_done = choice_thread(reporter, True)
    assert entered.wait(5)
    reporter.transport = Transport()
    disable, results, failures, disabled_done = choice_thread(reporter, False)
    try:
        assert cancelled.wait(5)
        assert not disabled_done.is_set()
        assert not reporter.enabled and not reporter.capture("generation_failed")
    finally:
        release.set()
        enable.join(5)
        disable.join(5)
        monkeypatch.setattr(reports, "_atomic", atomic)
        monkeypatch.setattr(prefs, "_save", save)
    assert enabled_done.is_set() and disabled_done.is_set()
    if save_fails:
        # OFF never succeeded while every durable write failed after an
        # already-started unlink. Both callers receive the storage failure.
        assert not enabled_results and len(enable_failures) == 1
        assert not results and len(failures) == 1
        assert not reporter.enabled and reporter.consent_failed
        reporter._tick()
        assert not reporter.enabled
        reporter.consent(False)  # A later explicit retry can now persist it.
    else:
        assert not enable_failures and enabled_results[0]["enabled"] is False
        assert not failures and results[0]["enabled"] is False
    restarted = reports.Reporter()
    restarted.schema = reports._schema()
    try:
        restarted.refresh()
        assert not restarted.enabled
    finally:
        restarted.shutdown()


def test_withdrawal_during_record_validation_does_not_requeue_old_transport(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    assert reporter.capture("engine_failed", stage="engine")
    reporter._tick()
    sending = False
    attempts = []
    persist, validate = reporter._persist_intake, reports.validate_record

    class Transport:
        stopped = False

        def shutdown(self):
            self.stopped = True

        def capture(self, *args, **kwargs):
            attempts.append(args)
            return not self.stopped

    transport = reporter.transport = Transport()

    def persist_before_send():
        nonlocal sending
        records = persist()
        sending = True
        return records

    def withdraw_after_validation(value, epoch, schema=None):
        safe = validate(value, epoch, schema)
        if sending:
            reporter.consent(False)
        return safe

    monkeypatch.setattr(reporter, "_persist_intake", persist_before_send)
    monkeypatch.setattr(reports, "validate_record", withdraw_after_validation)
    reporter._tick()
    assert not reporter.enabled and transport.stopped
    assert attempts == [] and reporter.inflight == {}


@pytest.mark.parametrize("location", ["outbox", "session"])
def test_deeply_nested_record_is_rejected_without_blocking_fresh_publication(reporter, location):
    reporter.consent(True)
    reporter.directory.mkdir(parents=True, exist_ok=True)
    malformed = reporter.directory / ("a" * 32 + ".json") if location == "outbox" else reporter.root / "session.json"
    malformed.write_text("[" * 1500 + "]" * 1500, encoding="utf-8")
    assert reporter.capture("engine_failed", stage="engine")
    reporter._tick()
    if location == "outbox":
        assert not malformed.exists()
    else:
        assert json.loads(malformed.read_text(encoding="utf-8"))["epoch"] == reporter.epoch
    saved, = reporter._files()
    assert saved["props"]["category"] == "engine_failed"


def test_consumed_managed_file_disappearing_does_not_pause_fresh_intake(managed_reporter, monkeypatch):
    instance = managed_reporter
    assert instance.capture("engine_failed", stage="engine")
    instance._tick()
    consumed, = instance.directory.glob("*.json")
    read_text = Path.read_text

    def consume_before_read(path, *args, **kwargs):
        if path == consumed:
            path.unlink()
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", consume_before_read)
    assert instance.capture("download_failed", stage="download")
    instance._tick()
    assert instance.storage_retry_at == 0
    fresh, = instance._files()
    assert fresh["props"]["category"] == "download_failed"
    assert instance.intake.empty() and instance.dropped == 0


def test_successful_ack_frees_space_before_retained_write_retry(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    assert reporter.capture("engine_failed", stage="engine")
    reporter._tick()
    delivered, = reporter.directory.glob("*.json")
    reporter.inflight[delivered.stem] = True
    atomic = reports._atomic

    def full_until_delivered_removed(path, value):
        if path.parent == reporter.directory and delivered.exists():
            raise OSError("no space until delivered report is removed")
        return atomic(path, value)

    monkeypatch.setattr(reports, "_atomic", full_until_delivered_removed)
    assert reporter.capture("download_failed", stage="download")
    reporter._tick()
    assert reporter.intake.qsize() == 1
    reporter._delivery_result(reporter.epoch, delivered.stem, 204)
    reporter._tick()
    assert not delivered.exists(), "retained writes blocked successful delivery cleanup"
    reporter._tick()
    fresh, = reporter._files()
    assert fresh["props"]["category"] == "download_failed"
    assert reporter.intake.empty() and reporter.acks.empty()


@pytest.mark.parametrize("status", [202, 400])
def test_locked_terminal_ack_preserves_slot_without_starving_healthy_work(reporter, monkeypatch, status):
    reports = importlib.import_module("core.diagnostic_reports")
    clock, wall = [100.0], [reports.time.time()]
    monkeypatch.setattr(reports.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(reports.time, "time", lambda: wall[0])
    reporter.consent(True)
    # This accepted evidence expires during the ACK cleanup cooldown; normal
    # pruning must not bypass the same file's deletion deadline.
    stamp = int(wall[0] * 1000) - reporter.schema["limits"]["ttl_ms"] + 1000
    assert reporter.capture("engine_failed", stage="engine", _reported_timestamp_ms=stamp)
    assert reporter.capture("download_failed", stage="download")
    reporter._tick()
    records = reporter._files()
    blocked = next(row for row in records if row["props"]["category"] == "engine_failed")
    healthy = next(row for row in records if row["props"]["category"] == "download_failed")
    blocked_path = reporter.directory / (blocked["id"] + ".json")
    healthy_path = reporter.directory / (healthy["id"] + ".json")
    sent, deletes = [], []
    unlocked = False
    unlink = Path.unlink

    class Transport:
        def capture(self, *args, **options):
            sent.append(options["event_id"])
            return True

        def shutdown(self):
            pass

    def controlled_unlink(path, *args, **kwargs):
        if path == blocked_path:
            deletes.append(clock[0])
            if not unlocked:
                raise PermissionError("controlled terminal ACK deletion lock")
        return unlink(path, *args, **kwargs)

    reporter.transport = Transport()
    reporter._tick()
    assert len(sent) == 2
    reporter._delivery_result(reporter.epoch, blocked["id"], status)
    # Fill the bounded ACK queue behind the locked item. Missing files are
    # already cleaned, and the last real ACK must still make progress.
    for index in range(reporter.acks.maxsize - 2):
        reporter._delivery_result(reporter.epoch, f"{index:032x}", 202)
    reporter._delivery_result(reporter.epoch, healthy["id"], 202)
    assert reporter.acks.full()
    assert reporter.capture("operation_failed", stage="other")
    monkeypatch.setattr(Path, "unlink", controlled_unlink)
    worker_tick(reporter)
    assert not healthy_path.exists(), "a later healthy ACK was starved by the first locked file"
    assert len(sent) == 3 and sent.count(blocked["id"]) == 1
    assert reporter.intake.empty() and reporter.acks.qsize() == 1
    assert reporter.acks.peek_nowait() == (reporter.epoch, blocked["id"], status)
    assert blocked["id"] in reporter.inflight
    wall[0] += 2  # The blocked evidence is now beyond TTL.
    for value in (100.0, 101.0, 159.9):
        clock[0] = value
        reporter._tick()
    assert deletes == [100.0], "ACK/prune paths hammered a file during its cleanup cooldown"
    unlocked = True
    reporter._tick()
    assert blocked_path.exists() and deletes == [100.0]
    clock[0] = 160.0
    reporter._tick()
    assert deletes == [100.0, 160.0] and not blocked_path.exists()
    assert reporter.acks.empty() and blocked["id"] not in reporter.inflight
    assert sent.count(blocked["id"]) == 1


def test_locked_ack_deadline_cleanup_frees_full_disk_without_a_new_capture(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    clock = [100.0]
    monkeypatch.setattr(reports.time, "monotonic", lambda: clock[0])
    reporter.consent(True)
    assert reporter.capture("engine_failed", stage="engine")
    reporter._tick()
    delivered, = reporter.directory.glob("*.json")
    reporter.inflight[delivered.stem] = True
    atomic, unlink = reports._atomic, Path.unlink
    writable = False
    deletes = []

    def full_until_ack_removed(path, value):
        if path.parent == reporter.directory and delivered.exists():
            raise OSError("controlled full volume until ACK cleanup")
        return atomic(path, value)

    def blocked_unlink(path, *args, **kwargs):
        if path == delivered:
            deletes.append(clock[0])
            if not writable:
                raise PermissionError("controlled ACK cleanup lock")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(reports, "_atomic", full_until_ack_removed)
    monkeypatch.setattr(Path, "unlink", blocked_unlink)
    assert reporter.capture("download_failed", stage="download")
    reporter._tick()
    assert reporter.intake.qsize() == 1
    reporter._delivery_result(reporter.epoch, delivered.stem, 202)
    worker_tick(reporter)
    worker_tick(reporter)
    assert deletes == [100.0]
    assert reporter.intake.qsize() == 1 and reporter.acks.qsize() == 1
    # Freeing an ACK must override even a later publication retry deadline.
    reporter.storage_retry_at = 500.0
    writable = True
    clock[0] = 160.0
    reporter._tick()
    assert deletes == [100.0, 160.0]
    assert reporter.acks.empty() and reporter.intake.empty()
    fresh, = reporter._files()
    assert fresh["props"]["category"] == "download_failed"


@pytest.mark.parametrize("budget", ["count", "bytes"])
def test_pending_ack_reserves_disk_capacity_until_off_clears_its_epoch(reporter, monkeypatch, budget):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    if budget == "count":
        reporter.schema["limits"]["queue_count"] = 1
    assert reporter.capture("engine_failed", stage="engine")
    reporter._tick()
    blocked, = reporter.directory.glob("*.json")
    if budget == "bytes":
        # Either ordinary record fits alone; both together exceed the actual
        # occupied-byte budget even though the terminal one is not sendable.
        reporter.schema["limits"]["queue_bytes"] = blocked.stat().st_size + 64
    reporter.inflight[blocked.stem] = True
    reporter._delivery_result(reporter.epoch, blocked.stem, 202)
    unlink = Path.unlink

    def denied(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("controlled retained ACK file")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    worker_tick(reporter)
    assert reporter.capture("download_failed", stage="download")
    worker_tick(reporter)
    assert reporter.intake.empty(), "known occupied capacity should be accounted, not indefinitely stall intake"
    assert len(list(reporter.directory.glob("*.json"))) == 1
    assert reporter.dropped == 1
    old = reporter.epoch
    reporter.consent(False)
    assert reporter.acks.empty() and not reporter.capture("generation_failed")
    assert not reporter.ack_cleanup_at
    assert reporter.ack_cleanup_count == reporter.ack_cleanup_bytes == 0
    monkeypatch.setattr(Path, "unlink", unlink)
    reporter.consent(True)
    assert reporter.epoch != old
    assert reporter.capture("generation_failed")
    reporter._tick()
    fresh, = reporter._files()
    assert fresh["epoch"] == reporter.epoch and fresh["props"]["category"] == "generation_failed"


def test_unread_report_survives_transient_io_and_reloads(storage_reporter, monkeypatch):
    instance = storage_reporter
    reports = importlib.import_module("core.diagnostic_reports")
    assert instance.capture("download_failed", stage="download")
    instance._tick()
    path, = instance.directory.glob("*.json")
    original = path.read_bytes()
    read_text = Path.read_text
    unreadable = True
    clock = [100.0]
    monkeypatch.setattr(reports.time, "monotonic", lambda: clock[0])

    def controlled_read(candidate, *args, **kwargs):
        if candidate == path and unreadable:
            raise PermissionError("temporary report read lock")
        return read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", controlled_read)
    worker_tick(instance)
    assert path.exists(), "a readable-later report must not be deleted as corrupt"
    assert path.read_bytes() == original
    assert instance.dropped == 0
    unreadable = False
    clock[0] += 60
    instance._tick()
    assert instance._files() == [json.loads(original)]


def test_failed_publication_retains_bounded_intake_and_causal_identity(storage_reporter, monkeypatch):
    instance = storage_reporter
    reports = importlib.import_module("core.diagnostic_reports")
    atomic = reports._atomic
    writable = False
    attempts = []
    clock = [100.0]
    monkeypatch.setattr(reports.time, "monotonic", lambda: clock[0])

    def controlled_atomic(path, value):
        if path.parent == instance.directory:
            attempts.append(path)
            if not writable:
                raise OSError("temporary full diagnostic storage")
        return atomic(path, value)

    monkeypatch.setattr(reports, "_atomic", controlled_atomic)
    error = RuntimeError("never persist this private message")
    assert instance.capture("engine_failed", stage="engine", error=error)
    worker_tick(instance)
    assert instance.intake.qsize() == 1, "accepted capture vanished before publication"
    assert list(instance.directory.glob("*.json")) == []
    assert len(attempts) == 1
    for _ in range(65):
        instance.capture("engine_failed", stage="engine", error=error)
    assert instance.intake.qsize() == 64
    assert instance.dropped == 2
    clock[0] += 59
    worker_tick(instance)
    assert len(attempts) == 1, "storage recovery must not busy-retry"
    writable = True
    clock[0] += 1
    instance._tick()
    saved = instance._files()
    assert len(saved) == 1 and saved[0]["props"]["occurrences"] == 1
    assert "private" not in json.dumps(saved)
    assert instance.intake.empty()


def test_failed_coalescing_retries_exactly_once(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    assert reporter.capture("engine_failed", stage="engine")
    reporter._tick()
    path, = reporter.directory.glob("*.json")
    original = path.read_bytes()
    atomic = reports._atomic
    writable = False
    clock = [100.0]
    monkeypatch.setattr(reports.time, "monotonic", lambda: clock[0])

    def controlled_atomic(candidate, value):
        if candidate == path and not writable:
            raise OSError("temporary outbox replacement lock")
        return atomic(candidate, value)

    monkeypatch.setattr(reports, "_atomic", controlled_atomic)
    assert reporter.capture("engine_failed", stage="engine", error=RuntimeError("private"))
    # Keep the same projected error class as the original capture for coalescing.
    assert reporter.capture("engine_failed", stage="engine")
    worker_tick(reporter)
    assert path.read_bytes() == original
    assert reporter.intake.qsize() >= 1
    writable = True
    clock[0] += 60
    reporter._tick()
    saved = reporter._files()
    assert sum(row["props"]["occurrences"] for row in saved) == 3
    assert next(row for row in saved if row["id"] == path.stem)["props"]["occurrences"] == 2


def test_failed_publication_cannot_borrow_fresh_managed_grant(managed_reporter, monkeypatch):
    instance = managed_reporter
    reports = importlib.import_module("core.diagnostic_reports")
    atomic = reports._atomic
    writable = False

    def controlled_atomic(path, value):
        if path.parent == instance.directory and not writable:
            raise OSError("temporary inbox write lock")
        return atomic(path, value)

    monkeypatch.setattr(reports, "_atomic", controlled_atomic)
    assert instance.capture("engine_failed", stage="engine")
    worker_tick(instance)
    assert instance.intake.qsize() == 1
    consent_path = instance.root / "consent.json"
    consent = json.loads(consent_path.read_text(encoding="utf-8"))
    consent.update(enabled=False, epoch="c" * 32)
    consent_path.write_text(json.dumps(consent), encoding="utf-8")
    instance._tick()
    assert instance.intake.empty()
    consent.update(enabled=True, epoch="d" * 32)
    consent_path.write_text(json.dumps(consent), encoding="utf-8")
    instance._tick()
    writable = True
    assert instance.capture("download_failed", stage="download")
    instance._tick()
    saved, = instance._files()
    assert saved["epoch"] == "d" * 32
    assert saved["props"]["category"] == "download_failed"


def test_worker_retries_without_new_capture_and_sends_only_committed_bytes(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    reporter._tick()
    atomic = reports._atomic
    failed = threading.Event()
    writable = threading.Event()
    delivered = threading.Event()
    clock = [100.0]
    sent = []
    monkeypatch.setattr(reports.time, "monotonic", lambda: clock[0])

    def controlled_atomic(path, value):
        if path.parent == reporter.directory and not writable.is_set():
            failed.set()
            raise OSError("temporary outbox lock")
        return atomic(path, value)

    class Transport:
        def capture(self, event, props, profile, **options):
            saved = json.loads((reporter.directory / (options["event_id"] + ".json")).read_text(encoding='utf-8'))
            assert saved["props"] == props
            sent.append(saved)
            delivered.set()
            return True

        def shutdown(self):
            pass

    monkeypatch.setattr(reports, "_atomic", controlled_atomic)
    reporter.transport = Transport()
    assert reporter.capture("download_failed", stage="download")
    reporter.start()
    assert failed.wait(5)
    with reporter.lock:
        assert reporter.intake.qsize() == 1
        assert not sent and reporter.storage_retry_at == 160
        writable.set()
        clock[0] = 160
    assert delivered.wait(5), "the application worker did not wake without a new event"
    with reporter.lock:
        assert len(sent) == 1 and reporter.intake.empty()


@pytest.mark.parametrize("control", ["off", "kill", "close"])
def test_storage_retry_does_not_survive_revocation_or_close(reporter, monkeypatch, control):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    reporter._tick()
    atomic = reports._atomic
    writable = False

    def controlled_atomic(path, value):
        if path.parent == reporter.directory and not writable:
            raise OSError("temporary outbox lock")
        return atomic(path, value)

    monkeypatch.setattr(reports, "_atomic", controlled_atomic)
    assert reporter.capture("engine_failed", stage="engine")
    worker_tick(reporter)
    assert reporter.intake.qsize() == 1
    if control == "kill":
        monkeypatch.setenv("OMNIVOICE_DIAGNOSTICS_DISABLED", "1")
        reporter._tick()
        monkeypatch.delenv("OMNIVOICE_DIAGNOSTICS_DISABLED")
    elif control == "off":
        reporter.consent(False)
        reporter._tick()
    else:
        reporter.shutdown()
        writable = True
        assert not reporter.capture("download_failed", stage="download")
        assert reporter._files() == []
        return
    assert reporter.intake.empty() and reporter._files() == []
    reporter.consent(True)
    writable = True
    assert reporter.capture("download_failed", stage="download")
    reporter._tick()
    saved, = reporter._files()
    assert saved["props"]["category"] == "download_failed"


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("number", [errno.EIO, errno.EACCES])
def test_unread_consent_closes_delivery_preserves_evidence_and_retries(request, monkeypatch, managed, number):
    import builtins
    instance = request.getfixturevalue("managed_reporter" if managed else "reporter")
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    clock = [100.0]
    monkeypatch.setattr(reports.time, "monotonic", lambda: clock[0])
    if not managed:
        instance.consent(True)
    assert instance.capture("engine_failed", stage="engine")
    instance._tick()
    report, = instance.directory.glob("*.json")
    original = report.read_bytes()
    marker = instance.root / "session.json"
    marker_bytes = marker.read_bytes() if not managed else None
    old_epoch = instance.epoch
    assert instance.capture("download_failed", stage="download")
    target = instance.root / "consent.json" if managed else Path(prefs._PREFS_PATH)
    attempts, cancelled = [], []
    unlocked = False
    read_text, open_file = Path.read_text, builtins.open

    class Transport:
        def shutdown(self):
            cancelled.append(True)

    if not managed:
        instance.transport = Transport()

    def fail_read():
        attempts.append(clock[0])
        raise OSError(number, "controlled authority read failure")

    def controlled_text(path, *args, **kwargs):
        if path == target and not unlocked:
            fail_read()
        return read_text(path, *args, **kwargs)

    def controlled_open(path, mode="r", *args, **kwargs):
        if str(path) == str(target) and mode == "r" and not unlocked:
            fail_read()
        return open_file(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", controlled_text)
    monkeypatch.setattr(builtins, "open", controlled_open)
    instance._tick()
    assert not instance.enabled and not instance.capture("generation_failed")
    assert report.exists() and report.read_bytes() == original
    if not managed:
        assert marker.read_bytes() == marker_bytes and cancelled == [True]
    for now in (100.0, 101.0, 159.9):
        clock[0] = now
        instance._tick()
    assert attempts == [100.0], "unread consent was retried on every worker tick"
    unlocked = True
    clock[0] = 160.0
    instance._tick()
    assert instance.enabled and instance.epoch == old_epoch
    rows = instance._files()
    assert {row["props"]["category"] for row in rows} == {"engine_failed", "download_failed"}
    assert any(row["id"] == report.stem for row in rows)
    assert instance.intake.empty()


@pytest.mark.parametrize("number", [errno.EIO, errno.EACCES])
@pytest.mark.parametrize("operation", ["on", "off", "set", "mapping", "delete"])
def test_unread_preferences_abort_mutations_without_replacing_other_settings(reporter, monkeypatch, number, operation):
    import builtins
    from core import prefs
    prefs.set_("tts_backend", "user_engine")
    prefs.set_("engine_options", {"keep": True})
    if operation == "off":
        reporter.consent(True)
    target = Path(prefs._PREFS_PATH)
    original = target.read_bytes()
    open_file = builtins.open

    def denied(path, mode="r", *args, **kwargs):
        if str(path) == str(target) and mode == "r":
            raise OSError(number, "controlled shared prefs read failure")
        return open_file(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", denied)
        action = {"on": lambda: reporter.consent(True), "off": lambda: reporter.consent(False),
                  "set": lambda: prefs.set_("new", True), "mapping": lambda: prefs.update_mapping("engine_options", {"new": True}),
                  "delete": lambda: prefs.delete("tts_backend")}[operation]
        with pytest.raises(OSError):
            action()
        assert target.read_bytes() == original
        # Ordinary read-only preferences remain forgiving, as before.
        assert prefs.get("tts_backend", "fallback") == "fallback"
        if operation in {"on", "off"}:
            assert not reporter.enabled and reporter.consent_failed
        if operation == "off":
            assert (reporter.root / "revoked.json").exists()
    if operation == "off":
        reports = importlib.import_module("core.diagnostic_reports")
        restarted = reports.Reporter(); restarted.schema = reports._schema()
        try:
            restarted._tick()
            assert not restarted.enabled
        finally:
            restarted.shutdown()


@pytest.mark.parametrize("number", [errno.EIO, errno.EACCES])
def test_hard_kill_with_unread_preferences_retires_grant_before_read_recovery(reporter, monkeypatch, number):
    import builtins
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(True)
    assert reporter.capture("engine_failed")
    reporter._tick()
    target = Path(prefs._PREFS_PATH)
    original = target.read_bytes()
    open_file = builtins.open
    with monkeypatch.context() as patch:
        def denied(path, mode="r", *args, **kwargs):
            if str(path) == str(target) and mode == "r":
                raise OSError(number, "controlled killed grant read failure")
            return open_file(path, mode, *args, **kwargs)
        patch.setattr(builtins, "open", denied)
        patch.setenv("OMNIVOICE_DIAGNOSTICS_DISABLED", "1")
        reporter._tick()
        assert not reporter.enabled and (reporter.root / "revoked.json").exists()
        assert target.read_bytes() == original
        assert not list(reporter.directory.glob("*.json"))
    restarted = reports.Reporter(); restarted.schema = reports._schema()
    try:
        restarted._tick()
        assert not restarted.enabled and not restarted.capture("engine_failed")
    finally:
        restarted.shutdown()


@pytest.mark.parametrize("body", [None, "{broken", "[]"])
def test_missing_or_proven_corrupt_preferences_still_allow_explicit_initialization(reporter, body):
    from core import prefs
    target = Path(prefs._PREFS_PATH)
    if body is not None:
        target.write_text(body, encoding="utf-8")
    reporter._tick()
    assert not reporter.enabled and reporter.epoch is None
    assert not target.exists() if body is None else target.read_text(encoding="utf-8") == body
    prefs.set_("unrelated", "keep")
    reporter.consent(True)
    assert reporter.enabled and prefs.get("unrelated") == "keep"


def test_unread_consent_recovery_retains_exact_pending_publication(reporter, monkeypatch):
    import builtins
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    clock = [100.0]
    monkeypatch.setattr(reports.time, "monotonic", lambda: clock[0])
    reporter.consent(True)
    reporter._tick()
    atomic = reports._atomic
    failed = False

    def committed_but_sync_failed(path, value):
        nonlocal failed
        atomic(path, value)
        if path.parent == reporter.directory and not failed:
            failed = True
            raise OSError(errno.EIO, "controlled post-rename publication error")

    monkeypatch.setattr(reports, "_atomic", committed_but_sync_failed)
    assert reporter.capture("engine_failed", stage="engine")
    reporter._tick()
    saved, = reporter.directory.glob("*.json")
    assert reporter.publication is not None and reporter.intake.qsize() == 1
    publication = reporter.publication
    target = Path(prefs._PREFS_PATH)
    open_file = builtins.open
    with monkeypatch.context() as patch:
        def denied(path, mode="r", *args, **kwargs):
            if str(path) == str(target) and mode == "r":
                raise PermissionError(errno.EACCES, "controlled consent suspension")
            return open_file(path, mode, *args, **kwargs)
        patch.setattr(builtins, "open", denied)
        reporter._tick()
        assert not reporter.enabled and reporter.publication is publication
        assert saved.exists()
    clock[0] = 160.0
    reporter._tick()
    rows = reporter._files()
    assert len(rows) == 1 and rows[0]["id"] == saved.stem
    assert rows[0]["props"]["occurrences"] == 1
    assert reporter.publication is None and reporter.intake.empty()


def test_hard_kill_without_any_preferences_does_not_create_a_grant(reporter, monkeypatch):
    from core import prefs
    monkeypatch.setenv("OMNIVOICE_DIAGNOSTICS_DISABLED", "1")
    reporter._tick()
    assert not reporter.enabled and reporter.epoch is None and reporter.profile is None
    assert not Path(prefs._PREFS_PATH).exists()
    assert not reporter.capture("engine_failed")


@pytest.mark.parametrize("policy_error", [FileNotFoundError, ValueError])
def test_worker_retries_killed_revocation_without_a_reporting_policy(reporter, monkeypatch, policy_error):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    from types import SimpleNamespace
    reporter.consent(True)
    clock, attempts = [100.0], []
    failed, recovered, writable = threading.Event(), threading.Event(), threading.Event()
    atomic, save, schema = reports._atomic, prefs._save, reporter.schema
    monkeypatch.setattr(reports, "time", SimpleNamespace(time=reports.time.time, monotonic=lambda: clock[0]))

    def policy():
        raise policy_error("controlled unavailable reporting policy")

    def fence(path, value):
        if path.name == "revoked.json":
            attempts.append(clock[0])
            if not writable.is_set():
                raise PermissionError(errno.EACCES, "controlled unavailable fence storage")
        result = atomic(path, value)
        if path.name == "revoked.json":
            recovered.set()
        return result

    def preferences(value):
        if not writable.is_set():
            failed.set()
            raise OSError(errno.EIO, "controlled unavailable preference storage")
        return save(value)

    monkeypatch.setattr(reports, "_schema", policy)
    monkeypatch.setattr(reports, "_atomic", fence)
    monkeypatch.setattr(prefs, "_save", preferences)
    monkeypatch.setenv("OMNIVOICE_DIAGNOSTICS_DISABLED", "1")
    instance = reports.Reporter()
    instance.start()
    try:
        assert failed.wait(5)
        with instance.lock:
            assert attempts == [100.0]
            assert not instance.capture("backend_crash") and instance.transport is None
            writable.set()
            clock[0] = 160.0
        assert recovered.wait(5), "policy failure terminated the durable withdrawal retry worker"
        with instance.lock:
            assert attempts == [100.0, 160.0]
            assert not instance.enabled and not instance.capture("backend_crash")
    finally:
        instance.stop.set()
        instance.thread.join(2)
    assert not instance.thread.is_alive()
    monkeypatch.delenv("OMNIVOICE_DIAGNOSTICS_DISABLED")
    monkeypatch.setattr(reports, "_schema", lambda: schema)
    restarted = reports.Reporter()
    restarted.schema = schema
    restarted.refresh()
    assert not restarted.enabled and not restarted.capture("backend_crash")
    assert prefs.get("diagnostics_enabled") is False


@pytest.mark.parametrize("existing_grant", [False, True])
def test_policy_failure_worker_preserves_unknown_evidence_without_creating_authority(reporter, monkeypatch, existing_grant):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    if existing_grant:
        reporter.consent(True)
        assert reporter.capture("engine_failed")
        reporter._tick()
    else:
        monkeypatch.setenv("OMNIVOICE_DIAGNOSTICS_DISABLED", "1")
    before = {path: path.read_bytes() for path in reporter.root.rglob("*") if path.is_file()}
    loaded = threading.Event()

    def policy():
        loaded.set()
        raise ValueError("controlled corrupt reporting policy")

    monkeypatch.setattr(reports, "_schema", policy)
    instance = reports.Reporter()
    instance.start()
    try:
        assert loaded.wait(5)
        assert not instance.capture("backend_crash") and instance.transport is None
        assert {path: path.read_bytes() for path in reporter.root.rglob("*") if path.is_file()} == before
        if not existing_grant:
            assert not Path(prefs._PREFS_PATH).exists()
    finally:
        instance.stop.set()
        instance.thread.join(2)
    assert not instance.thread.is_alive()


@pytest.mark.parametrize("unread", [False, True])
def test_hard_kill_retries_failed_durable_fence_at_bounded_cadence(reporter, monkeypatch, unread):
    import builtins
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(True)
    clock, attempts = [100.0], []
    from types import SimpleNamespace
    monkeypatch.setattr(reports, "time", SimpleNamespace(time=reports.time.time, monotonic=lambda: clock[0]))
    target = Path(prefs._PREFS_PATH)
    original = target.read_bytes()
    open_file, atomic, save = builtins.open, reports._atomic, prefs._save
    writable = False

    def denied_read(path, mode="r", *args, **kwargs):
        if unread and str(path) == str(target) and mode == "r" and not writable:
            raise OSError(errno.EIO, "controlled unread grant")
        return open_file(path, mode, *args, **kwargs)

    def denied_fence(path, value):
        if path.name == "revoked.json":
            attempts.append(clock[0])
            if not writable:
                raise PermissionError(errno.EACCES, "controlled unavailable fence storage")
        return atomic(path, value)

    monkeypatch.setattr(builtins, "open", denied_read)
    monkeypatch.setattr(reports, "_atomic", denied_fence)
    def denied_save(value):
        if not writable:
            raise PermissionError(errno.EACCES, "controlled unavailable preference storage")
        return save(value)
    monkeypatch.setattr(prefs, "_save", denied_save)
    monkeypatch.setenv("OMNIVOICE_DIAGNOSTICS_DISABLED", "1")
    for value in (100.0, 101.0, 159.9):
        clock[0] = value
        reporter._tick()
        assert not reporter.enabled and not reporter.capture("engine_failed")
    assert attempts == [100.0], "unread withdrawal hammered unavailable fence storage"
    assert target.read_bytes() == original  # No successful durable write was possible yet.
    writable = True
    clock[0] = 160.0
    reporter._tick()
    assert attempts == [100.0, 160.0]
    assert (reporter.root / "revoked.json").exists()
    assert prefs.get("diagnostics_enabled") is False
