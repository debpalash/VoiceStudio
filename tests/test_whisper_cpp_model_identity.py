"""A cached checkpoint's NAME must survive resolution, not its blob hash.

A Hugging Face cache entry for a whisper.cpp model is a symlink from
``snapshots/<rev>/ggml-<name>.bin`` into ``blobs/<sha256>``. Canonicalising
that link leaves the content hash as the file name, so the translation guard
classifies the hash instead of the checkpoint: an English-only, Turbo or
Distil checkpoint then looks multilingual and translation runs on
untranslated source audio labelled English. These tests drive the real
resolver (no mocked ``resolve_model``) over all four model sources;
repository provenance is covered in ``test_whisper_cpp_model_provenance``.
"""
from __future__ import annotations

import hashlib

import pytest

pytest_plugins = ["whisper_cpp_fixtures"]

#: Named checkpoints that must never translate, and one that may.
UNSUPPORTED = (
    "ggml-base.en-q5_0.bin",
    "ggml-large-v3-turbo.bin",
    "ggml-distil-large-v3.bin",
)
SUPPORTED = "ggml-base.bin"

#: Fake HF repo folder; the licence repo id is derived from it.
_REPO = "licensed-owner--custom-whisper"


def _blob(root, filename):
    """The content-addressed file a cached checkpoint points at."""
    blob = root / "blobs" / hashlib.sha256(filename.encode()).hexdigest()
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"weights")
    return blob


def _hf_cache(tmp_path, filename, symlink_or_skip):
    """An HF-like cache: ``blobs/<sha256>`` holding the weights and
    ``snapshots/<rev>/<filename>`` symlinked to it — the layout
    ``huggingface_hub`` hands out for a downloaded model."""
    repo_dir = tmp_path / "hf-cache" / f"models--{_REPO}"
    blob = _blob(repo_dir, filename)
    snapshot = repo_dir / "snapshots" / "rev0"
    snapshot.mkdir(parents=True, exist_ok=True)
    link = snapshot / filename
    symlink_or_skip(link, blob)
    return link


def _serve_from_cache(monkeypatch, link):
    """Make the HF cache lookup return ``link`` (the named snapshot entry)."""
    import huggingface_hub

    def fake(repo_id, filename, *args, **kwargs):
        return str(link) if filename == link.name else None

    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", fake)


def test_hf_snapshot_keeps_checkpoint_name(tmp_path, monkeypatch, m, symlink_or_skip):
    """The resolved path keeps the checkpoint's own name, not the blob hash."""
    link = _hf_cache(tmp_path, "ggml-base.en-q5_0.bin", symlink_or_skip)
    _serve_from_cache(monkeypatch, link)
    monkeypatch.delenv(m.MODEL_ENV, raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    resolved = m.resolve_model("ggml-base.en-q5_0.bin")

    assert resolved is not None
    assert resolved.is_absolute()
    assert resolved.name == "ggml-base.en-q5_0.bin"
    assert resolved.stem == "ggml-base.en-q5_0"
    # The weights stay physically readable through the named entry.
    assert resolved.read_bytes() == b"weights"


@pytest.mark.parametrize(
    "filename, expected",
    [(name, False) for name in UNSUPPORTED] + [(SUPPORTED, True)],
)
def test_cached_checkpoint_name_decides_translation(
    tmp_path, monkeypatch, m, symlink_or_skip, filename, expected
):
    """A cached checkpoint is classified by its name: English-only, Turbo and
    Distil refuse translation; a multilingual checkpoint still allows it."""
    link = _hf_cache(tmp_path, filename, symlink_or_skip)
    _serve_from_cache(monkeypatch, link)
    # A bare checkpoint name resolves through the HF cache, not a local path.
    monkeypatch.setenv(m.MODEL_ENV, filename)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    # The resolver really hands back the named checkpoint, not a guess.
    resolved = m.resolve_model()
    assert resolved is not None
    assert resolved.name == filename

    assert m.WhisperCppBackend().supports_translation() is expected


def test_direct_model_path_keeps_checkpoint_name(tmp_path, monkeypatch, m, symlink_or_skip):
    """A model selected by path keeps its name too — the path is the only
    place the checkpoint identity exists for a symlinked download."""
    link = _hf_cache(tmp_path, "ggml-large-v3-turbo.bin", symlink_or_skip)
    monkeypatch.setenv(m.MODEL_ENV, str(link))

    resolved = m.resolve_model()
    assert resolved is not None
    assert resolved.name == "ggml-large-v3-turbo.bin"
    assert m.WhisperCppBackend().supports_translation() is False


def test_package_model_keeps_checkpoint_name(tmp_path, monkeypatch, m, symlink_or_skip):
    """The package-local models directory resolves to the named checkpoint."""
    from engines.whisper_cpp import bootstrap

    pkg_dir = tmp_path / "pkg-models"
    pkg_dir.mkdir()
    # A user may point a package-local name at a cached download.
    symlink_or_skip(
        pkg_dir / "ggml-base.en-q5_0.bin",
        _blob(tmp_path / "pkg", "ggml-base.en-q5_0.bin"),
    )
    monkeypatch.setattr(bootstrap, "_PKG_MODELS_DIR", pkg_dir)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv(m.MODEL_ENV, raising=False)

    resolved = m.resolve_model("ggml-base.en-q5_0.bin")
    assert resolved is not None
    assert resolved.name == "ggml-base.en-q5_0.bin"
    assert m.WhisperCppBackend().supports_translation() is False


def test_user_cache_model_keeps_checkpoint_name(tmp_path, monkeypatch, m, symlink_or_skip):
    """The user whisper cache resolves to the named checkpoint."""
    cache_dir = tmp_path / ".cache" / "whisper"
    cache_dir.mkdir(parents=True)
    symlink_or_skip(
        cache_dir / "ggml-distil-large-v3.bin",
        _blob(tmp_path / "user-cache", "ggml-distil-large-v3.bin"),
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv(m.MODEL_ENV, raising=False)

    resolved = m.resolve_model("ggml-distil-large-v3.bin")
    assert resolved is not None
    assert resolved.name == "ggml-distil-large-v3.bin"
    assert m.WhisperCppBackend().supports_translation() is False


def test_missing_model_resolves_to_nothing(tmp_path, monkeypatch, m):
    """A model that is nowhere on disk resolves to None, not to a guess."""
    import huggingface_hub

    monkeypatch.delenv(m.MODEL_ENV, raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **k: None)

    assert m.resolve_model("ggml-absent.bin") is None
    assert m.WhisperCppBackend().supports_translation() is False


def test_broken_snapshot_link_resolves_to_nothing(tmp_path, monkeypatch, m, symlink_or_skip):
    """A dangling cache entry is not a model: it resolves to None."""
    link = tmp_path / "hf-cache" / f"models--{_REPO}" / "snapshots" / "rev0" / "ggml-base.bin"
    link.parent.mkdir(parents=True)
    symlink_or_skip(link, tmp_path / "hf-cache" / "blobs" / "gone")
    _serve_from_cache(monkeypatch, link)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv(m.MODEL_ENV, raising=False)

    assert m.resolve_model("ggml-base.bin") is None
    assert m.WhisperCppBackend().supports_translation() is False


def test_rejected_translation_never_launches_the_cli(tmp_path, monkeypatch, m, symlink_or_skip):
    """Translation refused for the checkpoint never starts the CLI."""
    link = _hf_cache(tmp_path, "ggml-base.en-q5_0.bin", symlink_or_skip)
    _serve_from_cache(monkeypatch, link)
    monkeypatch.setenv(m.MODEL_ENV, str(link))
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(m.wc, "locate_binary", lambda: tmp_path / "whisper-cli")
    monkeypatch.setattr(m.wc, "_ensure_wav_16k", lambda path, tmp: path)
    launched = []

    def _launched(*args, **kwargs):
        launched.append(args)
        raise AssertionError("whisper.cpp CLI launched for a rejected translation")

    monkeypatch.setattr(m.wc, "run_transcription", _launched)

    with pytest.raises(ValueError, match="translation"):
        m.WhisperCppBackend().transcribe(str(audio), task="translate")

    assert launched == []


def test_missing_explicit_model_path_never_loads_a_same_named_cached_model(tmp_path, monkeypatch, m):
    """A missing explicit path resolves to None instead of another cache's weights."""
    cache_dir = tmp_path / ".cache" / "whisper"
    cache_dir.mkdir(parents=True)
    (cache_dir / "ggml-base.bin").write_bytes(b"other weights")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv(m.MODEL_ENV, str(tmp_path / "chosen" / "ggml-base.bin"))

    assert m.resolve_model() is None
    # A bare name still searches the caches.
    monkeypatch.setenv(m.MODEL_ENV, "ggml-base.bin")
    assert m.resolve_model() == cache_dir / "ggml-base.bin"
