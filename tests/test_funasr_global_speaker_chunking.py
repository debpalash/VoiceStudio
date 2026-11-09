"""Whole-recording transcription for backends with per-call speaker labels."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from services.asr_backend import FunASRBackend

pytestmark = pytest.mark.usefixtures("asr_model_installed")


def _silent_wav(path, seconds, sr=16000):
    import numpy as np
    import soundfile as sf

    sf.write(str(path), np.zeros(int(round(seconds * sr)), dtype="float32"), sr, subtype="PCM_16")


@dataclass
class _Recorder:
    """Fake ASR backend; remembers the length in seconds of each submitted file."""
    full_audio_needed: bool
    seen_seconds: list = field(default_factory=list)

    @property
    def requires_full_audio_for_speaker_consistency(self):
        return self.full_audio_needed

    def ensure_loaded(self):
        pass

    def transcribe(self, path, *, word_timestamps=True):
        import soundfile as sf

        info = sf.info(path)
        self.seen_seconds.append(info.frames / info.samplerate)
        return {
            "chunks": [{"text": "ok then", "timestamp": (0.2, 1.4)}],
            "segments": [{"text": "ok then", "start": 0.2, "end": 1.4, "speaker": "Speaker 2"}],
            "language": "en",
        }


def _run(monkeypatch, tmp_path, backend, seconds):
    from api.routers import dub_core as dc
    import services.asr_backend as ab
    import services.speaker_clone as sc

    wav = tmp_path / "in.wav"
    _silent_wav(wav, seconds)
    job_id = f"t_global_{id(backend)}"
    dc._dub_jobs[job_id] = {"audio_path": str(wav), "vocals_path": None, "scene_cuts": []}

    guarded_calls = []

    async def _stub_guarded(pool, fn, **kw):
        guarded_calls.append(kw)
        return fn()

    def _no_pyannote(*a, **k):
        pytest.fail("pyannote must be skipped when the backend supplies speaker turns")

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(dc, "should_preload_tts_asr", lambda: False)
    monkeypatch.setattr(ab, "get_active_asr_backend", lambda *a, **k: backend)
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda *a, **k: None)
    monkeypatch.setattr(dc, "restore_tts_after_asr", lambda *a, **k: None)
    monkeypatch.setattr(dc, "_save_job", lambda *a, **k: None)
    monkeypatch.setattr(sc, "extract_speaker_clones", lambda *a, **k: {})
    monkeypatch.setattr(dc, "get_diarization_pipeline", _no_pyannote)
    monkeypatch.setattr(dc, "run_transcribe_guarded", _stub_guarded)
    monkeypatch.setattr(dc, "ASR_TRANSCRIBE_TIMEOUT_S", 777.5)
    monkeypatch.setattr(dc, "TRANSCRIBE_CHUNK_TIMEOUT_S", 44.25)

    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id, per_segment_refs=False)
        out = []
        async for part in resp.body_iterator:
            out.append(part.decode() if isinstance(part, (bytes, bytearray)) else str(part))
        return "".join(out)

    try:
        body = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)
    return body, guarded_calls


def _start_payload(body):
    lines = body.split("\n")
    idx = lines.index("event: start")
    return json.loads(lines[idx + 1].split("data:", 1)[1])


@pytest.mark.parametrize("env_value, flag", [("cam++", True), ("", False)])
def test_funasr_reports_whole_audio_need_per_env(monkeypatch, env_value, flag):
    monkeypatch.setenv("ASR_FUNASR_SPK", env_value)
    assert FunASRBackend().requires_full_audio_for_speaker_consistency is flag


def test_speaker_backend_receives_entire_file_once(monkeypatch, tmp_path):
    rec = _Recorder(full_audio_needed=True)
    body, guarded = _run(monkeypatch, tmp_path, rec, 71)
    assert len(rec.seen_seconds) == 1
    assert rec.seen_seconds[0] == pytest.approx(71.0, abs=0.1)
    assert [g["what"] for g in guarded] == ["Dub chunk 1/1"]
    assert guarded[0]["timeout"] == 777.5
    assert guarded[0]["timeout_env"] == "OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S"
    ev = _start_payload(body)
    assert ev["chunks"] == 1
    assert ev["chunk_s"] == pytest.approx(71.0, abs=0.1)


def test_regular_backend_still_sliced_into_windows(monkeypatch, tmp_path):
    rec = _Recorder(full_audio_needed=False)
    body, guarded = _run(monkeypatch, tmp_path, rec, 71)
    assert [round(x) for x in rec.seen_seconds] == [30, 30, 11]
    assert len(guarded) == 3
    assert {(g["timeout"], g["timeout_env"]) for g in guarded} == {
        (44.25, "OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S")
    }
    ev = _start_payload(body)
    assert ev["chunks"] == 3
    assert ev["chunk_s"] == pytest.approx(30.0)
