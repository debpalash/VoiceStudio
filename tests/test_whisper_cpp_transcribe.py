"""Unit tests for whisper.cpp ASR backend with Vulkan support (Phase 3)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytest_plugins = ["whisper_cpp_fixtures"]

_SAMPLE_WHISPER_CPP_JSON = json.loads((Path(__file__).parent / "fixtures/whisper_cpp.json").read_text())
def test_transcribe_success(tmp_path, monkeypatch, m):
    fake_audio = tmp_path / "test.wav"
    fake_audio.write_text("RIFFdummyWAVE")
    fake_bin = tmp_path / "whisper-cli"
    fake_model = tmp_path / "ggml-base.bin"

    monkeypatch.setattr("engines.whisper_cpp.backend.locate_binary", lambda: fake_bin)
    monkeypatch.setattr("engines.whisper_cpp.backend.resolve_model", lambda: fake_model)
    monkeypatch.setattr("engines.whisper_cpp.backend._ensure_wav_16k", lambda path, tmp: Path(path))

    executed_cmds = []

    def _fake_run(cmd, **kw):
        executed_cmds.append(cmd)
        # Find -of prefix
        of_idx = cmd.index("-of")
        out_prefix = Path(cmd[of_idx + 1])
        out_json = out_prefix.parent / "out.json"
        out_json.write_text(json.dumps(_SAMPLE_WHISPER_CPP_JSON), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    backend = m.WhisperCppBackend()
    res = backend.transcribe(str(fake_audio), word_timestamps=True, language="en")

    assert res["text"] == "Hello world. This is whisper cpp."
    assert res["language"] == "en"
    assert len(res["chunks"]) == 2
    assert res["chunks"][0] == {"text": "Hello world.", "timestamp": (0.0, 2.5)}
    assert res["chunks"][1] == {"text": "This is whisper cpp.", "timestamp": (2.5, 4.8)}

    # Verify word timestamps
    assert len(res["segments"]) == 2
    words_0 = res["segments"][0]["words"]
    assert len(words_0) == 2
    assert words_0[0]["word"] == "Hello"
    assert words_0[0]["start"] == 0.0
    assert words_0[0]["end"] == 1.2

    # Verify command line arguments passed
    assert len(executed_cmds) == 1
    cmd = executed_cmds[0]
    assert "-m" in cmd
    assert "-l" in cmd
    assert "en" in cmd
    assert "--output-json-full" in cmd

def test_transcribe_device_and_backend_flags(tmp_path, monkeypatch, m):
    fake_audio = tmp_path / "test.wav"
    fake_audio.write_text("RIFFdummyWAVE")
    fake_bin = tmp_path / "whisper-cli"
    fake_model = tmp_path / "ggml-base.bin"

    monkeypatch.setattr("engines.whisper_cpp.backend.locate_binary", lambda: fake_bin)
    monkeypatch.setattr("engines.whisper_cpp.backend.resolve_model", lambda: fake_model)
    monkeypatch.setattr("engines.whisper_cpp.backend._ensure_wav_16k", lambda path, tmp: Path(path))

    executed_cmds = []

    def _fake_run(cmd, **kw):
        executed_cmds.append(cmd)
        of_idx = cmd.index("-of")
        out_prefix = Path(cmd[of_idx + 1])
        (out_prefix.parent / "out.json").write_text(json.dumps(_SAMPLE_WHISPER_CPP_JSON))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    backend = m.WhisperCppBackend()

    # Test CPU backend flag
    monkeypatch.setenv(m.BACKEND_ENV, "cpu")
    backend.transcribe(str(fake_audio), word_timestamps=False)
    assert "-ng" in executed_cmds[0]

    # Test device index flag
    monkeypatch.setattr("core.device_caps.requested_device_override", lambda: "auto")
    monkeypatch.delenv(m.BACKEND_ENV)
    monkeypatch.setenv(m.DEVICE_ENV, "1")
    backend.transcribe(str(fake_audio), task="translate")
    assert "-dev" in executed_cmds[1]
    assert "1" in executed_cmds[1]
    assert "--translate" in executed_cmds[1]

def test_transcribe_missing_audio_file(m):
    backend = m.WhisperCppBackend()
    with pytest.raises(FileNotFoundError):
        backend.transcribe("non_existent_audio_path.wav")

def test_transcribe_timeout_handling(tmp_path, monkeypatch, m):
    fake_audio = tmp_path / "test.wav"
    fake_audio.write_text("RIFFdummyWAVE")
    monkeypatch.setattr("engines.whisper_cpp.backend.locate_binary", lambda: tmp_path / "whisper-cli")
    monkeypatch.setattr("engines.whisper_cpp.backend.resolve_model", lambda: tmp_path / "ggml-base.bin")
    monkeypatch.setattr("engines.whisper_cpp.backend._ensure_wav_16k", lambda path, tmp: Path(path))

    def _fake_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="whisper-cli", timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _fake_timeout)

    backend = m.WhisperCppBackend()
    with pytest.raises(m.ab.ASRTimeoutError):
        backend.transcribe(str(fake_audio))
