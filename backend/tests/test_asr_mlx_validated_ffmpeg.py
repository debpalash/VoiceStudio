"""MLXWhisperBackend decodes the file itself instead of relying on a PATH ffmpeg."""
import sys
import types
import wave

import pytest

from services import asr_backend


class _FakeMlxWhisper(types.ModuleType):
    def __init__(self):
        super().__init__("mlx_whisper")
        self.received = []

    def transcribe(self, audio, **kwargs):
        self.received.append(audio)
        return {"language": "en", "segments": [{"text": "ok", "start": 0.0, "end": 0.2}]}


@pytest.fixture
def quiet_clip(tmp_path):
    path = tmp_path / "quiet.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(bytes(2 * 3200))
    return path


def test_decoded_waveform_is_shared_by_transcribe_and_alignment(monkeypatch, quiet_clip):
    fake = _FakeMlxWhisper()
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)

    seen_by_aligner = []

    def recording_align(segments, audio, language_code, device=None):
        seen_by_aligner.append(audio)
        return segments

    monkeypatch.setattr(asr_backend, "forced_align", recording_align)

    engine = asr_backend.MLXWhisperBackend.__new__(asr_backend.MLXWhisperBackend)
    engine._model_name = "base"
    engine.transcribe(str(quiet_clip), word_timestamps=True)

    (waveform,) = fake.received
    assert not isinstance(waveform, (str, bytes))
    assert len(waveform) > 0
    assert len(seen_by_aligner) == 1
    assert seen_by_aligner[0] is waveform
