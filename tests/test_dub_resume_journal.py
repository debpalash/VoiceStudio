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
