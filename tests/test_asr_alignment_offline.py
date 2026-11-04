"""Public forced alignment must not bypass offline TorchAudio or NLTK caches."""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


_OFFLINE_VARIABLES = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
_TRUE_VALUES = ("1", "ON", "YES", "TRUE", "on", "yes", "true", " True ")
_DEVICES = ("cpu", "cuda", "mps")
_TORCH_MODELS = {"en": "WAV2VEC2_ASR_BASE_960H"}
_HF_MODELS = {"pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish"}
_HF_FILES = ("config.json", "preprocessor_config.json", "vocab.json", "model.safetensors")


@pytest.fixture
def alignment_environment(monkeypatch, tmp_path):
    """Exercise allowed routes; the Windows HIP gate has its own regressions."""
    backend = importlib.import_module("services.asr_backend")
    config = importlib.import_module("core.config")
    pipelines = importlib.import_module("core.windows_rocm.pipelines")
    monkeypatch.setattr(pipelines, "windows_hip_installed", lambda: False)
    monkeypatch.setattr(pipelines, "prepare_audio_imports", lambda: None)
    torch = importlib.import_module("torch")
    state = types.SimpleNamespace(
        backend=backend,
        checkpoint=tmp_path / "torch" / "hub" / "checkpoints"
        / "wav2vec2_fairseq_base_ls960_asr_ls960.pth",
        snapshot=tmp_path / "hf-snapshot",
        punkt_ready=True,
        allow_downloads=False,
        downloads=[],
        snapshot_calls=[],
        model_calls=[],
        align_calls=[],
        model=object(),
        metadata={"language": "en", "dictionary": {"h": 0}, "type": "torchaudio"},
        segments=[{"text": "hello", "start": 0.0, "end": 1.0}],
        aligned=[{
            "text": "hello", "start": 0.1, "end": 0.9,
            "words": [{"word": "hello", "start": 0.1, "end": 0.9}],
        }],
        audio=object(),
    )
    monkeypatch.setattr(backend, "_ALIGN_CACHE", {})
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(torch.hub, "get_dir", lambda: str(state.checkpoint.parent.parent))
    for variable in _OFFLINE_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    def install_model(language):
        if language in _TORCH_MODELS:
            state.checkpoint.parent.mkdir(parents=True, exist_ok=True)
            state.checkpoint.write_bytes(b"fake cached checkpoint")
        else:
            state.snapshot.mkdir(parents=True, exist_ok=True)
            for filename in _HF_FILES:
                (state.snapshot / filename).write_bytes(b"fake cached asset")

    def download_asset(source):
        state.downloads.append(source)
        if not state.allow_downloads:
            raise OSError(f"unexpected {source} downloader delegation")

    def torch_download(*_args, **_kwargs):
        download_asset("torch")
        install_model("en")
        return {}

    monkeypatch.setattr(torch.hub, "load_state_dict_from_url", torch_download)

    def snapshot_download(repo_id, **kwargs):
        state.snapshot_calls.append((repo_id, kwargs))
        assert repo_id == _HF_MODELS["pl"]
        if not state.snapshot.is_dir():
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("no cached alignment snapshot")
            download_asset("hf")
            install_model("pl")
        return str(state.snapshot)

    huggingface_hub = types.ModuleType("huggingface_hub")
    huggingface_hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", huggingface_hub)

    def load_punkt(resource):
        assert resource == "tokenizers/punkt/english.pickle"
        if not state.punkt_ready:
            raise LookupError("no cached Punkt data")
        return object()

    def download_punkt(package, **_kwargs):
        assert package == "punkt_tab"
        download_asset("punkt")
        state.punkt_ready = True
        return True

    nltk_data = types.ModuleType("nltk.data")
    nltk_data.path = []
    nltk_data.load = load_punkt
    nltk = types.ModuleType("nltk")
    nltk.data = nltk_data
    nltk.download = download_punkt
    monkeypatch.setitem(sys.modules, "nltk", nltk)
    monkeypatch.setitem(sys.modules, "nltk.data", nltk_data)

    torchaudio = types.ModuleType("torchaudio")
    torchaudio.pipelines = types.SimpleNamespace(WAV2VEC2_ASR_BASE_960H=types.SimpleNamespace(
        _path=state.checkpoint.name,
    ))
    monkeypatch.setitem(sys.modules, "torchaudio", torchaudio)

    def dependency_load_align_model(language_code, device, model_name=None, model_dir=None):
        state.model_calls.append((language_code, device, model_name, model_dir))
        if language_code in _TORCH_MODELS:
            if not state.checkpoint.is_file():
                torch.hub.load_state_dict_from_url("https://invalid.example/alignment.pth")
        elif language_code in _HF_MODELS:
            if model_name is None or model_name == _HF_MODELS[language_code]:
                snapshot_download(_HF_MODELS[language_code])
            else:
                assert Path(model_name) == state.snapshot
            if not all((state.snapshot / filename).is_file() for filename in _HF_FILES):
                raise OSError("incomplete alignment snapshot")
        else:
            raise ValueError("no default alignment model")
        return state.model, state.metadata

    def dependency_align(segments, model, metadata, audio, device, **kwargs):
        state.align_calls.append((segments, model, metadata, audio, device, kwargs))
        try:
            nltk_data.load("tokenizers/punkt/english.pickle")
        except LookupError:
            nltk.download("punkt_tab", quiet=True)
            nltk_data.load("tokenizers/punkt/english.pickle")
        return {"segments": state.aligned}

    alignment = types.ModuleType("whisperx.alignment")
    alignment.DEFAULT_ALIGN_MODELS_TORCH = dict(_TORCH_MODELS)
    alignment.DEFAULT_ALIGN_MODELS_HF = dict(_HF_MODELS)
    alignment.load_align_model = dependency_load_align_model
    alignment.align = dependency_align
    whisperx = types.ModuleType("whisperx")
    whisperx.__path__ = []
    whisperx.alignment = alignment
    whisperx.load_align_model = dependency_load_align_model
    whisperx.align = dependency_align
    monkeypatch.setitem(sys.modules, "whisperx", whisperx)
    monkeypatch.setitem(sys.modules, "whisperx.alignment", alignment)
    state.install_model = install_model
    return state


def _set_offline(monkeypatch, variable, value="1"):
    for name in _OFFLINE_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, value)


@pytest.mark.parametrize("offline_variable", _OFFLINE_VARIABLES)
@pytest.mark.parametrize("offline_value", _TRUE_VALUES)
@pytest.mark.parametrize("language", ("en", "pl"))
def test_public_loader_rejects_uncached_offline_assets_without_delegating(
    monkeypatch, alignment_environment, offline_variable, offline_value, language,
):
    state = alignment_environment
    _set_offline(monkeypatch, offline_variable, offline_value)

    result = state.backend.load_align_model(language, "cpu")

    assert result is None
    assert state.model_calls == []
    assert state.downloads == []
    assert all(options.get("local_files_only") is True for _, options in state.snapshot_calls)


@pytest.mark.parametrize("offline_variable", _OFFLINE_VARIABLES)
@pytest.mark.parametrize("missing_file", _HF_FILES)
def test_public_loader_rejects_incomplete_offline_hf_snapshot_without_delegating(
    monkeypatch, alignment_environment, offline_variable, missing_file,
):
    state = alignment_environment
    _set_offline(monkeypatch, offline_variable)
    state.install_model("pl")
    (state.snapshot / missing_file).unlink()

    result = state.backend.load_align_model("pl", "cpu")

    assert result is None
    assert state.model_calls == []
    assert state.downloads == []


@pytest.mark.parametrize("offline_variable", _OFFLINE_VARIABLES)
@pytest.mark.parametrize("device", _DEVICES)
@pytest.mark.parametrize("language", ("en", "pl"))
def test_cached_offline_alignment_uses_the_requested_device_and_reuses_the_model(
    monkeypatch, alignment_environment, offline_variable, device, language,
):
    state = alignment_environment
    _set_offline(monkeypatch, offline_variable)
    state.install_model(language)

    first = state.backend.load_align_model(language, device)
    second = state.backend.load_align_model(language, device)
    aligned = state.backend.forced_align(state.segments, state.audio, language, device=device)

    assert first == (state.model, state.metadata)
    assert second is first
    assert aligned is state.aligned
    assert len(state.model_calls) == 1
    assert state.model_calls[0][:2] == (language, device)
    assert len(state.align_calls) == 1
    assert state.align_calls[0][:5] == (
        state.segments, state.model, state.metadata, state.audio, device,
    )
    assert state.downloads == []


@pytest.mark.parametrize("offline_variable", _OFFLINE_VARIABLES)
@pytest.mark.parametrize("device", _DEVICES)
def test_missing_punkt_blocks_offline_alignment_even_for_an_already_loaded_model(
    monkeypatch, alignment_environment, offline_variable, device,
):
    state = alignment_environment
    _set_offline(monkeypatch, offline_variable)
    state.install_model("en")
    assert state.backend.load_align_model("en", device) == (state.model, state.metadata)
    state.punkt_ready = False

    unchanged = state.backend.forced_align(state.segments, state.audio, "en", device=device)
    blocked_calls = list(state.align_calls)
    blocked_downloads = list(state.downloads)
    state.punkt_ready = True
    retried = state.backend.forced_align(state.segments, state.audio, "en", device=device)

    assert unchanged is state.segments
    assert blocked_calls == []
    assert blocked_downloads == []
    assert retried is state.aligned
    assert len(state.model_calls) == 1
    assert len(state.align_calls) == 1
    assert state.downloads == []


@pytest.mark.parametrize("offline_variable", _OFFLINE_VARIABLES)
@pytest.mark.parametrize("language", ("en", "pl"))
def test_missing_punkt_keeps_native_segments_offline_with_cached_weights(
    monkeypatch, alignment_environment, offline_variable, language,
):
    state = alignment_environment
    _set_offline(monkeypatch, offline_variable)
    state.install_model(language)
    state.punkt_ready = False

    result = state.backend.forced_align(state.segments, state.audio, language, device="cpu")

    assert result is state.segments
    assert state.align_calls == []
    assert state.downloads == []


@pytest.mark.parametrize("offline_variable", _OFFLINE_VARIABLES)
@pytest.mark.parametrize("language", ("en", "pl"))
@pytest.mark.parametrize("recovery", ("install_assets", "clear_offline"))
def test_offline_cache_miss_does_not_poison_later_alignment_retries(
    monkeypatch, alignment_environment, offline_variable, language, recovery,
):
    state = alignment_environment
    _set_offline(monkeypatch, offline_variable)
    missing = state.backend.load_align_model(language, "cpu")
    offline_downloads = list(state.downloads)
    if recovery == "install_assets":
        state.install_model(language)
    else:
        monkeypatch.delenv(offline_variable)
        state.allow_downloads = True

    retried = state.backend.load_align_model(language, "cpu")
    aligned = state.backend.forced_align(state.segments, state.audio, language, device="cpu")

    assert missing is None
    assert offline_downloads == []
    assert retried == (state.model, state.metadata)
    assert aligned is state.aligned
    assert state.downloads == (
        [] if recovery == "install_assets" else ["torch" if language == "en" else "hf"]
    )


@pytest.mark.parametrize("offline_variable", _OFFLINE_VARIABLES)
@pytest.mark.parametrize("recovery", ("install_assets", "clear_offline"))
def test_missing_offline_punkt_can_be_retried_after_installation_or_going_online(
    monkeypatch, alignment_environment, offline_variable, recovery,
):
    state = alignment_environment
    _set_offline(monkeypatch, offline_variable)
    state.install_model("en")
    state.punkt_ready = False

    missing = state.backend.load_align_model("en", "cpu")
    offline_downloads = list(state.downloads)
    if recovery == "install_assets":
        state.punkt_ready = True
    else:
        monkeypatch.delenv(offline_variable)
        state.allow_downloads = True

    retried = state.backend.forced_align(state.segments, state.audio, "en", device="cpu")

    assert missing is None
    assert offline_downloads == []
    assert retried is state.aligned
    assert state.downloads == ([] if recovery == "install_assets" else ["punkt"])


@pytest.mark.parametrize("offline_value", (None, "", "0", "FALSE", "off", "no"))
@pytest.mark.parametrize("language", ("en", "pl"))
def test_false_offline_flags_preserve_online_delegation_and_successful_cache(
    monkeypatch, alignment_environment, offline_value, language,
):
    state = alignment_environment
    for variable in _OFFLINE_VARIABLES:
        if offline_value is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, offline_value)
    state.allow_downloads = True

    first = state.backend.load_align_model(language, "cpu")
    second = state.backend.load_align_model(language, "cpu")
    aligned = state.backend.forced_align(state.segments, state.audio, language, device="cpu")

    assert first == (state.model, state.metadata)
    assert second is first
    assert aligned is state.aligned
    assert state.model_calls == [(language, "cpu", None, None)]
    assert state.downloads == ["torch" if language == "en" else "hf"]
