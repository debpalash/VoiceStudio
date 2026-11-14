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
