"""Unit tests for the dub crash-resume journal (pure file I/O)."""
import os
import time
import importlib

import pytest


@pytest.fixture
def dub_resume():
    return importlib.import_module("services.dub_resume")


def _wav(path, payload=b"RIFF....WAVEdata"):
    path.write_bytes(payload)
    return str(path)


def _ok(_path):
    return True


def test_round_trip_and_key_mismatch(tmp_path, dub_resume):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"))
    src = _wav(tmp_path / "s.wav")
    assert journal.record("a", "key1", src, 24000)
    assert journal.has("a", "key1") and not journal.has("a", "key2")
    assert journal.recover("a", "key1", 24000, _ok)
    assert journal.recover("a", "key2", 24000, _ok) is None
    assert not journal.has("a", "key1")  # a mismatching entry is discarded


def test_sample_rate_and_validator_reject(tmp_path, dub_resume):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"))
    journal.record("a", "k", _wav(tmp_path / "s.wav"), 24000)
    assert journal.recover("a", "k", 16000, _ok) is None
    journal.record("a", "k", _wav(tmp_path / "s.wav"), 24000)
    assert journal.recover("a", "k", 24000, lambda _p: False) is None
    assert not (tmp_path / "j" / "seg_a.wav").exists()


def test_torn_pair_from_racing_renders_is_rejected(tmp_path, dub_resume):
    """Render B's audio under render A's record never verifies."""
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"))
    journal.record("a", "keyA", _wav(tmp_path / "a.wav", b"audio of A"), 24000)
    (tmp_path / "j" / "seg_a.wav").write_bytes(b"audio of B")
    assert journal.recover("a", "keyA", 24000, _ok) is None


def test_size_bound(tmp_path, dub_resume):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"), max_bytes=100)
    assert journal.record("a", "k", _wav(tmp_path / "a.wav", b"x" * 80), 1)
    assert not journal.record("b", "k", _wav(tmp_path / "b.wav", b"x" * 80), 1)
    assert not (tmp_path / "j" / "seg_b.wav").exists()


@pytest.mark.parametrize("operation", ["full", "discard", "failure"])
def test_segment_diagnostics_are_bounded_single_line(tmp_path, dub_resume, caplog, operation):
    import logging
    from core.logging_utils import log_safe

    sid = "segment\nFORGED\r\x1b[31m" + ("x" * 800 if operation != "discard" else "")
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"), max_bytes=0 if operation == "full" else 100)
    src = _wav(tmp_path / "s.wav")
    with caplog.at_level(logging.INFO):
        if operation == "discard":
            assert journal.record(sid, "k", src, 1)
            assert journal.recover(sid, "changed", 1, _ok) is None
        else:
            assert not journal.record(sid, "k", src if operation == "full" else str(tmp_path / "missing.wav"), 1)
    assert caplog.records
    message = caplog.records[-1].getMessage()
    assert log_safe(sid) in message
    assert not any(char in message for char in "\n\r\x1b")


def test_prune_drops_unknown_stale_and_torn_entries(tmp_path, dub_resume):
    root = tmp_path / "j"
    journal = dub_resume.DubResumeJournal(str(root), max_age_s=3600)
    for sid in ("keep", "gone", "old"):
        journal.record(sid, "k", _wav(tmp_path / f"{sid}.wav"), 1)
    old = time.time() - 7200
    os.utime(root / "seg_old.wav", (old, old))
    (root / "seg_torn.wav").write_bytes(b"no record")
    (root / "seg_keep.wav.1.tmp").write_bytes(b"partial")
    journal.prune({"keep", "old", "torn"})
    assert sorted(p.name for p in root.iterdir()) == ["seg_keep.json", "seg_keep.wav"]


def test_discard_removes_journal_and_empty_parent(tmp_path, dub_resume):
    root = tmp_path / ".resume" / "en"
    journal = dub_resume.DubResumeJournal(str(root))
    journal.record("a", "k", _wav(tmp_path / "s.wav"), 1)
    journal.discard()
    assert not (tmp_path / ".resume").exists()


def test_reference_hash_follows_content_not_path(tmp_path, dub_resume):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"voice one")
    memo = {}
    first = dub_resume.hash_reference_file(str(ref), memo)
    ref.write_bytes(b"voice two!")
    assert dub_resume.hash_reference_file(str(ref), memo) != first
    assert dub_resume.hash_reference_file(str(tmp_path / "missing.wav")) is None
    assert dub_resume.hash_reference_file(None) is None


def test_every_part_changes_the_key(dub_resume):
    base = {"text": "hi", "seed": 1, "num_step": 16}
    key = dub_resume.build_key(base)
    assert dub_resume.build_key(dict(base)) == key
    for field, value in (("text", "ho"), ("seed", 2), ("num_step", 8), ("extra", 1)):
        assert dub_resume.build_key({**base, field: value}) != key


@pytest.mark.parametrize("alternate_spelling", [False, True])
def test_overlapping_journals_share_the_track_size_bound(tmp_path, dub_resume, alternate_spelling):
    root = str(tmp_path / "j")
    first = dub_resume.DubResumeJournal(root, max_bytes=100)
    second = dub_resume.DubResumeJournal(
        os.path.join(root, ".") if alternate_spelling else root, max_bytes=100,
    )
    first.prune({"a", "b"})
    second.prune({"a", "b"})
    source = _wav(tmp_path / "source.wav", b"x" * 80)
    assert first.record("a", "k", source, 1)
    assert not second.record("b", "k", source, 1)
    assert sum(path.stat().st_size for path in (tmp_path / "j").glob("*.wav")) <= 100
    second.drop("a")
    assert first.record("b", "k", source, 1)
    second.discard()
    assert first.record("a", "k", source, 1)


@pytest.mark.parametrize("root_exists", [False, True])
def test_case_insensitive_root_aliases_share_the_track_size_bound(tmp_path, dub_resume, root_exists):
    parent = tmp_path / "CaseParent"
    parent.mkdir()
    parent_alias = tmp_path / "caseparent"
    if not parent_alias.exists() or not parent.samefile(parent_alias):
        pytest.skip("temporary filesystem is case-sensitive")
    root = parent / "Journal"
    alias = parent_alias / "journal"
    if root_exists:
        root.mkdir()
    first = dub_resume.DubResumeJournal(str(root), max_bytes=100)
    second = dub_resume.DubResumeJournal(str(alias), max_bytes=100)
    assert root.exists() is root_exists  # Looking up state must not create the journal.
    first.prune({"a", "b"})
    second.prune({"a", "b"})
    source = _wav(tmp_path / "source.wav", b"x" * 80)
    assert first.record("a", "k", source, 1)
    assert not second.record("b", "k", source, 1)
    assert sum(path.stat().st_size for path in root.glob("*.wav")) <= 100
    second.discard()
    assert first.record("b", "k", source, 1)


def test_distinct_case_sensitive_roots_keep_independent_size_bounds(tmp_path, dub_resume):
    first_root = tmp_path / "Journal"
    first_root.mkdir()
    second_root = tmp_path / "journal"
    if second_root.exists() and first_root.samefile(second_root):
        pytest.skip("temporary filesystem is case-insensitive")
    second_root.mkdir()
    first = dub_resume.DubResumeJournal(str(first_root), max_bytes=100)
    second = dub_resume.DubResumeJournal(str(second_root), max_bytes=100)
    source = _wav(tmp_path / "source.wav", b"x" * 80)
    assert first.record("a", "k", source, 1)
    assert second.record("b", "k", source, 1)


def test_missing_distinct_roots_stay_lazy_and_keep_independent_bounds(tmp_path, dub_resume):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = dub_resume.DubResumeJournal(str(first_root), max_bytes=100)
    second = dub_resume.DubResumeJournal(str(second_root), max_bytes=100)
    assert not first_root.exists() and not second_root.exists()
    source = _wav(tmp_path / "source.wav", b"x" * 80)
    assert first.record("a", "k", source, 1)
    assert second.record("b", "k", source, 1)


def test_replacing_a_segment_only_counts_the_replacement_once(tmp_path, dub_resume):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"), max_bytes=100)
    source = _wav(tmp_path / "source.wav", b"x" * 80)
    assert journal.record("a", "old", source, 1)
    assert journal.record("a", "new", source, 1)
    assert journal.has("a", "new") and not journal.has("a", "old")


def test_reopening_a_journal_counts_existing_audio_without_pruning(tmp_path, dub_resume):
    root = str(tmp_path / "j")
    source = _wav(tmp_path / "source.wav", b"x" * 80)
    journal = dub_resume.DubResumeJournal(root, max_bytes=100)
    assert journal.record("a", "k", source, 1)
    del journal
    reopened = dub_resume.DubResumeJournal(root, max_bytes=100)
    assert not reopened.record("b", "k", source, 1)


def test_torn_metadata_write_still_counts_its_audio(tmp_path, dub_resume, monkeypatch):
    root = tmp_path / "j"
    journal = dub_resume.DubResumeJournal(str(root), max_bytes=100)
    source = _wav(tmp_path / "source.wav", b"x" * 80)

    def broken_metadata(*_args, **_kwargs):
        raise OSError("metadata write failed")

    with monkeypatch.context() as patch:
        patch.setattr(dub_resume.json, "dump", broken_metadata)
        assert not journal.record("a", "k", source, 1)
    assert not journal.record("b", "k", source, 1)
    assert sum(path.stat().st_size for path in root.glob("*.wav")) <= 100
    journal.prune({"a", "b"})
    assert journal.record("b", "k", source, 1)


def test_journal_write_failure_does_not_log_filesystem_paths(tmp_path, dub_resume, monkeypatch, caplog):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"))
    source = _wav(tmp_path / "source.wav")

    def denied(*_args):
        raise PermissionError(f"Access denied: {tmp_path / 'private' / 'voice.wav'}")

    monkeypatch.setattr(dub_resume.shutil, "copyfile", denied)
    with caplog.at_level("INFO", logger="omnivoice.dub"):
        assert not journal.record("a", "k", source, 1)
    assert "PermissionError" in caplog.text
    assert str(tmp_path) not in caplog.text


def test_recovery_stages_a_snapshot_that_survives_journal_replacement(tmp_path, dub_resume):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"))
    old = b"verified first render"
    assert journal.record("a", "old", _wav(tmp_path / "old.wav", old), 24000)
    staged = tmp_path / "private-stage.wav"
    assert journal.recover_into("a", "old", 24000, _ok, str(staged)) == str(staged)
    assert journal.record("a", "new", _wav(tmp_path / "new.wav", b"second render"), 24000)
    assert journal.has("a", "new")
    assert staged.read_bytes() == old


def test_recovery_holds_the_root_lock_until_staging_finishes(tmp_path, dub_resume, monkeypatch):
    from queue import Queue
    from threading import Thread

    root = str(tmp_path / "j")
    reader = dub_resume.DubResumeJournal(root)
    writer = dub_resume.DubResumeJournal(os.path.join(root, "."))
    old = b"verified first render"
    assert reader.record("a", "old", _wav(tmp_path / "old.wav", old), 24000)
    replacement = _wav(tmp_path / "replacement.wav", b"other render")
    copied = tmp_path / "private-stage.wav"
    attempts = Queue()
    lock_observations = []
    writes = []
    failures = []
    threads = []
    copy = dub_resume.copy_into_stage

    def replace_audio():
        acquired = writer._lock.acquire(blocking=False)
        try:
            if acquired:
                # An unlocked copy must observe this replacement: coordinate
                # the write before copying instead of relying on scheduling.
                writes.append(writer.record("a", "new", replacement, 24000))
                attempts.put(True)
            else:
                attempts.put(False)
                writes.append(writer.record("a", "new", replacement, 24000))
        except BaseException as error:
            failures.append(error)
        finally:
            if acquired:
                writer._lock.release()

    def copy_while_writer_waits(source, destination):
        thread = Thread(target=replace_audio, daemon=True)
        threads.append(thread)
        thread.start()
        lock_observations.append(attempts.get(timeout=2))
        copy(source, destination)

    monkeypatch.setattr(dub_resume, "copy_into_stage", copy_while_writer_waits)
    try:
        assert reader.recover_into("a", "old", 24000, _ok, str(copied)) == str(copied)
    finally:
        for thread in threads:
            thread.join(timeout=2)
    assert threads and all(not thread.is_alive() for thread in threads)
    assert not failures
    assert lock_observations == [False]
    assert writes == [True]
    assert writer.has("a", "new")
    assert copied.read_bytes() == old


@pytest.mark.parametrize("key, sample_rate, valid", [
    ("missing", 24000, True), ("old", 16000, True), ("old", 24000, False),
])
def test_invalid_recovery_does_not_publish_a_staged_snapshot(
    tmp_path, dub_resume, key, sample_rate, valid,
):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"))
    assert journal.record("a", "old", _wav(tmp_path / "old.wav"), 24000)
    staged = tmp_path / "private-stage.wav"
    assert journal.recover_into("a", key, sample_rate, lambda _: valid, str(staged)) is None
    assert not staged.exists()
