"""The clone-upload reference auto-transcribe path must offload the resident
TTS model before WhisperX cold-loads and restore it afterwards.

VoiceStudio's clone upload saves a reference clip and immediately calls
``_auto_transcribe_reference``. Before this change that path loaded WhisperX
large-v3 on CPU beside an XPU-resident TTS model; it never called
``offload_tts_for_asr()``/``restore_tts_after_asr()``. The wrapper must run
the offload before transcription and always run the restore, including when
transcription raises or times out.
"""

import asyncio

import api.routers.profiles as profiles
import services.asr_backend as ab
import services.model_manager as mm


def _patch_transcribe(monkeypatch, fn):
    monkeypatch.setattr(ab, "transcribe_reference", fn)


def test_clone_reference_offloads_before_transcribing_and_restores_after(
    monkeypatch, tmp_path
):
    audio = str(tmp_path / "reference.wav")
    calls = []

    def fake_offload():
        calls.append("offload")

    def fake_transcribe(path, *, release_after=False):
        calls.append(("transcribe", path))
        return "hello world"

    def fake_restore():
        calls.append("restore")

    monkeypatch.setattr(mm, "offload_tts_for_asr", fake_offload)
    monkeypatch.setattr(mm, "restore_tts_after_asr", fake_restore)
    _patch_transcribe(monkeypatch, fake_transcribe)

    result = asyncio.run(profiles._auto_transcribe_reference(audio))

    assert result == "hello world"
    assert calls == ["offload", ("transcribe", audio), "restore"]


def test_clone_reference_restores_even_when_transcription_fails(
    monkeypatch, tmp_path
):
    audio = str(tmp_path / "reference.wav")
    calls = []

    def fake_offload():
        calls.append("offload")

    def failing_transcribe(path, *, release_after=False):
        calls.append(("transcribe", path))
        raise RuntimeError("ASR failed")

    def fake_restore():
        calls.append("restore")

    monkeypatch.setattr(mm, "offload_tts_for_asr", fake_offload)
    monkeypatch.setattr(mm, "restore_tts_after_asr", fake_restore)
    _patch_transcribe(monkeypatch, failing_transcribe)

    result = asyncio.run(profiles._auto_transcribe_reference(audio))

    assert result == ""
    assert calls == ["offload", ("transcribe", audio), "restore"]


def test_clone_reference_restores_when_transcription_times_out(
    monkeypatch, tmp_path
):
    audio = str(tmp_path / "reference.wav")
    calls = []

    def fake_offload():
        calls.append("offload")

    def slow_transcribe(path, *, release_after=False):
        calls.append(("transcribe", path))
        raise asyncio.TimeoutError("pretend wait_for timeout")

    def fake_restore():
        calls.append("restore")

    monkeypatch.setattr(mm, "offload_tts_for_asr", fake_offload)
    monkeypatch.setattr(mm, "restore_tts_after_asr", fake_restore)
    _patch_transcribe(monkeypatch, slow_transcribe)

    result = asyncio.run(profiles._auto_transcribe_reference(audio))

    assert result == ""
    assert calls == ["offload", ("transcribe", audio), "restore"]
