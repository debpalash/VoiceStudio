"""Transient storage faults preserve accepted diagnostics until durable publication."""
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


def test_withdrawal_cancels_transport_and_returns_while_worker_storage_is_blocked(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import analytics_transport, prefs
    reporter.consent(True)
    old_epoch = reporter.epoch
    reporter._tick()
    entered, release = threading.Event(), threading.Event()
    sending, cancelled, withdrawn = threading.Event(), threading.Event(), threading.Event()
    atomic = reports._atomic

    def blocked_atomic(path, value):
        if path.parent == reporter.directory:
            entered.set()
            assert release.wait(5), "test did not release controlled outbox storage"
        return atomic(path, value)

    def blocked_send(transport, body):
        sending.set()
        assert transport._stopped.wait(5), "withdrawal did not cancel the independent sender"
        cancelled.set()
        return 0

    monkeypatch.setattr(reports, "_atomic", blocked_atomic)
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
    result = []

    def withdraw():
        result.append(reporter.consent(False))
        withdrawn.set()

    choice = threading.Thread(target=withdraw, daemon=True)
    choice.start()
    try:
        assert withdrawn.wait(.5), "OFF waited for the worker-held storage lock"
        assert cancelled.wait(.5), "OFF left the independent sender active"
        assert result[0]["enabled"] is False and result[0]["grant_epoch"] is None
        assert not reporter.capture("generation_failed")
        assert not transport.capture("diagnostic_report", {}, reporter.profile)
        assert reporter.epoch != old_epoch
    finally:
        release.set()
        choice.join(5)
        transport.shutdown()
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


def test_withdrawal_fences_a_worker_consent_read_already_in_progress(reporter, monkeypatch):
    from core import prefs
    reporter.consent(True)
    old_epoch = reporter.epoch
    entered, release = threading.Event(), threading.Event()
    load = prefs._load
    saved = load()

    def blocked_load():
        if threading.current_thread().name == "blocked-diagnostic-read":
            entered.set()
            assert release.wait(5)
            return dict(saved)
        return load()

    def tick():
        with reporter.lock:
            reporter._tick()

    monkeypatch.setattr(prefs, "_load", blocked_load)
    worker = threading.Thread(target=tick, name="blocked-diagnostic-read", daemon=True)
    worker.start()
    assert entered.wait(5)
    try:
        assert reporter.consent(False)["enabled"] is False
        assert reporter.epoch != old_epoch
        assert not reporter.capture("generation_failed")
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not reporter.enabled and reporter.epoch is None
    reporter._tick()
    assert prefs.get("diagnostics_enabled") is False
    reporter.consent(True)
    assert reporter.enabled and reporter.epoch != old_epoch


def test_failed_withdrawal_persistence_retries_at_a_bounded_deadline(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(True)
    save, atomic = prefs._save, reports._atomic
    writable = False
    attempts = []
    clock = [100.0]
    monkeypatch.setattr(reports.time, "monotonic", lambda: clock[0])

    def save_when_healthy(data):
        attempts.append(data["diagnostics_enabled"])
        if not writable:
            raise PermissionError("controlled preferences failure")
        return save(data)

    def atomic_when_healthy(path, value):
        if not writable and path.name == "revoked.json":
            raise PermissionError("controlled revocation failure")
        return atomic(path, value)

    monkeypatch.setattr(prefs, "_save", save_when_healthy)
    monkeypatch.setattr(reports, "_atomic", atomic_when_healthy)
    with pytest.raises(PermissionError):
        reporter.consent(False)
    reporter._tick()
    assert reporter.consent_retry_at == 160 and len(attempts) == 2
    assert not reporter.enabled and not reporter.capture("generation_failed")
    writable = True
    reporter._tick()
    assert len(attempts) == 2 and prefs.get("diagnostics_enabled") is True
    clock[0] = 160
    reporter._tick()
    assert attempts == [False, False, False]
    assert prefs.get("diagnostics_enabled") is False
    assert reporter.pending_withdrawal is None and not reporter.enabled
    reporter.consent(True)
    assert reporter.enabled and reporter.capture("download_failed", stage="download")


def test_withdrawal_during_enable_write_preserves_existing_revocation(reporter, monkeypatch):
    from core import prefs
    reporter.consent(False)
    revoked = reporter.root / "revoked.json"
    original = revoked.read_bytes()
    entered, release = threading.Event(), threading.Event()
    save = prefs._save
    result = []

    def blocked_enable(data):
        save(data)
        if data["diagnostics_enabled"]:
            entered.set()
            assert release.wait(5)

    monkeypatch.setattr(prefs, "_save", blocked_enable)
    worker = threading.Thread(target=lambda: result.append(reporter.consent(True)), daemon=True)
    worker.start()
    assert entered.wait(5)
    try:
        assert reporter.consent(False)["enabled"] is False
        assert not reporter.capture("generation_failed")
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and result[0]["enabled"] is False
    assert revoked.read_bytes() == original, "superseded ON removed the durable OFF marker"
    assert reporter.pending_withdrawal is not None and not reporter.enabled
    reporter._tick()
    assert prefs.get("diagnostics_enabled") is False
    assert reporter.pending_withdrawal is None


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


@pytest.mark.parametrize("save_fails", [False, True])
def test_shutdown_commits_deferred_withdrawal_before_worker_stops(reporter, monkeypatch, save_fails):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(True)
    reporter._tick()
    entered, release = threading.Event(), threading.Event()
    atomic, save = reports._atomic, prefs._save

    def blocked_atomic(path, value):
        if path.parent == reporter.directory:
            entered.set()
            assert release.wait(5)
        return atomic(path, value)

    def tick():
        with reporter.lock:
            reporter._tick()

    monkeypatch.setattr(reports, "_atomic", blocked_atomic)
    assert reporter.capture("engine_failed", stage="engine")
    worker = threading.Thread(target=tick, daemon=True)
    worker.start()
    assert entered.wait(5)
    try:
        assert reporter.consent(False)["enabled"] is False
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    reporter.consent_retry_at = float("inf")  # Shutdown must not wait for a retry tick.
    if save_fails:
        monkeypatch.setattr(prefs, "_save", lambda _: (_ for _ in ()).throw(PermissionError("controlled OFF failure")))
        with pytest.raises(PermissionError, match="controlled OFF failure"):
            reporter.shutdown()
        assert reporter.pending_withdrawal is not None
        monkeypatch.setattr(prefs, "_save", save)
    else:
        reporter.shutdown()
        assert reporter.pending_withdrawal is None
    assert reporter.stop.is_set() and not reporter.enabled
    restarted = reports.Reporter()
    restarted.schema = reports._schema()
    try:
        restarted._tick()
        assert not restarted.enabled, "shutdown lost the accepted withdrawal before restart"
    finally:
        restarted.shutdown()


def test_withdrawal_overlapping_original_marker_unlink_keeps_durable_fence(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(False)
    revoked = reporter.root / "revoked.json"
    entered, release = threading.Event(), threading.Event()
    unlink, atomic, save = Path.unlink, reports._atomic, prefs._save
    off_requested = False
    results = []

    def blocked_unlink(path, *args, **kwargs):
        if path == revoked and threading.current_thread().name == "enable-withdrawal-race":
            entered.set()
            assert release.wait(5)
        return unlink(path, *args, **kwargs)

    def fail_later_atomic(path, value):
        if off_requested:
            raise PermissionError("controlled later durable write failure")
        return atomic(path, value)

    def fail_later_save(data):
        if off_requested:
            raise PermissionError("controlled later preferences failure")
        return save(data)

    monkeypatch.setattr(Path, "unlink", blocked_unlink)
    monkeypatch.setattr(reports, "_atomic", fail_later_atomic)
    monkeypatch.setattr(prefs, "_save", fail_later_save)
    worker = threading.Thread(target=lambda: results.append(reporter.consent(True)), name="enable-withdrawal-race", daemon=True)
    worker.start()
    assert entered.wait(5)
    try:
        off_requested = True
        assert reporter.consent(False)["enabled"] is False
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and results[0]["enabled"] is False
    reporter._tick()  # Both attempted durable OFF writes still fail.
    restarted = reports.Reporter()
    restarted.schema = reports._schema()
    try:
        restarted.refresh()
        assert not restarted.enabled, "the stale ON removed the only durable revocation evidence"
    finally:
        off_requested = False
        reporter._tick()
        restarted.shutdown()


def test_deeply_nested_record_is_rejected_without_blocking_fresh_publication(reporter):
    reporter.consent(True)
    reporter.directory.mkdir(parents=True, exist_ok=True)
    malformed = reporter.directory / ("a" * 32 + ".json")
    malformed.write_text("[" * 1500 + "]" * 1500, encoding="utf-8")
    assert reporter.capture("engine_failed", stage="engine")
    reporter._tick()
    assert not malformed.exists()
    saved, = reporter._files()
    assert saved["props"]["category"] == "engine_failed"


@pytest.mark.parametrize("restore_fails", [False, True])
def test_final_fence_retirement_overlap_restores_or_surfaces_uncommitted_off(reporter, monkeypatch, restore_fails):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(False)
    pending_fence = reporter.root / "revocation-pending.json"
    entered, release = threading.Event(), threading.Event()
    unlink, atomic = Path.unlink, reports._atomic
    off_requested = False
    results, failures = [], []

    def blocked_unlink(path, *args, **kwargs):
        if path == pending_fence and threading.current_thread().name == "final-fence-race":
            entered.set()
            assert release.wait(5)
        return unlink(path, *args, **kwargs)

    def controlled_atomic(path, value):
        if restore_fails and off_requested:
            raise PermissionError("controlled final fence restoration failure")
        return atomic(path, value)

    def enable():
        try:
            results.append(reporter.consent(True))
        except OSError as error:
            failures.append(error)

    monkeypatch.setattr(Path, "unlink", blocked_unlink)
    monkeypatch.setattr(reports, "_atomic", controlled_atomic)
    worker = threading.Thread(target=enable, name="final-fence-race", daemon=True)
    worker.start()
    assert entered.wait(5)
    try:
        off_requested = True
        assert reporter.consent(False)["enabled"] is False
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not reporter.enabled and reporter.consent_failed
    assert reporter.pending_withdrawal is not None
    assert not reporter.capture("generation_failed")
    if restore_fails:
        assert len(failures) == 1 and isinstance(failures[0], PermissionError)
        # OFF has had no successful durable operation after an already-started
        # final unlink. Keep that limitation explicit; do not assert restart safety.
        assert not (reporter.root / "revoked.json").exists()
        assert prefs.get("diagnostics_enabled") is True
    else:
        assert not failures and results[0]["enabled"] is False
        assert (reporter.root / "revoked.json").exists()
        restarted = reports.Reporter()
        restarted.schema = reports._schema()
        try:
            restarted.refresh()
            assert not restarted.enabled
        finally:
            restarted.shutdown()
    off_requested = False
    reporter._tick()
    assert reporter.pending_withdrawal is None
    assert prefs.get("diagnostics_enabled") is False


def test_successful_current_grant_retires_both_fences_and_survives_restart(reporter):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(False)
    reporter.consent(True)
    assert reporter.enabled
    assert not (reporter.root / "revoked.json").exists()
    assert not (reporter.root / "revocation-pending.json").exists()
    restarted = reports.Reporter()
    restarted.schema = reports._schema()
    try:
        restarted.refresh()
        assert restarted.enabled and restarted.epoch == reporter.epoch
    finally:
        restarted.shutdown()


def test_withdrawal_arriving_during_shutdown_marker_io_is_also_persisted(reporter, monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(True)
    reporter._tick()
    entered, release = threading.Event(), threading.Event()
    atomic = reports._atomic

    def blocked_retirement(path, value):
        if path.name == "session.json" and value.get("clean") is True:
            entered.set()
            assert release.wait(5)
        return atomic(path, value)

    monkeypatch.setattr(reports, "_atomic", blocked_retirement)
    worker = threading.Thread(target=reporter.shutdown, daemon=True)
    worker.start()
    assert entered.wait(5)
    try:
        assert reporter.consent(False)["enabled"] is False
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert reporter.stop.is_set() and reporter.pending_withdrawal is None
    assert prefs.get("diagnostics_enabled") is False


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
