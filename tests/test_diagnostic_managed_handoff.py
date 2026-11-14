"""Published Python inbox files are immutable across Electron's read/delete handoff."""
import importlib
import json
import threading

import pytest

from test_diagnostic_reports import reporter  # noqa: F401


@pytest.fixture
def managed_reporter(reporter, monkeypatch, tmp_path):
    reports = importlib.import_module("core.diagnostic_reports")
    root = tmp_path / "managed"
    root.mkdir()
    monkeypatch.setenv("OMNIVOICE_DIAGNOSTICS_DIR", str(root))
    consent = {
        "version": 1, "enabled": True, "epoch": "a" * 32,
        "profile_id": "install_" + "b" * 32,
    }
    (root / "consent.json").write_text(json.dumps(consent), encoding="utf-8")
    instance = reports.Reporter()
    instance.schema = reports._schema()
    instance._tick()
    yield instance
    instance.shutdown()


def test_matching_failure_during_inbox_read_delete_is_not_lost(managed_reporter):
    instance = managed_reporter
    assert instance.capture("engine_failed", stage="engine")
    instance._tick()
    path, = instance.directory.glob("*.json")
    published = path.read_bytes()
    read = threading.Event()
    remove = threading.Event()
    consumed = []
    errors = []

    def desktop_consumer():
        try:
            # Match Electron importInbox: read a stable producer ID, commit
            # that snapshot, then unlink the file after persistence succeeds.
            consumed.append(json.loads(path.read_text(encoding="utf-8")))
            read.set()
            if not remove.wait(5):
                raise TimeoutError("producer did not finish the controlled update")
            path.unlink()
        except BaseException as error:
            errors.append(error)
            read.set()

    consumer = threading.Thread(target=desktop_consumer)
    consumer.start()
    try:
        assert read.wait(5)
        assert not errors
        assert instance.capture("engine_failed", stage="engine")
        instance._tick()
        original_after_second_failure = path.read_bytes()
    finally:
        remove.set()
        consumer.join(5)
    assert not consumer.is_alive()
    assert not errors
    remaining = instance._files()
    assert sum(record["props"]["occurrences"] for record in consumed + remaining) == 2, (
        "the desktop read/delete handoff lost the concurrently captured failure"
    )
    assert original_after_second_failure == published
    assert {record["id"] for record in consumed}.isdisjoint(
        record["id"] for record in remaining
    )
    assert instance.transport is None


def test_repeated_managed_failures_keep_the_inbox_capacity_bound(managed_reporter):
    instance = managed_reporter
    for _ in range(40):
        assert instance.capture("engine_failed", stage="engine")
    instance._tick()
    saved = instance._files()
    assert len(saved) == instance._capacity("count") == 32
    assert instance.dropped == 8
    assert all(record["props"]["occurrences"] == 1 for record in saved)
    assert len({record["id"] for record in saved}) == 32
    assert instance.transport is None


def test_withdrawal_drops_pending_old_epoch_before_fresh_opt_in(managed_reporter):
    instance = managed_reporter
    assert instance.capture("engine_failed", stage="engine")
    instance._tick()
    assert instance.capture("engine_failed", stage="engine")
    consent_path = instance.root / "consent.json"
    consent = json.loads(consent_path.read_text(encoding="utf-8"))
    consent.update(enabled=False, epoch="c" * 32)
    consent_path.write_text(json.dumps(consent), encoding="utf-8")
    instance._tick()
    assert instance._files() == []
    consent.update(enabled=True, epoch="d" * 32)
    consent_path.write_text(json.dumps(consent), encoding="utf-8")
    instance._tick()
    assert instance._files() == []
    assert instance.capture("engine_failed", stage="engine")
    instance._tick()
    fresh, = instance._files()
    assert fresh["epoch"] == "d" * 32
    assert fresh["props"]["occurrences"] == 1

