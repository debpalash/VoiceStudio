"""Unit tests for the dub crash-resume journal (pure file I/O)."""
import os
import time

from services import dub_resume


def _wav(path, payload=b"RIFF....WAVEdata"):
    path.write_bytes(payload)
    return str(path)


def _ok(_path):
    return True


def test_round_trip_and_key_mismatch(tmp_path):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"))
    src = _wav(tmp_path / "s.wav")
    assert journal.record("a", "key1", src, 24000)
    assert journal.has("a", "key1") and not journal.has("a", "key2")
    assert journal.recover("a", "key1", 24000, _ok)
    assert journal.recover("a", "key2", 24000, _ok) is None
    assert not journal.has("a", "key1")  # a mismatching entry is discarded


def test_sample_rate_and_validator_reject(tmp_path):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"))
    journal.record("a", "k", _wav(tmp_path / "s.wav"), 24000)
    assert journal.recover("a", "k", 16000, _ok) is None
    journal.record("a", "k", _wav(tmp_path / "s.wav"), 24000)
    assert journal.recover("a", "k", 24000, lambda _p: False) is None
    assert not (tmp_path / "j" / "seg_a.wav").exists()


def test_torn_pair_from_racing_renders_is_rejected(tmp_path):
    """Render B's audio under render A's record never verifies."""
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"))
    journal.record("a", "keyA", _wav(tmp_path / "a.wav", b"audio of A"), 24000)
    (tmp_path / "j" / "seg_a.wav").write_bytes(b"audio of B")
    assert journal.recover("a", "keyA", 24000, _ok) is None


def test_size_bound(tmp_path):
    journal = dub_resume.DubResumeJournal(str(tmp_path / "j"), max_bytes=100)
    assert journal.record("a", "k", _wav(tmp_path / "a.wav", b"x" * 80), 1)
    assert not journal.record("b", "k", _wav(tmp_path / "b.wav", b"x" * 80), 1)
    assert not (tmp_path / "j" / "seg_b.wav").exists()


def test_prune_drops_unknown_stale_and_torn_entries(tmp_path):
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


def test_discard_removes_journal_and_empty_parent(tmp_path):
    root = tmp_path / ".resume" / "en"
    journal = dub_resume.DubResumeJournal(str(root))
    journal.record("a", "k", _wav(tmp_path / "s.wav"), 1)
    journal.discard()
    assert not (tmp_path / ".resume").exists()


def test_reference_hash_follows_content_not_path(tmp_path):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"voice one")
    memo = {}
    first = dub_resume.hash_reference_file(str(ref), memo)
    ref.write_bytes(b"voice two!")
    assert dub_resume.hash_reference_file(str(ref), memo) != first
    assert dub_resume.hash_reference_file(str(tmp_path / "missing.wav")) is None
    assert dub_resume.hash_reference_file(None) is None


def test_every_part_changes_the_key():
    base = {"text": "hi", "seed": 1, "num_step": 16}
    key = dub_resume.build_key(base)
    assert dub_resume.build_key(dict(base)) == key
    for field, value in (("text", "ho"), ("seed", 2), ("num_step", 8), ("extra", 1)):
        assert dub_resume.build_key({**base, field: value}) != key
