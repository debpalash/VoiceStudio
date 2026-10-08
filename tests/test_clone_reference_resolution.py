"""#2442: cloning must find the speech-to-text model the user already installed.

Model Catalogue installs every checkpoint at a pinned commit and never writes
the ``main`` ref, so ``snapshot_download(repo, local_files_only=True)`` — the
only lookup OmniVoice's implicit-ASR fallback made — reported an installed
Whisper as missing. Voice cloning then failed with "needs an installed
speech-to-text model" although the Clone page's transcribe button (which asks
the selected engine) worked, and a long reference that a supplied transcript
cannot align got the same misleading advice.

One resolution order, simulated end to end here:
  supplied/stored transcript -> installed engine (transcribe_reference)
  -> the model's own cached Whisper (any installed snapshot, pinned or not)
  -> a typed error that names the real cause.
"""
import importlib
import os
from types import SimpleNamespace

import pytest
import soundfile as sf
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

SR = 24_000
PINNED = "06f233fe06e710322aca913c1bc4249a0d71fce1"


def _tts():
    return importlib.import_module("services.tts_backend")


def _wav(path, seconds, value=0.1):
    sf.write(path, torch.full((int(seconds * SR),), value).numpy(), SR)
    return str(path)


class _Tokenizer:
    config = SimpleNamespace(hop_length=320)
    device = "cpu"

    def encode(self, audio):
        return SimpleNamespace(audio_codes=torch.zeros((1, 1, 1), dtype=torch.long))


def _model():
    from omnivoice.models.omnivoice import OmniVoice

    model = OmniVoice.__new__(OmniVoice)
    model.sampling_rate = SR
    model.audio_tokenizer = _Tokenizer()
    model._asr_pipe = None
    model.transcribe = lambda _audio: "Words from the cached Whisper."
    model.loaded = []

    def load(*, model_name):
        model.loaded.append(model_name)
        model._asr_pipe = object()

    model.load_asr_model = load
    return model


@pytest.fixture()
def hf_cache(tmp_path, monkeypatch):
    """An empty, isolated Hugging Face cache; nothing from the host leaks in."""
    from huggingface_hub import constants

    cache = tmp_path / "hf"
    cache.mkdir()
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME",
                 "OMNIVOICE_PYTORCH_ASR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return cache


def _install_pinned(cache, repo="openai/whisper-large-v3", complete=True):
    """The layout Model Catalogue leaves behind: a snapshot, no refs/main."""
    snapshot = cache / ("models--" + repo.replace("/", "--")) / "snapshots" / PINNED
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}")
    if complete:
        _complete(snapshot)
    return snapshot


def _complete(snapshot):
    for name in ("preprocessor_config.json", "tokenizer.json"):
        (snapshot / name).write_text("{}")
    (snapshot / "model.safetensors").write_bytes(b"0" * 16)


@pytest.fixture()
def no_prompt_disk_cache(monkeypatch):
    tts = _tts()
    monkeypatch.setattr(tts, "_prompt_disk_dir", lambda: None)
    tts.clear_clone_prompt_cache()
    yield
    tts.clear_clone_prompt_cache()


@pytest.mark.parametrize("seconds", [4, 25])
def test_catalogue_installed_whisper_without_a_main_ref_is_reused(
    tmp_path, hf_cache, monkeypatch, seconds
):
    """Short or long, an installed pinned snapshot is found and loaded."""
    snapshot = _install_pinned(hf_cache)
    # No recognizer from the registry produced words (e.g. the selected engine
    # is the PyTorch pipeline, which transcribe_reference leaves to the model).
    import services.asr_backend as ab

    monkeypatch.setattr(ab, "transcribe_reference", lambda *_a, **_k: None)
    model = _model()

    prompt = model.create_voice_clone_prompt(
        _wav(tmp_path / "ref.wav", seconds), None, preprocess_prompt=False
    )

    assert model.loaded == [str(snapshot)]
    assert prompt.ref_text.startswith("Words from the cached Whisper")


def test_configured_pytorch_model_is_preferred(tmp_path, hf_cache, monkeypatch):
    other = _install_pinned(hf_cache, "openai/whisper-large-v3")
    chosen = _install_pinned(hf_cache, "openai/whisper-small")
    monkeypatch.setenv("OMNIVOICE_PYTORCH_ASR_MODEL", "openai/whisper-small")
    model = _model()

    model.create_voice_clone_prompt(
        _wav(tmp_path / "ref.wav", 3), None, preprocess_prompt=False
    )

    assert model.loaded == [str(chosen)]
    assert str(other) not in model.loaded


def test_partial_snapshot_is_not_loaded(tmp_path, hf_cache):
    """A config-only snapshot is a truncated download, not an installed model."""
    _install_pinned(hf_cache, complete=False)
    model = _model()

    with pytest.raises(ValueError, match="installed speech-to-text model"):
        model.create_voice_clone_prompt(
            _wav(tmp_path / "ref.wav", 3), None, preprocess_prompt=False
        )
    assert model.loaded == []


def test_short_reference_without_any_asr_asks_for_a_transcript(tmp_path, hf_cache):
    model = _model()

    with pytest.raises(ValueError) as caught:
        model.create_voice_clone_prompt(
            _wav(tmp_path / "ref.wav", 5), None, preprocess_prompt=False
        )

    message = str(caught.value)
    assert "installed speech-to-text model" in message
    assert "reference transcript" in message
    assert "too long" not in message


def test_long_reference_without_any_asr_names_its_length(tmp_path, hf_cache):
    """A transcript cannot rescue a 35 s clip, so the advice is to trim it."""
    model = _model()

    with pytest.raises(ValueError) as caught:
        model.create_voice_clone_prompt(
            _wav(tmp_path / "long.wav", 35), None, preprocess_prompt=False
        )

    message = str(caught.value)
    assert "[clone_ref_too_long]" in message
    assert "35.0" in message and "3-10 second" in message


def test_installed_engine_transcript_needs_no_model_whisper(
    tmp_path, hf_cache, monkeypatch, no_prompt_disk_cache
):
    """ASR installed through the catalogue, no Whisper in the model's cache:
    the engine's transcript is used and the model fallback is never consulted."""
    import services.asr_backend as ab

    calls = []
    monkeypatch.setattr(
        ab, "transcribe_reference", lambda path, **_k: calls.append(path) or "Engine words."
    )
    model = _model()
    model._load_cached_reference_asr = lambda: (_ for _ in ()).throw(
        AssertionError("model Whisper consulted")
    )

    prompt = _tts()._get_clone_prompt(model, _wav(tmp_path / "ref.wav", 6), None)

    assert prompt is not None and prompt.ref_text.startswith("Engine words")
    assert len(calls) == 1


def test_supplied_transcript_never_looks_for_asr(
    tmp_path, hf_cache, monkeypatch, no_prompt_disk_cache
):
    import services.asr_backend as ab

    monkeypatch.setattr(
        ab, "transcribe_reference",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ASR not needed")),
    )
    model = _model()
    model._load_cached_reference_asr = lambda: (_ for _ in ()).throw(
        AssertionError("model Whisper consulted")
    )

    prompt = _tts()._get_clone_prompt(
        model, _wav(tmp_path / "ref.wav", 6), "Typed words."
    )

    assert prompt is not None and prompt.ref_text.startswith("Typed words")


def test_stored_transcript_on_a_long_clip_still_clones_from_catalogue_whisper(
    tmp_path, hf_cache, monkeypatch, no_prompt_disk_cache
):
    """The reported flow: a 35 s voice saved with its transcript, Whisper only
    in the catalogue layout, no engine transcript for the windows."""
    snapshot = _install_pinned(hf_cache)
    import services.asr_backend as ab

    monkeypatch.setattr(ab, "transcribe_reference", lambda *_a, **_k: None)
    model = _model()
    original = _wav(tmp_path / "long.wav", 35)

    prompt = _tts()._get_clone_prompt(model, original, "The whole clip transcript.")

    assert prompt is not None
    assert model.loaded == [str(snapshot)]
    assert prompt.ref_text.startswith("Words from the cached Whisper")


def test_newest_incomplete_snapshot_yields_to_an_older_complete_one(
    tmp_path, hf_cache
):
    """Config plus one weight file is not enough: tokenizer and feature
    extractor settings are needed too, so the older whole snapshot wins."""
    import time

    whole = _install_pinned(hf_cache)
    newer = whole.parent / ("a" * 40)
    newer.mkdir()
    (newer / "config.json").write_text("{}")
    (newer / "model.safetensors").write_bytes(b"0" * 16)
    later = time.time() + 60
    os.utime(newer, (later, later))
    model = _model()

    model.create_voice_clone_prompt(
        _wav(tmp_path / "ref.wav", 3), None, preprocess_prompt=False
    )

    assert model.loaded == [str(whole)]


def test_sharded_snapshot_missing_a_shard_is_incomplete(tmp_path):
    import json

    from omnivoice.models.omnivoice import _has_asr_weights

    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "config.json").write_text("{}")
    _complete(snapshot)
    (snapshot / "model.safetensors").unlink()
    (snapshot / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"a": "m-1.safetensors", "b": "m-2.safetensors"}})
    )
    (snapshot / "m-1.safetensors").write_bytes(b"0")
    assert not _has_asr_weights(str(snapshot))
    (snapshot / "m-2.safetensors").write_bytes(b"0")
    assert _has_asr_weights(str(snapshot))
