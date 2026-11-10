"""dots.tts on the managed audio.cpp runtime (#2665).

The venv ``dots-tts`` engine has no Windows install path. The pinned audio.cpp
release already runs the ``dots_tts`` family on every desktop OS, so a second
audio.cpp engine serves it: its own package, loopback port and server.json
directory, the shared runtime, and Breeze-TTS-2 left exactly as it was.
"""
from __future__ import annotations

import base64
import importlib
import io
import json
from unittest.mock import Mock

import pytest

DOTS_VARS = (
    "OMNIVOICE_AUDIOCPP_DOTS_MODEL",
    "OMNIVOICE_AUDIOCPP_DOTS_PACKAGE",
    "OMNIVOICE_AUDIOCPP_DOTS_PORT",
    "OMNIVOICE_AUDIOCPP_MODEL",
    "OMNIVOICE_AUDIOCPP_PACKAGE",
    "OMNIVOICE_AUDIOCPP_PORT",
)


@pytest.fixture(autouse=True)
def scrub_env(monkeypatch):
    for var in DOTS_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def mods():
    audiocpp = importlib.import_module("engines.audiocpp")
    bootstrap = importlib.import_module("engines.audiocpp.bootstrap")
    return audiocpp, bootstrap


def test_dots_package_never_collides_with_breeze(mods):
    _audiocpp, bootstrap = mods
    breeze, dots = bootstrap.BREEZE, bootstrap.DOTS
    assert dots.family == "dots_tts"
    assert (dots.package_dir, dots.default_package) == (
        "DotTTS-SOAR-GGUF", "dots-tts-soar-q8_0.gguf",
    )
    assert bootstrap.server_port(dots) != bootstrap.server_port(breeze)
    assert dots.workdir != breeze.workdir
    assert {dots.package_env, dots.port_env, dots.model_env}.isdisjoint(
        {breeze.package_env, breeze.port_env, breeze.model_env}
    )
    # Breeze-TTS-2 keeps its historical defaults and env names.
    assert bootstrap.server_port() == bootstrap.DEFAULT_PORT == 17860
    assert bootstrap.package_filename() == "breeze-tts-2-q8_0.gguf"
    assert breeze.model_env == "OMNIVOICE_AUDIOCPP_MODEL"


def test_dots_env_overrides_are_its_own(monkeypatch, mods):
    _audiocpp, bootstrap = mods
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_DOTS_PORT", "18090")
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_DOTS_PACKAGE", "dots-tts-soar-bf16.gguf")
    assert bootstrap.server_port(bootstrap.DOTS) == 18090
    assert bootstrap.package_filename(bootstrap.DOTS) == "dots-tts-soar-bf16.gguf"
    assert bootstrap.server_port() == 17860
    assert bootstrap.package_filename() == "breeze-tts-2-q8_0.gguf"


def test_dots_resolves_only_its_own_cached_package(monkeypatch, tmp_path, mods):
    _audiocpp, bootstrap = mods
    package = tmp_path / "DotTTS-SOAR-GGUF"
    package.mkdir()
    model = package / "dots-tts-soar-q8_0.gguf"
    model.write_bytes(b"GGUF")
    calls = []

    def fake_snapshot(**kwargs):
        calls.append(kwargs)
        return str(tmp_path)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
    assert bootstrap.resolve_model_file(bootstrap.DOTS) == model
    assert calls == [{
        "repo_id": "audio-cpp/audio.cpp-gguf",
        "revision": bootstrap.HF_MODEL_REVISION,
        "allow_patterns": ["DotTTS-SOAR-GGUF/dots-tts-soar-q8_0.gguf"],
        "local_files_only": True,
    }]


def test_missing_dots_weights_name_dots(monkeypatch, mods):
    _audiocpp, bootstrap = mods
    from huggingface_hub.utils import LocalEntryNotFoundError

    def missing(**_kwargs):
        raise LocalEntryNotFoundError("not cached")

    monkeypatch.setattr("huggingface_hub.snapshot_download", missing)
    with pytest.raises(RuntimeError, match=r"dots\.tts is not installed.*OMNIVOICE_AUDIOCPP_DOTS_MODEL"):
        bootstrap.resolve_model_file(bootstrap.DOTS)


def test_dots_server_serves_the_dots_family(tmp_path, monkeypatch, mods):
    audiocpp, bootstrap = mods
    backend = audiocpp.AudioCPPDotsBackend()
    binary = tmp_path / "audiocpp_server"
    binary.write_bytes(b"binary")
    model = tmp_path / "dots.gguf"
    model.write_bytes(b"GGUF")
    seen = []
    monkeypatch.setattr(bootstrap, "resolve_server_binary", lambda: binary)
    monkeypatch.setattr(
        bootstrap, "resolve_model_file", lambda package: seen.append(package) or model,
    )
    devices = bootstrap.parse_device_list('CPU:0 "Test CPU" [CPU]')
    monkeypatch.setattr(
        bootstrap, "resolve_compute_selection",
        lambda *a, **k: bootstrap.select_device(devices),
    )
    monkeypatch.setattr(importlib.import_module("core.config"), "DATA_DIR", tmp_path / "data")
    proc = Mock()
    proc.poll.return_value = None
    monkeypatch.setattr(audiocpp, "spawn_owned", Mock(return_value=proc))
    monkeypatch.setattr(backend, "_wait_for_health", lambda: None)

    backend._ensure_loaded()

    assert seen == [bootstrap.DOTS]
    assert backend._server_json == tmp_path / "data" / "audiocpp-dots" / "server.json"
    config = json.loads(backend._server_json.read_text(encoding="utf-8"))
    assert config["port"] == 17861
    assert config["models"][0]["family"] == "dots_tts"
    assert config["models"][0]["id"].startswith("dots-tts-soar-")
    assert config["models"][0]["path"] == str(model)
    backend._proc = None


def _wav_json() -> dict:
    import numpy as np
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.zeros(240, dtype="float32"), 24000, format="WAV")
    return {"audio": base64.b64encode(buf.getvalue()).decode()}


def test_dots_requests_clone_without_design_and_with_its_guidance(monkeypatch, mods):
    audiocpp, bootstrap = mods
    backend = audiocpp.AudioCPPDotsBackend()
    devices = bootstrap.parse_device_list('CPU:0 "Test CPU" [CPU]')

    def loaded():
        backend._port = 17861
        backend._server_model_id = "dots-tts-soar-launch"
        backend._selection = bootstrap.select_device(devices)

    sent = []
    monkeypatch.setattr(backend, "_ensure_loaded", loaded)
    monkeypatch.setattr(
        backend, "_post_json", lambda path, payload, timeout: sent.append(payload) or _wav_json(),
    )
    wav = backend.generate(
        "Bonjour tout le monde.", ref_audio="/tmp/ref.wav", ref_text="Hello there.",
        instruct="whisper", guidance_scale=2.0, language="fr", seed=7,
    )
    assert wav.shape[0] == 1
    (payload,) = sent
    assert "instructions" not in payload
    assert payload["voice_ref"] == {"type": "path", "path": "/tmp/ref.wav"}
    assert payload["reference_text"] == "Hello there."
    assert payload["guidance_scale"] == 1.2
    assert payload["seed"] == 7


def test_dots_is_registered_without_voice_design(mods):
    audiocpp, _bootstrap = mods
    tts_backend = importlib.import_module("services.tts_backend")
    cls = tts_backend.get_backend_class("audiocpp-dots")
    assert cls is audiocpp.AudioCPPDotsBackend
    assert cls.supports_voice_design is False
    assert audiocpp.AudioCPPBackend.supports_voice_design is True
    assert cls().supported_languages == ["multi"]
    assert tts_backend._INSTALL_HINTS["audiocpp-dots"]
    assert tts_backend._engine_docs_url("audiocpp-dots")
    # The venv engine points Windows users at this one.
    assert "audio.cpp dots.tts" in tts_backend._INSTALL_HINTS["dots-tts"]


def test_breeze_requests_are_unchanged(monkeypatch, mods):
    audiocpp, bootstrap = mods
    backend = audiocpp.AudioCPPBackend()
    devices = bootstrap.parse_device_list('CPU:0 "Test CPU" [CPU]')

    def loaded():
        backend._port = 17860
        backend._server_model_id = "breeze-tts-2-launch"
        backend._selection = bootstrap.select_device(devices)

    sent = []
    monkeypatch.setattr(backend, "_ensure_loaded", loaded)
    monkeypatch.setattr(
        backend, "_post_json", lambda path, payload, timeout: sent.append(payload) or _wav_json(),
    )
    backend.generate("Hello.", description="warm narrator", guidance_scale=2.0)
    assert sent[0]["instructions"] == "warm narrator"
    assert sent[0]["guidance_scale"] == 2.0


def test_dots_reports_its_native_rate_before_the_first_render(mods):
    # Callers read sample_rate before generate() sets it from the response;
    # inheriting Breeze's 24 kHz would mislabel the first dots.tts render.
    audiocpp, _bootstrap = mods
    assert audiocpp.AudioCPPDotsBackend().sample_rate == 48000
    assert audiocpp.AudioCPPBackend().sample_rate == 24000
