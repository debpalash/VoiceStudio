"""Native Windows CTranslate2 HIP requires evidence, not torch's CUDA alias."""
from __future__ import annotations

import os
import types

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core.device_caps import HostCaps
from services import asr_backend as ab


@pytest.fixture(autouse=True)
def clear_hip_probe():
    ab._windows_ct2_hip_ready.cache_clear()
    yield
    ab._windows_ct2_hip_ready.cache_clear()


def test_hip_wheel_requires_both_native_markers_and_live_isolated_probe(monkeypatch, tmp_path):
    library = tmp_path / "ctranslate2.dll"
    monkeypatch.setattr(ab, "_ct2_hip_dll", lambda: library)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="1\n")

    monkeypatch.setattr(ab.subprocess, "run", run)
    library.write_bytes(b"cublas64_12.dll\x00")
    assert ab._windows_ct2_hip_ready() is False
    assert not calls

    ab._windows_ct2_hip_ready.cache_clear()
    library.write_bytes(b"hipblas.dll\x00amdhip64_7.dll\x00")
    assert ab._windows_ct2_hip_ready() is True
    assert ab._windows_ct2_hip_ready() is True
    assert len(calls) == 1
    assert calls[0][0][0] == ab.sys.executable
    assert calls[0][1]["timeout"] > 0


@pytest.mark.parametrize("returncode,stdout", [(1, "1\n"), (-1073740791, "1\n"), (0, "0\n")])
def test_hip_wheel_probe_must_see_a_live_device(monkeypatch, tmp_path, returncode, stdout):
    library = tmp_path / "ctranslate2.dll"
    library.write_bytes(b"hipblas.dll\x00amdhip64_7.dll\x00")
    monkeypatch.setattr(ab, "_ct2_hip_dll", lambda: library)
    monkeypatch.setattr(
        ab.subprocess, "run",
        lambda *_args, **_kwargs: types.SimpleNamespace(returncode=returncode, stdout=stdout),
    )
    assert ab._windows_ct2_hip_ready() is False


def test_hip_wheel_probe_crash_or_timeout_never_crashes_backend(monkeypatch, tmp_path):
    library = tmp_path / "ctranslate2.dll"
    library.write_bytes(b"hipblas.dll\x00amdhip64_7.dll\x00")
    monkeypatch.setattr(ab, "_ct2_hip_dll", lambda: library)

    def fail(*_args, **_kwargs):
        raise ab.subprocess.TimeoutExpired("python", 15)

    monkeypatch.setattr(ab.subprocess, "run", fail)
    assert ab._windows_ct2_hip_ready() is False


def test_rocm_hip_and_cuda_pick_ct2_gpu_only_when_host_matches(monkeypatch):
    host = {"family": "rocm"}
    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: HostCaps(family=host["family"], available_families=(host["family"], "cpu")),
    )
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(ab, "_windows_ct2_hip_ready", lambda: True)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    assert ab._ctranslate2_cuda_ok() is True
    assert ab.WhisperXBackend._pick_device() == ("cuda", "float16")

    monkeypatch.setattr(ab, "_windows_ct2_hip_ready", lambda: False)
    assert ab._ctranslate2_cuda_ok() is False
    assert ab.WhisperXBackend._pick_device() == ("cpu", "int8")

    host["family"] = "cpu"
    monkeypatch.setattr(ab, "_windows_ct2_hip_ready", lambda: True)
    assert ab._ctranslate2_cuda_ok() is False

    host["family"] = "cuda"
    assert ab._ctranslate2_cuda_ok() is False
    monkeypatch.setattr(ab, "_rocm_torch", lambda: False)
    assert ab._ctranslate2_cuda_ok() is True


def test_rocm_verified_hip_prefers_whisperx_then_faster_whisper(monkeypatch):
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: True)
    monkeypatch.setattr(
        ab, "_probe_available", lambda cls: cls.id in {"whisperx", "faster-whisper", "pytorch-whisper"},
    )
    assert ab._auto_detect() == "whisperx"
    monkeypatch.setattr(
        ab, "_probe_available", lambda cls: cls.id in {"faster-whisper", "pytorch-whisper"},
    )
    assert ab._auto_detect() == "faster-whisper"

    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: False)
    assert ab._auto_detect() == "pytorch-whisper"


def test_inventory_rocm_only_for_verified_native_ct2(monkeypatch):
    caps = HostCaps(family="rocm", available_families=("rocm", "cpu"))
    monkeypatch.setattr(ab.sys, "platform", "win32")
    monkeypatch.setattr(ab, "_windows_rocm_whisperx_status", lambda: (False, "alignment unavailable"))
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: False)
    assert ab._asr_gpu_compat(ab.WhisperXBackend, caps) == ("cuda", "cpu")
    assert ab._asr_gpu_compat(ab.FasterWhisperBackend, caps) == ("cuda", "cpu")
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: True)
    assert ab._asr_gpu_compat(ab.WhisperXBackend, caps) == ("cuda", "cpu")
    assert ab._asr_gpu_compat(ab.FasterWhisperBackend, caps) == ("cuda", "rocm", "cpu")
    monkeypatch.setattr(ab, "_windows_rocm_whisperx_status", lambda: (True, "verified"))
    for backend in (ab.WhisperXBackend, ab.FasterWhisperBackend):
        assert ab._asr_gpu_compat(backend, caps) == ("cuda", "rocm", "cpu")
    assert ab._asr_gpu_compat(
        ab.WhisperXBackend, HostCaps(family="cpu", available_families=("cpu",))
    ) == ("cuda", "cpu")


def test_faster_whisper_uses_verified_hip_gpu(monkeypatch):
    loads = []
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: True)
    monkeypatch.setattr(ab, "_ctranslate2_execstack_ok", lambda: (True, "ready"))
    monkeypatch.setitem(
        ab.sys.modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=lambda *args, **kwargs: loads.append(kwargs) or object()),
    )
    backend = ab.FasterWhisperBackend(model_name="cached-model")
    backend._ensure_model()
    assert loads == [{"device": "cuda", "compute_type": "float16"}]


def test_rocm_does_not_need_nvidia_cudnn_or_claim_ct2_is_cpu_only(monkeypatch):
    from core import cudnn8

    monkeypatch.setitem(
        ab.sys.modules, "torch", types.SimpleNamespace(version=types.SimpleNamespace(hip="7.2")),
    )
    needed, reason = cudnn8._torch_wants_cudnn8()
    assert needed is False
    assert "HIP" in reason
    assert "runs on CPU" not in reason


def test_windows_rocm_broken_whisperx_import_falls_back_to_hip_faster_whisper(monkeypatch):
    from services import asr_backend as ab

    monkeypatch.setattr(ab.sys, "platform", "win32")
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: True)
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_ctranslate2_execstack_ok", lambda: (True, "ready"))
    monkeypatch.setattr(ab, "_ctranslate2_cudnn_ok", lambda: (True, "ready"))
    monkeypatch.setattr(
        ab, "_windows_rocm_whisperx_status",
        lambda: (False, "WhisperX ASR import failed: torchaudio.AudioMetaData is missing"),
    )
    monkeypatch.setattr(
        ab.FasterWhisperBackend, "is_available", classmethod(lambda cls: (True, "ready")),
    )
    assert ab._auto_detect() == "faster-whisper"
    assert ab.active_backend_id() == "faster-whisper"
    assert ab.WhisperXBackend.is_available() == (
        False, "WhisperX ASR import failed: torchaudio.AudioMetaData is missing",
    )


def test_explicit_windows_rocm_whisperx_reports_import_failure_in_inventory(monkeypatch):
    monkeypatch.setattr(ab.sys, "platform", "win32")
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_windows_rocm_whisperx_status", lambda: (False, "torchaudio.AudioMetaData is missing"))
    monkeypatch.setattr(ab, "_ctranslate2_execstack_ok", lambda: (True, "ready"))
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: True)
    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: HostCaps(family="rocm", available_families=("rocm", "cpu")),
    )
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "whisperx")
    monkeypatch.setattr(ab, "_REGISTRY", {"whisperx": ab.WhisperXBackend})
    assert ab.active_backend_id() == "whisperx"
    row, = ab.list_backends()
    assert row["available"] is False
    assert "torchaudio.AudioMetaData" in row["reason"]
    assert "rocm" not in row["gpu_compat"]
    assert row["routing_status"] == "cpu_fallback"


def test_windows_rocm_whisperx_native_import_failure_is_isolated(monkeypatch):
    ab._windows_rocm_whisperx_status.cache_clear()

    def run(command, **kwargs):
        assert command[0] == ab.sys.executable
        assert "whisperx.asr" in command[2]
        assert kwargs["timeout"] > 0
        return types.SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Traceback\nAttributeError: module 'torchaudio' has no attribute 'AudioMetaData'\n",
        )

    monkeypatch.setattr(ab.subprocess, "run", run)
    ok, reason = ab._windows_rocm_whisperx_status()
    assert ok is False
    assert "AttributeError: module 'torchaudio' has no attribute 'AudioMetaData'" in reason
    ab._windows_rocm_whisperx_status.cache_clear()


def test_whisperx_import_alone_does_not_prove_windows_rocm_alignment(monkeypatch):
    ab._windows_rocm_whisperx_status.cache_clear()
    monkeypatch.setattr(
        ab.subprocess, "run",
        lambda *_args, **_kwargs: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    try:
        ok, reason = ab._windows_rocm_whisperx_status()
        assert ok is False
        assert "full ASR-and-alignment pipeline" in reason
    finally:
        ab._windows_rocm_whisperx_status.cache_clear()


def test_whisperx_crashed_import_probe_never_blocks_backend(monkeypatch):
    ab._windows_rocm_whisperx_status.cache_clear()
    monkeypatch.setattr(
        ab.subprocess, "run",
        lambda *_args, **_kwargs: types.SimpleNamespace(returncode=-1073740791, stdout="", stderr=""),
    )
    try:
        ok, reason = ab._windows_rocm_whisperx_status()
        assert ok is False
        assert "exit -1073740791" in reason
    finally:
        ab._windows_rocm_whisperx_status.cache_clear()


def test_windows_rocm_faster_whisper_aligns_on_transcription_request_without_env(monkeypatch):
    import numpy as np

    monkeypatch.delenv("OMNIVOICE_FAST_WHISPER_ROCM_ALIGN", raising=False)
    monkeypatch.setattr(ab.sys, "platform", "win32")
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: True)
    monkeypatch.setattr(ab, "_decode_audio_16k_mono", lambda path: np.zeros(16000, dtype=np.float32))
    events = []
    native_word = types.SimpleNamespace(word=" hello", start=0.1, end=0.8, probability=0.9)
    segment = types.SimpleNamespace(text=" hello", start=0.1, end=0.8, words=[native_word])
    fake_model = types.SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([segment]), types.SimpleNamespace(language="en", language_probability=0.99, duration=1.0)))
    alignment = types.SimpleNamespace(align=lambda segments, model, metadata, audio, device, **kwargs: (
        events.append((segments, device)) or {"segments": [{"text": " hello", "start": 0.12, "end": 0.78, "words": [{"word": "hello", "start": 0.12, "end": 0.78, "score": 0.88}]}]}
    ))
    loads = []
    monkeypatch.setattr(ab, "_load_installed_align_model", lambda language, device: (loads.append((language, device)) or (alignment, object(), {})))
    backend = ab.FasterWhisperBackend(model_name="local-model")
    backend._model = fake_model
    backend._device = "cuda"
    result = backend.transcribe("demo.wav", word_timestamps=True)
    assert events[0][1] == "cuda"
    assert result["segments"][0]["words"][0]["score"] == 0.88
    assert result["chunks"][0]["timestamp"] == (0.12, 0.78)
    assert result["language"] == "en"
    backend.transcribe("demo.wav", word_timestamps=True)
    assert loads == [("en", "cuda")]
    backend.transcribe("demo.wav", word_timestamps=False)
    assert loads == [("en", "cuda")]


@pytest.mark.parametrize("platform,rocm,ct2_ready", [
    ("win32", False, True), ("linux", True, True), ("win32", True, False),
])
def test_other_hosts_and_unverified_wheel_do_not_import_alignment(monkeypatch, platform, rocm, ct2_ready):
    monkeypatch.delenv("OMNIVOICE_FAST_WHISPER_ROCM_ALIGN", raising=False)
    monkeypatch.setattr(ab.sys, "platform", platform)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: rocm)
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: ct2_ready)
    monkeypatch.setattr(ab, "_load_installed_align_model", lambda *_args: pytest.fail("unexpected alignment"))
    segment = types.SimpleNamespace(
        text=" hi", start=0.1, end=0.5,
        words=[types.SimpleNamespace(word=" hi", start=0.1, end=0.5, probability=0.9)],
    )
    backend = ab.FasterWhisperBackend(model_name="local-model")
    backend._model = types.SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([segment]), types.SimpleNamespace(language="en", language_probability=0.9, duration=1.0)))
    result = backend.transcribe("demo.wav")
    assert result["segments"][0]["words"][0]["probability"] == 0.9


@pytest.mark.parametrize("platform,rocm,expected", [
    ("win32", True, True), ("win32", False, False), ("linux", True, False),
])
def test_alignment_default_is_windows_verified_rocm_only(monkeypatch, platform, rocm, expected):
    monkeypatch.delenv("OMNIVOICE_FAST_WHISPER_ROCM_ALIGN", raising=False)
    monkeypatch.setattr(ab.sys, "platform", platform)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: rocm)
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: True)
    assert ab._windows_rocm_fast_align_requested() is expected
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: False)
    assert ab._windows_rocm_fast_align_requested() is False


def test_rocm_alignment_does_not_align_translated_text(monkeypatch):
    monkeypatch.setattr(ab.sys, "platform", "win32")
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: True)
    monkeypatch.setattr(ab, "_load_installed_align_model", lambda *_args: pytest.fail("translated text was aligned"))
    backend = ab.FasterWhisperBackend(model_name="local-model")
    backend._model = types.SimpleNamespace(transcribe=lambda *args, **kwargs: (
        iter([types.SimpleNamespace(text=" hello", start=0.1, end=0.8, words=[])]),
        types.SimpleNamespace(language="en", language_probability=0.9, duration=1.0),
    ))
    backend._device = "cuda"
    assert backend.transcribe("demo.wav", task="translate")["language"] == "en"


def test_english_aligner_uses_cached_checkpoint_and_punkt_offline(monkeypatch, tmp_path):
    import nltk
    import torch
    import torchaudio
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(torch.hub, "get_dir", lambda: str(tmp_path))
    model_name = "WAV2VEC2_ASR_BASE_960H"
    checkpoint = tmp_path / "checkpoints" / "wav2vec2_fairseq_base_ls960_asr_ls960.pth"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"already cached")
    monkeypatch.setattr(nltk.data, "load", lambda *_args: object())
    monkeypatch.setattr(nltk, "download", lambda *_args, **_kwargs: pytest.fail("punkt already cached"))
    loaded = []
    monkeypatch.setattr(torchaudio.pipelines, model_name, types.SimpleNamespace(_path=checkpoint.name))
    monkeypatch.setitem(ab.sys.modules, "whisperx.alignment", types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={"en": model_name}, DEFAULT_ALIGN_MODELS_HF={},
        load_align_model=lambda language, device, **kwargs: (loaded.append((language, device, kwargs)) or (object(), {})),
    ))
    ab._load_installed_align_model("en", "cuda")
    assert loaded == [("en", "cuda", {"model_name": model_name, "model_dir": str(checkpoint.parent)})]


@pytest.mark.parametrize('offline_variable', ['HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE'])
@pytest.mark.parametrize('offline_value', ['1', 'true', 'YES', 'ON'])
def test_french_aligner_uses_cached_gpu_checkpoint(monkeypatch, tmp_path, offline_variable, offline_value):
    import nltk
    import torch
    import torchaudio
    from core import config

    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    monkeypatch.delenv('TRANSFORMERS_OFFLINE', raising=False)
    monkeypatch.setenv(offline_variable, offline_value)
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(torch.hub, 'get_dir', lambda: str(tmp_path))
    checkpoint = tmp_path / 'checkpoints' / 'french.pth'
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b'cached')
    monkeypatch.setattr(nltk.data, 'load', lambda *_args: object())
    model_name = 'VOXPOPULI_ASR_BASE_10K_FR'
    monkeypatch.setattr(torchaudio.pipelines, model_name, types.SimpleNamespace(_path=checkpoint.name))
    loaded = []
    monkeypatch.setitem(ab.sys.modules, 'whisperx.alignment', types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={'fr': model_name}, DEFAULT_ALIGN_MODELS_HF={},
        load_align_model=lambda language, device, **kwargs: (
            loaded.append((language, device, kwargs)) or (object(), {})
        ),
    ))

    ab._load_installed_align_model('fr', 'cuda')
    assert loaded == [('fr', 'cuda', {'model_name': model_name, 'model_dir': str(checkpoint.parent)})]


@pytest.mark.parametrize('offline_variable', ['HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE'])
@pytest.mark.parametrize('offline_value', ['1', 'true', 'YES', 'ON'])
def test_dutch_aligner_uses_cached_hf_snapshot_on_gpu(monkeypatch, tmp_path, offline_variable, offline_value):
    import huggingface_hub
    import nltk
    from core import config

    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    monkeypatch.delenv('TRANSFORMERS_OFFLINE', raising=False)
    monkeypatch.setenv(offline_variable, offline_value)
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(nltk.data, 'load', lambda *_args: object())
    for name in ('config.json', 'preprocessor_config.json', 'vocab.json', 'model.safetensors'):
        (tmp_path / name).write_bytes(b'cached')
    monkeypatch.setattr(huggingface_hub, 'snapshot_download', lambda *_args, **_kwargs: str(tmp_path))
    loaded = []
    monkeypatch.setitem(ab.sys.modules, 'whisperx.alignment', types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={}, DEFAULT_ALIGN_MODELS_HF={'nl': 'owner/dutch'},
        load_align_model=lambda language, device, **kwargs: (
            loaded.append((language, device, kwargs)) or (object(), {})
        ),
    ))

    ab._load_installed_align_model('nl', 'cuda')
    assert loaded == [('nl', 'cuda', {'model_name': str(tmp_path)})]


def test_missing_punkt_downloads_only_on_transcription_into_persistent_cache(monkeypatch, tmp_path):
    import nltk
    from core import config

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    state = {"installed": False}

    def load_data(*_args):
        if not state["installed"]:
            raise LookupError("missing")
        return object()

    monkeypatch.setattr(nltk.data, "load", load_data)
    calls = []

    def download(package, **kwargs):
        calls.append((package, kwargs))
        state["installed"] = True
        return True

    monkeypatch.setattr(nltk, "download", download)
    for filename in ("config.json", "preprocessor_config.json", "vocab.json", "pytorch_model.bin"):
        (tmp_path / filename).write_bytes(b"cached")
    monkeypatch.setitem(ab.sys.modules, "whisperx.alignment", types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={}, DEFAULT_ALIGN_MODELS_HF={"pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish"},
        load_align_model=lambda *_args, **_kwargs: (object(), {}),
    ))
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *_args, **_kwargs: str(tmp_path))
    assert not calls
    ab._load_installed_align_model("pl", "cuda")
    assert calls == [("punkt_tab", {"download_dir": str(tmp_path / "nltk_data"), "quiet": True})]
    assert str(tmp_path / "nltk_data") in nltk.data.path


@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("offline_value", ["", "0", "FALSE", "off", "no"])
def test_polish_aligner_downloads_only_on_cache_miss(monkeypatch, tmp_path, cached, offline_value):
    import nltk
    import huggingface_hub

    monkeypatch.setenv("HF_HUB_OFFLINE", offline_value)
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", offline_value)
    monkeypatch.setattr(nltk.data, "load", lambda *_args: object())
    calls = []
    for filename in ("config.json", "preprocessor_config.json", "vocab.json", "pytorch_model.bin"):
        (tmp_path / filename).write_bytes(b"cached")

    def snapshot(repo, **kwargs):
        calls.append((repo, kwargs))
        if not cached and kwargs.get("local_files_only"):
            raise FileNotFoundError("offline cache miss")
        return str(tmp_path)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    monkeypatch.setitem(ab.sys.modules, "whisperx.alignment", types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={}, DEFAULT_ALIGN_MODELS_HF={"pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish"},
        load_align_model=lambda *_args, **_kwargs: (object(), {}),
    ))
    ab._load_installed_align_model("pl", "cuda")
    repo, options = calls[0]
    assert repo == "jonatasgrosman/wav2vec2-large-xlsr-53-polish"
    assert options["local_files_only"] is True
    assert set(options["allow_patterns"]) >= {
        "config.json", "preprocessor_config.json", "vocab.json", "pytorch_model.bin",
    }
    assert "flax_model.msgpack" not in options["allow_patterns"]
    assert "language_model/lm.binary" not in options["allow_patterns"]
    assert calls[1:] == ([] if cached else [(repo, {"allow_patterns": options["allow_patterns"]})])


def test_incomplete_polish_snapshot_is_downloaded_on_demand(monkeypatch, tmp_path):
    import nltk
    import huggingface_hub

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setattr(nltk.data, "load", lambda *_args: object())
    calls = []

    def snapshot(_repo, **kwargs):
        calls.append(kwargs)
        if not kwargs.get("local_files_only"):
            for filename in ("config.json", "preprocessor_config.json", "vocab.json", "pytorch_model.bin"):
                (tmp_path / filename).write_bytes(b"downloaded")
        return str(tmp_path)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    monkeypatch.setitem(ab.sys.modules, "whisperx.alignment", types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={}, DEFAULT_ALIGN_MODELS_HF={"pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish"},
        load_align_model=lambda *_args, **_kwargs: (object(), {}),
    ))
    ab._load_installed_align_model("pl", "cuda")
    assert len(calls) == 2
    assert calls[0]["local_files_only"] is True
    assert calls[0]["allow_patterns"] == calls[1]["allow_patterns"]


def test_polish_download_failure_is_classified_as_optional_alignment_unavailable(monkeypatch):
    import huggingface_hub

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    def snapshot(_repo, **kwargs):
        if kwargs.get("local_files_only"):
            raise FileNotFoundError("offline")
        raise OSError("network unavailable")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    monkeypatch.setitem(ab.sys.modules, "whisperx.alignment", types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={}, DEFAULT_ALIGN_MODELS_HF={"pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish"},
    ))
    with pytest.raises(ab._OptionalAlignmentUnavailable, match="Polish.*network unavailable"):
        ab._load_installed_align_model("pl", "cuda")


def test_other_languages_never_load_or_download_a_forced_aligner(monkeypatch):
    monkeypatch.setitem(ab.sys.modules, "whisperx.alignment", types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={"en": "WAV2VEC2_ASR_BASE_960H"}, DEFAULT_ALIGN_MODELS_HF={},
    ))
    with pytest.raises(ValueError, match="not configured"):
        ab._load_installed_align_model("de", "cuda")


def test_uncached_english_offline_fails_without_a_download(monkeypatch, tmp_path):
    import torch
    import torchaudio

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(torch.hub, "get_dir", lambda: str(tmp_path))
    monkeypatch.setattr(torchaudio.pipelines, "WAV2VEC2_ASR_BASE_960H", types.SimpleNamespace(_path="not-cached.pth"))
    monkeypatch.setitem(ab.sys.modules, "whisperx.alignment", types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={"en": "WAV2VEC2_ASR_BASE_960H"}, DEFAULT_ALIGN_MODELS_HF={},
        load_align_model=lambda *_args, **_kwargs: pytest.fail("offline load attempted"),
    ))
    with pytest.raises(RuntimeError, match="English.*missing while offline"):
        ab._load_installed_align_model("en", "cuda")


def test_uncached_polish_offline_fails_without_a_download(monkeypatch):
    import huggingface_hub

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    def snapshot(_repo, **kwargs):
        assert kwargs["local_files_only"] is True
        assert "pytorch_model.bin" in kwargs["allow_patterns"]
        raise FileNotFoundError("missing")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    monkeypatch.setitem(ab.sys.modules, "whisperx.alignment", types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={}, DEFAULT_ALIGN_MODELS_HF={"pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish"},
    ))
    with pytest.raises(RuntimeError, match="Polish.*missing while offline"):
        ab._load_installed_align_model("pl", "cuda")


def test_uncached_punkt_offline_fails_without_network(monkeypatch, tmp_path):
    import nltk
    import huggingface_hub
    from core import config

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    for filename in ("config.json", "preprocessor_config.json", "vocab.json", "pytorch_model.bin"):
        (tmp_path / filename).write_bytes(b"cached")
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *_args, **_kwargs: str(tmp_path))
    monkeypatch.setattr(nltk.data, "load", lambda *_args: (_ for _ in ()).throw(LookupError("missing")))
    monkeypatch.setattr(nltk, "download", lambda *_args, **_kwargs: pytest.fail("offline download attempted"))
    monkeypatch.setitem(ab.sys.modules, "whisperx.alignment", types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={}, DEFAULT_ALIGN_MODELS_HF={"pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish"},
    ))
    with pytest.raises(RuntimeError, match="NLTK punkt_tab is missing while offline"):
        ab._load_installed_align_model("pl", "cuda")


@pytest.mark.parametrize('language', ['en', 'pl', 'de', 'nl'])
def test_windows_rocm_alignment_failure_never_silently_returns_native_words(monkeypatch, language):
    monkeypatch.setattr(ab.sys, "platform", "win32")
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: True)
    loads = []

    def load_align_model(*args):
        loads.append(args)
        raise RuntimeError("missing alignment asset")

    monkeypatch.setattr(ab, "_load_installed_align_model", load_align_model)
    backend = ab.FasterWhisperBackend(model_name="cached-model")
    native_word = types.SimpleNamespace(word=" hello", start=0.1, end=0.7, probability=0.9)
    segment = types.SimpleNamespace(text=" hello", start=0.1, end=0.7, words=[native_word])
    backend._model = types.SimpleNamespace(transcribe=lambda *args, **kwargs: (
        iter([segment]), types.SimpleNamespace(language=language, language_probability=0.99, duration=1.0),
    ))
    backend._device = "cuda"
    with pytest.raises(RuntimeError, match='missing alignment asset'):
        backend.transcribe('demo.wav')
    assert loads == [(language, 'cuda')]


def _rocm_backend_with_native_words(monkeypatch, language):
    monkeypatch.setattr(ab.sys, 'platform', 'win32')
    monkeypatch.setattr(ab, '_rocm_torch', lambda: True)
    monkeypatch.setattr(ab, '_ctranslate2_cuda_ok', lambda: True)
    native_word = types.SimpleNamespace(word=' hello', start=0.1, end=0.7, probability=0.9)
    segment = types.SimpleNamespace(text=' hello', start=0.1, end=0.7, words=[native_word])
    backend = ab.FasterWhisperBackend(model_name='cached-model')
    backend._model = types.SimpleNamespace(transcribe=lambda *_args, **_kwargs: (
        iter([segment]), types.SimpleNamespace(language=language, language_probability=0.99, duration=1.0),
    ))
    backend._device = 'cuda'
    return backend


def test_unsupported_rocm_language_preserves_native_word_timestamps(monkeypatch):
    backend = _rocm_backend_with_native_words(monkeypatch, 'sw')
    monkeypatch.setitem(ab.sys.modules, 'whisperx.alignment', types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={'en': 'english'}, DEFAULT_ALIGN_MODELS_HF={'pl': 'polish'},
    ))

    result = backend.transcribe('demo.wav')
    assert result['segments'][0]['words'][0]['probability'] == 0.9


@pytest.mark.parametrize('missing', ['english_checkpoint', 'polish_checkpoint', 'punkt'])
@pytest.mark.parametrize('offline_variable', ['HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE'])
@pytest.mark.parametrize('offline_value', ['1', 'ON', 'YES', 'TRUE', 'on', 'yes', 'true', 'True'])
def test_rocm_transcription_keeps_native_words_when_optional_aligner_missing_offline(
    monkeypatch, tmp_path, missing, offline_variable, offline_value,
):
    import huggingface_hub
    import nltk
    import torch
    import torchaudio
    from core import config

    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    monkeypatch.delenv('TRANSFORMERS_OFFLINE', raising=False)
    monkeypatch.setenv(offline_variable, offline_value)
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(nltk, 'download', lambda *_args, **_kwargs: pytest.fail('offline download attempted'))
    monkeypatch.setattr(torch.hub, 'get_dir', lambda: str(tmp_path))
    monkeypatch.setattr(
        torchaudio.pipelines, 'WAV2VEC2_ASR_BASE_960H', types.SimpleNamespace(_path='uncached.pth'),
    )
    language = 'en' if missing == 'english_checkpoint' else 'pl'
    snapshots = []

    def snapshot(_repo, **kwargs):
        snapshots.append(kwargs)
        assert kwargs['local_files_only'] is True
        if missing == 'polish_checkpoint':
            raise FileNotFoundError('offline cache miss')
        return str(tmp_path)

    monkeypatch.setattr(huggingface_hub, 'snapshot_download', snapshot)
    if missing == 'punkt':
        for filename in ('config.json', 'preprocessor_config.json', 'vocab.json', 'pytorch_model.bin'):
            (tmp_path / filename).write_bytes(b'cached')
        monkeypatch.setattr(nltk.data, 'load', lambda *_args: (_ for _ in ()).throw(LookupError('missing')))
    monkeypatch.setitem(ab.sys.modules, 'whisperx.alignment', types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={'en': 'WAV2VEC2_ASR_BASE_960H'},
        DEFAULT_ALIGN_MODELS_HF={'pl': 'jonatasgrosman/wav2vec2-large-xlsr-53-polish'},
        load_align_model=lambda *_args, **_kwargs: pytest.fail('uncached aligner loaded'),
    ))

    backend = _rocm_backend_with_native_words(monkeypatch, language)
    result = backend.transcribe('demo.wav', word_timestamps=True)
    assert result['segments'] == [{
        'text': ' hello', 'start': 0.1, 'end': 0.7,
        'words': [{'word': ' hello', 'start': 0.1, 'end': 0.7, 'probability': 0.9}],
    }]
    assert result['chunks'] == [{'text': ' hello', 'timestamp': (0.1, 0.7)}]
    assert backend._hip_align_models == {}
    assert len(snapshots) == (0 if language == 'en' else 1)


def test_rocm_transcription_keeps_native_words_after_polish_download_failure(monkeypatch):
    import huggingface_hub

    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    monkeypatch.delenv('TRANSFORMERS_OFFLINE', raising=False)
    attempts = []

    def snapshot(_repo, **kwargs):
        attempts.append(kwargs.get('local_files_only', False))
        if kwargs.get('local_files_only'):
            raise FileNotFoundError('uncached')
        raise OSError('network unavailable')

    monkeypatch.setattr(huggingface_hub, 'snapshot_download', snapshot)
    monkeypatch.setitem(ab.sys.modules, 'whisperx.alignment', types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={},
        DEFAULT_ALIGN_MODELS_HF={'pl': 'jonatasgrosman/wav2vec2-large-xlsr-53-polish'},
    ))
    backend = _rocm_backend_with_native_words(monkeypatch, 'pl')
    result = backend.transcribe('demo.wav')
    assert result['segments'][0]['words'][0]['probability'] == 0.9
    assert attempts == [True, False]


def test_rocm_transcription_keeps_native_words_after_english_download_failure(monkeypatch, tmp_path):
    import torch
    import torchaudio
    from urllib.error import URLError

    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    monkeypatch.delenv('TRANSFORMERS_OFFLINE', raising=False)
    monkeypatch.setattr(torch.hub, 'get_dir', lambda: str(tmp_path))
    monkeypatch.setattr(
        torchaudio.pipelines, 'WAV2VEC2_ASR_BASE_960H', types.SimpleNamespace(_path='uncached.pth'),
    )
    attempts = []

    def load_align_model(language, device, **_kwargs):
        attempts.append((language, device))
        raise URLError('network unavailable')

    monkeypatch.setitem(ab.sys.modules, 'whisperx.alignment', types.SimpleNamespace(
        DEFAULT_ALIGN_MODELS_TORCH={'en': 'WAV2VEC2_ASR_BASE_960H'},
        DEFAULT_ALIGN_MODELS_HF={},
        load_align_model=load_align_model,
    ))
    backend = _rocm_backend_with_native_words(monkeypatch, 'en')
    result = backend.transcribe('demo.wav')
    assert result['segments'][0]['words'][0]['probability'] == 0.9
    assert attempts == [('en', 'cuda')]


def test_rocm_transcription_does_not_hide_alignment_computation_failure(monkeypatch):
    import numpy as np

    def fail_alignment(*_args, **_kwargs):
        raise ValueError('alignment computation failed')

    monkeypatch.setattr(ab, '_load_installed_align_model', lambda *_args: (
        types.SimpleNamespace(align=fail_alignment), object(), {},
    ))
    monkeypatch.setattr(ab, '_decode_audio_16k_mono', lambda _path: np.zeros(16000, dtype=np.float32))
    backend = _rocm_backend_with_native_words(monkeypatch, 'en')
    with pytest.raises(ValueError, match='alignment computation failed'):
        backend.transcribe('demo.wav')


def _rocm_backend_with_alignment_output(monkeypatch, text, aligned_texts):
    import numpy as np

    backend = _rocm_backend_with_native_words(monkeypatch, "en")
    native_segment = types.SimpleNamespace(text=text, start=0.0, end=2.0, words=[])
    backend._model = types.SimpleNamespace(transcribe=lambda *_args, **_kwargs: (
        iter([native_segment]),
        types.SimpleNamespace(language="en", language_probability=0.99, duration=2.0),
    ))
    aligned = [
        {
            "text": sentence, "start": float(index), "end": float(index + 1),
            "words": [{"word": sentence, "start": float(index), "end": float(index + 1)}],
        }
        for index, sentence in enumerate(aligned_texts)
    ]
    monkeypatch.setattr(ab, "_decode_audio_16k_mono", lambda _path: np.zeros(32000, dtype=np.float32))
    monkeypatch.setattr(ab, "_load_installed_align_model", lambda *_args: (
        types.SimpleNamespace(align=lambda *_args, **_kwargs: {"segments": aligned}), object(), {},
    ))
    return backend, aligned


@pytest.mark.parametrize("text,aligned_texts", [
    (" Hello there. Goodbye now.", ["Hello there.", "Goodbye now."]),
    (" Dzień dobry.\nDo zobaczenia! ", ["Dzień dobry.", "Do zobaczenia!"]),
])
def test_rocm_alignment_accepts_sentence_splitting(monkeypatch, text, aligned_texts):
    backend, aligned = _rocm_backend_with_alignment_output(monkeypatch, text, aligned_texts)

    result = backend.transcribe("demo.wav")

    assert result["segments"] == aligned
    assert result["chunks"] == [
        {"text": segment["text"], "timestamp": (segment["start"], segment["end"])}
        for segment in aligned
    ]
    assert backend._device == "cuda"


@pytest.mark.parametrize("aligned_texts", [
    ["Hello there."],
    ["Goodbye now. Hello there."],
    ["Hello there. Another sentence."],
])
def test_rocm_alignment_rejects_changed_transcript(monkeypatch, aligned_texts):
    backend, _aligned = _rocm_backend_with_alignment_output(
        monkeypatch, " Hello there. Goodbye now.", aligned_texts,
    )

    with pytest.raises(RuntimeError, match="ROCm forced alignment"):
        backend.transcribe("demo.wav")


@pytest.mark.parametrize("words", [
    [],
    [{"word": "Hello", "start": None, "end": 1.0}],
    [{"word": "Hello", "start": 0.0, "end": None}],
])
def test_rocm_alignment_still_rejects_missing_word_timings(monkeypatch, words):
    backend, aligned = _rocm_backend_with_alignment_output(monkeypatch, "Hello", ["Hello"])
    aligned[0]["words"] = words

    with pytest.raises(RuntimeError, match="ROCm forced alignment"):
        backend.transcribe("demo.wav")
