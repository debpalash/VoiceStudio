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

    def fake_offload(**kwargs):
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

    def fake_offload(**kwargs):
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

    def fake_offload(**kwargs):
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


def test_clone_reference_forces_a_unified_memory_release(monkeypatch, tmp_path):
    audio = str(tmp_path / "reference.wav")
    calls = []
    seen_kwargs = []

    def fake_offload(**kwargs):
        seen_kwargs.append(kwargs)
        calls.append("offload")

    def fake_transcribe(path, *, release_after=False):
        calls.append(("transcribe", path))
        return "forced"

    def fake_restore():
        calls.append("restore")

    monkeypatch.setattr(mm, "offload_tts_for_asr", fake_offload)
    monkeypatch.setattr(mm, "restore_tts_after_asr", fake_restore)
    _patch_transcribe(monkeypatch, fake_transcribe)

    result = asyncio.run(profiles._auto_transcribe_reference(audio))

    assert result == "forced"
    assert seen_kwargs == [{"force_unified_release": True}]
    assert calls == ["offload", ("transcribe", audio), "restore"]


def test_clone_reference_timeout_does_not_restore_while_asr_still_runs(
    monkeypatch, tmp_path
):
    import threading

    audio = str(tmp_path / "reference.wav")
    calls = []
    asr_started = threading.Event()
    release_asr = threading.Event()

    def fake_offload(**kwargs):
        calls.append("offload")

    def slow_transcribe(path, *, release_after=False):
        calls.append("transcribe-start")
        asr_started.set()
        release_asr.wait(5)
        calls.append("transcribe-end")
        return "late"

    def fake_restore():
        calls.append("restore")

    monkeypatch.setattr(mm, "offload_tts_for_asr", fake_offload)
    monkeypatch.setattr(mm, "restore_tts_after_asr", fake_restore)
    monkeypatch.setattr(profiles, "_reference_transcribe_timeout", lambda: 0.2)
    _patch_transcribe(monkeypatch, slow_transcribe)

    async def run():
        result = await profiles._auto_transcribe_reference(audio)
        # Deadline passed, ASR thread still running: TTS must not be restored.
        snapshot = list(calls)
        release_asr.set()
        return result, snapshot

    result, snapshot = asyncio.run(run())

    assert result == ""
    assert "restore" not in snapshot
    assert calls == ["offload", "transcribe-start", "transcribe-end", "restore"]


def test_clone_reference_skips_asr_when_release_refused(monkeypatch, tmp_path):
    audio = str(tmp_path / "reference.wav")
    calls = []

    monkeypatch.setattr(mm, "offload_tts_for_asr", lambda **k: False)
    monkeypatch.setattr(mm, "restore_tts_after_asr", lambda: calls.append("restore"))
    _patch_transcribe(
        monkeypatch, lambda p, **k: calls.append("transcribe") or "x"
    )

    assert asyncio.run(profiles._auto_transcribe_reference(audio)) == ""
    assert calls == []
