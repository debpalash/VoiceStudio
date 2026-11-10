"""Long recordings must be transcribable end to end.

Reported: a 25-minute m4a sent through the MCP ``transcribe`` tool failed with
the bare text "Error executing tool transcribe: " — no reason. Two defects:

1. Every whole-file transcribe (/transcribe, the OpenAI route, batch) ran under a
   flat 300 s budget, so any recording longer than a few minutes was abandoned
   with a 504 even on a healthy GPU, and the still-running native call then
   blocked every retry. The budget now scales with the audio's length.
2. The MCP tool waits for the backend's budget (#2040), so it must mirror the
   scaled budget too or it gives up first on a long file.
"""
import asyncio
import os
import shutil
import subprocess

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest


# ── duration-scaled budget ──────────────────────────────────────────────────

def test_budget_scales_with_audio_length(monkeypatch):
    from services import asr_backend

    monkeypatch.setattr(asr_backend, "ASR_TRANSCRIBE_TIMEOUT_S", 300.0)
    monkeypatch.setattr(asr_backend, "ASR_TIMEOUT_REALTIME_FACTOR", 2.0)
    budget = asr_backend.whole_file_transcribe_timeout
    assert budget(None) == 300.0          # unknown length → flat floor
    assert budget(0) == 300.0
    assert budget(30.0) == 300.0          # short clip keeps failing fast
    assert budget(1519.7) == pytest.approx(3039.4)   # the reported 25-min file


def test_factor_zero_disables_scaling(monkeypatch):
    from services import asr_backend

    monkeypatch.setattr(asr_backend, "ASR_TRANSCRIBE_TIMEOUT_S", 300.0)
    monkeypatch.setattr(asr_backend, "ASR_TIMEOUT_REALTIME_FACTOR", 0.0)
    assert asr_backend.whole_file_transcribe_timeout(5000.0) == 300.0


def test_probe_reads_real_duration_and_never_raises(tmp_path):
    from services import asr_backend

    assert asr_backend.probe_audio_duration_s(str(tmp_path / "missing.wav")) is None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not on PATH")
    wav = tmp_path / "tone.wav"
    subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "3", str(wav)],
        check=True,
    )
    assert asr_backend.probe_audio_duration_s(str(wav)) == pytest.approx(3.0, abs=0.1)


class _StubBackend:
    id = "stub"

    def transcribe(self, _path, **_kw):
        return {"text": "hola", "segments": [{"start": 0.0, "end": 1.0, "text": "hola"}],
                "language": "es"}


@pytest.mark.usefixtures("asr_model_installed")
def test_rest_transcribe_uses_the_duration_scaled_budget(monkeypatch):
    """Fail-before: the route passed no timeout, so the flat 300 s applied."""
    from fastapi.testclient import TestClient

    seen = {}

    async def _spy(executor, fn, **kw):
        seen.update(kw)
        return fn()

    monkeypatch.setattr("services.asr_backend.get_capture_asr_backend", lambda: _StubBackend())
    monkeypatch.setattr("services.asr_backend.whole_file_transcribe_timeout_for", lambda _p: 3039.0)
    monkeypatch.setattr("services.asr_backend.run_transcribe_guarded", _spy)

    from main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    r = client.post("/transcribe", files={"audio": ("a.wav", b"\x00" * 32000, "audio/wav")})
    assert r.status_code == 200, r.text
    assert seen.get("timeout") == 3039.0


# ── MCP: the tool waits as long as the backend's scaled budget ─────────────

def test_mcp_transcribe_wait_follows_the_scaled_backend_budget(monkeypatch):
    """Fail-before: the MCP wait was the flat 300 s floor + grace, so a long
    file the backend was still transcribing was cut off client-side."""
    import mcp_server

    for name in ("OMNIVOICE_MCP_TIMEOUT_S", "OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S",
                 "OMNIVOICE_ASR_TIMEOUT_REALTIME_FACTOR"):
        monkeypatch.delenv(name, raising=False)
    short = mcp_server._post_timeout_s("transcribe", audio_s=30.0)
    long_ = mcp_server._post_timeout_s("transcribe", audio_s=1519.7)
    assert short == mcp_server._post_timeout_s("transcribe")       # floor unchanged
    assert long_ > 1519.7 * 2.0                                    # > backend budget
    monkeypatch.setenv("OMNIVOICE_ASR_TIMEOUT_REALTIME_FACTOR", "0")
    assert mcp_server._post_timeout_s("transcribe", audio_s=1519.7) == short


def test_mcp_audio_seconds_probes_in_memory_audio(tmp_path):
    import mcp_server

    assert mcp_server._audio_seconds(b"not audio") is None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not on PATH")
    wav = tmp_path / "tone.wav"
    subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "4", str(wav)],
        check=True,
    )
    assert mcp_server._audio_seconds(wav.read_bytes()) == pytest.approx(4.0, abs=0.1)
