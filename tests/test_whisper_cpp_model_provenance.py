"""A whisper.cpp model's repository provenance follows the file it points at.

The resolver keeps a cached checkpoint's logical name so capability checks
can classify it, but the licence check must keep working on the bytes the
user actually selected: a cached model is a symlink into a content-addressed
blob, and the repository holding those bytes is the one whose terms apply.
A snapshot alias that points into another repository must not borrow that
repository's identity, and a custom local file must not gain one either.
"""
from __future__ import annotations

import hashlib

import pytest

pytest_plugins = ["whisper_cpp_fixtures"]

_OWNER = "licensed-owner--custom-whisper"
_OTHER = "other-owner--other-whisper"


def _repo_dir(root, owner):
    return root / "hf-cache" / f"models--{owner}"


def _blob(repo_dir, filename):
    """The content-addressed file a cached checkpoint points at."""
    blob = repo_dir / "blobs" / hashlib.sha256(filename.encode()).hexdigest()
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"weights")
    return blob


def test_cached_model_keeps_its_repository_provenance(tmp_path, monkeypatch, m, symlink_or_skip):
    """A model symlinked from a cache directory still names its repository."""
    repo = _repo_dir(tmp_path, _OWNER)
    blob = _blob(repo, "ggml-base.bin")
    link = tmp_path / "models" / "ggml-base.bin"
    link.parent.mkdir(parents=True)
    symlink_or_skip(link, blob)
    monkeypatch.setenv(m.MODEL_ENV, str(link))

    assert m.ab.backend_model_repos(m.WhisperCppBackend()) == ["licensed-owner/custom-whisper"]


def test_snapshot_alias_uses_the_actual_target_repository(
    tmp_path, monkeypatch, m, symlink_or_skip
):
    """A snapshot entry pointing into another repository is judged by the
    repository that actually holds the bytes."""
    alias_repo = _repo_dir(tmp_path, _OTHER)
    blob = _blob(_repo_dir(tmp_path, _OWNER), "ggml-base.bin")
    snapshot = alias_repo / "snapshots" / "rev0"
    snapshot.mkdir(parents=True)
    link = snapshot / "ggml-base.bin"
    symlink_or_skip(link, blob)
    monkeypatch.setenv(m.MODEL_ENV, str(link))

    assert m.ab.backend_model_repos(m.WhisperCppBackend()) == ["licensed-owner/custom-whisper"]


def test_licence_acceptance_is_enforced_for_the_actual_repository(
    tmp_path, monkeypatch, m, symlink_or_skip
):
    """The gated repository of the selected file is the one that is checked."""
    from services import model_acceptance

    repo = _repo_dir(tmp_path, "gated-owner--custom-whisper")
    blob = _blob(repo, "ggml-base.bin")
    link = tmp_path / "models" / "ggml-base.bin"
    link.parent.mkdir(parents=True)
    symlink_or_skip(link, blob)
    monkeypatch.setenv(m.MODEL_ENV, str(link))
    checked = []

    def refuse(repos):
        checked.append(repos)
        raise RuntimeError("licence refused")

    monkeypatch.setattr(model_acceptance, "ensure_accepted", refuse)

    with pytest.raises(RuntimeError, match="licence refused"):
        m.ab.ensure_backend_licence(m.WhisperCppBackend())

    assert checked == [["gated-owner/custom-whisper"]]


def test_custom_local_file_has_no_repository(tmp_path, monkeypatch, m):
    """An ordinary local file is a user asset: no repository is invented."""
    model = tmp_path / "my-model.bin"
    model.write_bytes(b"weights")
    monkeypatch.setenv(m.MODEL_ENV, str(model))

    assert m.ab.backend_model_repos(m.WhisperCppBackend()) == []


def test_checkpoint_name_survives_a_provenance_resolved_model(
    tmp_path, monkeypatch, m, symlink_or_skip
):
    """Resolving the repository target does not disturb the checkpoint name
    the translation guard classifies."""
    repo = _repo_dir(tmp_path, _OWNER)
    blob = _blob(repo, "ggml-base.en-q5_0.bin")
    link = tmp_path / "models" / "ggml-base.en-q5_0.bin"
    link.parent.mkdir(parents=True)
    symlink_or_skip(link, blob)
    monkeypatch.setenv(m.MODEL_ENV, str(link))

    assert m.ab.backend_model_repos(m.WhisperCppBackend()) == ["licensed-owner/custom-whisper"]
    assert m.WhisperCppBackend().supports_translation() is False
