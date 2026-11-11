"""Offline contracts for the native CosyVoice GGUF/Vulkan adapter."""

from __future__ import annotations

import importlib
import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from engines.cosyvoice_gguf.prompt_cache import prompt_cache_key
from engines.cosyvoice_gguf.server import build_speech_payload, decode_speech_response


class _LiveModule:
    """Resolve the module at use time so sys.modules pollution cannot leave stale refs."""

    def __init__(self, name):
        object.__setattr__(self, "_name", name)

    def _mod(self):
        return importlib.import_module(object.__getattribute__(self, "_name"))

    def __getattr__(self, attr):
        return getattr(self._mod(), attr)

    def __setattr__(self, attr, value):
        setattr(self._mod(), attr, value)

    def __delattr__(self, attr):
        delattr(self._mod(), attr)


bootstrap = _LiveModule("engines.cosyvoice_gguf.bootstrap")


def parse_backends(*args, **kwargs):
    return bootstrap.parse_backends(*args, **kwargs)


def choose_backend(*args, **kwargs):
    return bootstrap.choose_backend(*args, **kwargs)


def test_bootstrap_proxy_follows_a_replaced_module(monkeypatch):
    """Regression: a module captured at import goes stale once another test
    replaces sys.modules["engines.cosyvoice_gguf.bootstrap"]; the proxy must not."""
    import sys
    import types

    replacement = types.ModuleType("engines.cosyvoice_gguf.bootstrap")
    replacement.parse_backends = lambda *a, **k: "replacement"
    monkeypatch.setitem(sys.modules, "engines.cosyvoice_gguf.bootstrap", replacement)
    assert parse_backends("") == "replacement"
    bootstrap.marker = 1
    assert replacement.marker == 1


def test_parse_and_choose_vulkan_backend():
    rows = parse_backends(
        "Available GGML backends:\n"
        "Vulkan0 - AMD Radeon RX 6700 XT\n"
        "CPU - AMD Ryzen 9 5900X\n"
    )
    chosen = choose_backend(rows, "auto")
    assert chosen.name == "Vulkan0"
    assert chosen.target == "vulkan"
    assert chosen.device_name == "AMD Radeon RX 6700 XT"


# #3980: the exact ``cosyvoice-cli.exe --list-backends`` output from a GTX 1080 Ti.
# GPU rows carry a PCI bus id in the middle column; CPU rows carry ``-``.
REPORTED_LOG = (
    "ggml_vulkan: Found 1 Vulkan devices:\n"
    "ggml_vulkan: 0 = NVIDIA GeForce GTX 1080 Ti (NVIDIA)\n"
)
REPORTED_TABLE = (
    "Available GGML backends:\n"
    "  Vulkan0    0000:01:00.0         NVIDIA GeForce GTX 1080 Ti\n"
    "  CPU        -                    Intel(R) Core(TM) i5-10400F CPU @ 2.90GHz\n"
)


@pytest.mark.parametrize(
    "output",
    [
        REPORTED_LOG + REPORTED_TABLE,
        (REPORTED_LOG + REPORTED_TABLE).replace("\n", "\r\n"),  # Windows console
        REPORTED_TABLE + "\n" + REPORTED_LOG,  # stdout first, ggml log on stderr
        "\ufeff" + REPORTED_TABLE.replace("    ", "\t"),
    ],
)
def test_reported_list_backends_output_selects_vulkan(output):
    rows = parse_backends(output)
    assert [(row.name, row.target, row.device_name) for row in rows] == [
        ("Vulkan0", "vulkan", "NVIDIA GeForce GTX 1080 Ti"),
        ("CPU", "cpu", "Intel(R) Core(TM) i5-10400F CPU @ 2.90GHz"),
    ]
    chosen = choose_backend(rows, "auto")
    assert (chosen.name, chosen.fallback_reason) == ("Vulkan0", None)


def test_log_and_header_lines_never_parse_as_devices():
    assert parse_backends(
        REPORTED_LOG + "Available GGML backends:\nDevice count: 2\nusage: cosyvoice-cli [options]\n"
    ) == []


def test_backend_selection_falls_back_only_in_auto_mode():
    rows = parse_backends("Available GGML backends:\nCPU - Ryzen 9\n")
    chosen = choose_backend(rows, "auto")
    assert chosen.name == "CPU"
    assert chosen.fallback_reason
    try:
        choose_backend(rows, "Vulkan0")
    except RuntimeError as exc:
        assert "Vulkan0" in str(exc)
    else:
        raise AssertionError("explicit unavailable backend silently fell back")


def test_prompt_cache_key_includes_audio_and_reference_transcript(tmp_path):
    ref = tmp_path / "reference.wav"
    ref.write_bytes(b"voice-a")
    first = prompt_cache_key(ref, "spoken text")
    assert prompt_cache_key(ref, "spoken text") == first
    assert prompt_cache_key(ref, "edited transcript") != first
    ref.write_bytes(b"voice-b")
    assert prompt_cache_key(ref, "spoken text") != first


def test_speech_payload_carries_local_prompt_and_instruct_controls():
    payload = build_speech_payload(
        model_id="cosyvoice-test",
        voice="voice-abc",
        text="hello",
        instruct="soft and breathy",
        seed=42,
        speed=0.9,
    )
    assert payload == {
        "model": "cosyvoice-test",
        "voice": "voice-abc",
        "input": "hello",
        "response_format": "wav",
        "instructions": "soft and breathy",
        "seed": 42,
        "speed": 0.9,
    }


def test_decode_wav_response_to_mono_float():
    audio = io.BytesIO()
    sf.write(audio, np.asarray([0.0, 0.5, -0.5], dtype=np.float32), 24000, format="WAV")
    rate, wav = decode_speech_response(audio.getvalue())
    assert rate == 24000
    assert wav.dtype == np.float32
    assert wav.shape == (3,)
    assert np.max(np.abs(wav)) > 0.4


def test_decode_rejects_non_wav_server_response():
    try:
        decode_speech_response(b"backend unavailable")
    except ValueError as exc:
        assert "WAV" in str(exc)
    else:
        raise AssertionError("server error payload decoded as audio")


def test_direct_model_override_never_downloads(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model")
    monkeypatch.setenv(bootstrap.MODEL_ENV, str(model))
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda **kw: (_ for _ in ()).throw(AssertionError("download attempted")),
    )
    assert bootstrap.resolve_model() == model.resolve()


def test_binary_override_is_authoritative_and_managed_path_is_fallback(
    tmp_path, monkeypatch
):
    managed = tmp_path / "bin"
    managed.mkdir()
    default = managed / (
        "cosyvoice-server.exe" if bootstrap.os.name == "nt" else "cosyvoice-server"
    )
    default.write_bytes(b"managed")
    override = tmp_path / "explicit-server.exe"
    override.write_bytes(b"override")
    monkeypatch.setenv(bootstrap.DIR_ENV, str(tmp_path))
    monkeypatch.setenv(bootstrap.BIN_ENV, str(override))
    assert bootstrap.resolve_server() == override.resolve()
    monkeypatch.setenv(bootstrap.BIN_ENV, str(tmp_path / "missing.exe"))
    try:
        bootstrap.resolve_server()
    except RuntimeError as exc:
        assert bootstrap.BIN_ENV in str(exc)
    else:
        raise AssertionError("missing explicit runtime unexpectedly fell back")
    monkeypatch.delenv(bootstrap.BIN_ENV)
    assert bootstrap.resolve_server() == default.resolve()


def test_model_cache_lookup_is_immutable_and_local_only(tmp_path, monkeypatch):
    files = tmp_path / bootstrap.MODEL_FILE
    files.write_bytes(b"model")
    calls = []
    monkeypatch.delenv(bootstrap.MODEL_ENV, raising=False)
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda *args, **kwargs: calls.append((args, kwargs)) or tmp_path,
    )
    assert bootstrap.resolve_model() == files
    assert calls[0][0] == (bootstrap.HF_MODEL_REPO,)
    assert calls[0][1] == {
        "revision": bootstrap.HF_MODEL_REVISION,
        "local_files_only": True,
    }


def test_backend_registry_exposes_opt_in_engine_and_docs():
    from services import tts_backend

    engine = tts_backend._REGISTRY["cosyvoice-gguf"]
    assert engine.id == "cosyvoice-gguf"
    assert engine.supports_cloning is True
    assert engine.supports_voice_design is False
    assert engine.supports_generation_seed is True
    assert tts_backend._engine_docs_url(engine.id).endswith(
        "/docs/engines/cosyvoice-gguf.md"
    )


def test_backend_generate_uses_cached_reference_and_returns_engine_tensor(
    tmp_path, monkeypatch
):
    import engines.cosyvoice_gguf as engine_module

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    prompt = tmp_path / "prompt.gguf"
    prompt.write_bytes(b"prompt")
    audio = io.BytesIO()
    sf.write(audio, np.asarray([0.0, 0.2, -0.2], dtype=np.float32), 24000, format="WAV")

    class Server:
        model_id = "test-model"

        def ensure(self, path: Path, key: str):
            assert path == prompt
            assert key == "content-key"

        def speak(self, payload):
            assert payload["instructions"] == "warm and breathy"
            assert payload["speed"] == 0.9
            return audio.getvalue()

        def stop(self):
            pass

    monkeypatch.setattr(engine_module, "cached_prompt", lambda *_: prompt)
    monkeypatch.setattr(engine_module, "prompt_cache_key", lambda *_: "content-key")
    backend = engine_module.CosyVoiceGGUFBackend()
    backend._server = Server()
    waveform = backend.generate(
        "hello",
        ref_audio=str(reference),
        ref_text="hello there",
        language="en",
        instruct="warm and breathy",
        speed=0.9,
    )
    assert tuple(waveform.shape) == (1, 3)
    assert backend.sample_rate == 24000



def _backend_with_failing_server(tmp_path, monkeypatch, error):
    import engines.cosyvoice_gguf as engine_module

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    stopped = []

    class Server:
        model_id = "test-model"

        def ensure(self, *_):
            pass

        def speak(self, _payload):
            raise error

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(engine_module, "cached_prompt", lambda *_: tmp_path / "p.gguf")
    monkeypatch.setattr(engine_module, "prompt_cache_key", lambda *_: "key")
    backend = engine_module.CosyVoiceGGUFBackend()
    backend._server = Server()
    return backend, str(reference), stopped


def test_http_error_keeps_healthy_server_and_names_the_status(tmp_path, monkeypatch):
    import urllib.error

    error = urllib.error.HTTPError("http://127.0.0.1/v1/audio/speech", 400, "bad", {}, None)
    backend, reference, stopped = _backend_with_failing_server(tmp_path, monkeypatch, error)
    with pytest.raises(RuntimeError, match="HTTP 400") as raised:
        backend.generate("hello", ref_audio=reference, ref_text="hi", language="en")
    assert "timed out" not in str(raised.value)
    assert stopped == []


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (TimeoutError("slow"), "timed out"),
        (__import__("urllib.error").error.URLError(TimeoutError("slow")), "timed out"),
        (ConnectionRefusedError("gone"), "stopped responding"),
    ],
)
def test_transport_failures_stop_the_server_with_an_accurate_reason(
    tmp_path, monkeypatch, error, message
):
    backend, reference, stopped = _backend_with_failing_server(tmp_path, monkeypatch, error)
    with pytest.raises(RuntimeError, match=message):
        backend.generate("hello", ref_audio=reference, ref_text="hi", language="en")
    assert stopped == [True]


def test_invalid_explicit_overrides_error_instead_of_using_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv(bootstrap.MODEL_ENV, str(tmp_path / "missing.gguf"))
    with pytest.raises(RuntimeError, match=bootstrap.MODEL_ENV):
        bootstrap.resolve_model()
    monkeypatch.setenv(bootstrap.FRONTEND_ENV, str(tmp_path))
    with pytest.raises(RuntimeError, match="campplus"):
        bootstrap.frontend_dir()


def test_failed_backend_probe_falls_back_to_cpu_with_its_reason(tmp_path, monkeypatch):
    import engines.cosyvoice_gguf as engine_module

    def run(*_args, **_kwargs):
        raise __import__("subprocess").TimeoutExpired(["cli"], 30)

    monkeypatch.delenv(bootstrap.BACKEND_ENV, raising=False)
    bootstrap._PROBE_CACHE.clear()
    monkeypatch.setattr(bootstrap, "resolve_cli", lambda: tmp_path / "cli")
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    profile = engine_module.CosyVoiceGGUFBackend.runtime_compute_profile(None)
    assert profile["routing_status"] == "cpu_fallback"
    assert "probe" in profile["routing_reason"]


def test_model_revision_comes_from_the_central_pin():
    from services.hf_revisions import revision_for

    assert bootstrap.HF_MODEL_REVISION == revision_for(bootstrap.HF_MODEL_REPO)
