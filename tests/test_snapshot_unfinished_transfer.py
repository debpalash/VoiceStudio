"""A repository with one finished weight file is not installed while another is
still transferring.

OmniVoice ships two weight files: the 2.4 GB model and an 0.8 GB audio
tokenizer under ``audio_tokenizer/``. The weight-presence check accepts *any*
plausible weight file, so as soon as the smaller tokenizer landed, the catalogue
reported the model ``installed`` (and the setup wizard showed it as installed)
while the main weight was still a ``.part`` in ``blobs/``. The same false
positive followed a crash after the smaller file finished. A partial transfer
written after the snapshot's newest finished file now marks it incomplete;
older leftovers from earlier attempts do not, so existing installs stay
installed.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from services import model_catalogue as catalogue

MODEL = {"repo_id": "org/two-weights"}
WEIGHT = 6 * 1024 * 1024


def _repo(tmp_path: Path, symlink) -> tuple[Path, Path]:
    repo = tmp_path / "models--org--two-weights"
    blobs = repo / "blobs"
    snapshot = repo / "snapshots" / "rev"
    (snapshot / "audio_tokenizer").mkdir(parents=True)
    blobs.mkdir()
    blob = blobs / "tokenizer-blob"
    blob.write_bytes(b"\0" * WEIGHT)
    symlink(snapshot / "audio_tokenizer" / "model.safetensors", blob)
    (snapshot / "config.json").write_text("{}")
    return snapshot, blobs


def _age(path: Path, seconds: float) -> None:
    stamp = path.lstat().st_mtime - seconds
    os.utime(path, (stamp, stamp), follow_symlinks=False)


@pytest.mark.parametrize("suffix", [".part", ".part.done", ".incomplete"])
def test_newer_partial_transfer_marks_snapshot_incomplete(tmp_path, symlink_or_skip, suffix):
    snapshot, blobs = _repo(tmp_path, symlink_or_skip)
    for entry in snapshot.rglob("*"):
        _age(entry, 60)
    (blobs / f"main-weight{suffix}").write_bytes(b"\0" * 1024)

    assert not catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_finished_snapshot_without_partials_is_complete(tmp_path, symlink_or_skip):
    snapshot, _blobs = _repo(tmp_path, symlink_or_skip)

    assert catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_stale_partial_from_an_earlier_attempt_is_ignored(tmp_path, symlink_or_skip):
    snapshot, blobs = _repo(tmp_path, symlink_or_skip)
    stale = blobs / "old-revision.part"
    stale.write_bytes(b"\0" * 1024)
    _age(stale, 3600)

    assert catalogue.snapshot_is_complete(MODEL, str(snapshot))


def test_entries_with_required_files_keep_their_exact_check(tmp_path, symlink_or_skip):
    snapshot, blobs = _repo(tmp_path, symlink_or_skip)
    for entry in snapshot.rglob("*"):
        _age(entry, 60)
    (blobs / "sibling-package.part").write_bytes(b"\0" * 1024)
    model = {**MODEL, "required_files": ["audio_tokenizer/model.safetensors"]}

    assert catalogue.snapshot_is_complete(model, str(snapshot))
