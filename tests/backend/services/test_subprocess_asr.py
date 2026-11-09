"""Crash-isolated ASR (Wave 4.2 / Spec 7).

Validates the SubprocessASRBackend round-trip + crash recovery against the
stdlib-only echo sidecar (no torch, runs everywhere). Mirrors
test_subprocess_backend.py's echo-subclass pattern.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch  # noqa: F401 — front-load torch during collection (matches
# test_subprocess_backend.py); transcribe() lazily imports model_manager,
# and a mid-test cold torch import hangs on this dev box's Triton cache.

from services.subprocess_asr import SubprocessASRBackend

REPO_ROOT = Path(__file__).resolve().parents[3]
ECHO_SCRIPT = REPO_ROOT / "backend" / "engines" / "_echo" / "main.py"


class EchoASRBackend(SubprocessASRBackend):
    id = "_echo_asr"
    display_name = "Echo ASR (test)"

    @classmethod
    def is_available(cls):
        return (True, "ready") if ECHO_SCRIPT.is_file() else (False, "missing")

    @classmethod
    def venv_python(cls):
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls):
        return ECHO_SCRIPT


@pytest.fixture
def asr(monkeypatch):
    # The echo sidecar self-crashes after one frame when OMNIVOICE_ECHO_CRASH
    # is set (a sibling subprocess test uses it). Clear it by default so the
    # non-crash tests here never inherit a leaked flag via os.environ.copy();
    # the crash test re-sets it explicitly.
    monkeypatch.delenv("OMNIVOICE_ECHO_CRASH", raising=False)
    monkeypatch.delenv("OMNIVOICE_ECHO_CRASH_NO_REPLY", raising=False)
    b = EchoASRBackend()
    yield b
    try:
        b.shutdown()
    except Exception:
        pass


def test_transcribe_round_trip(asr):
    result = asr.transcribe("/tmp/clip.wav", word_timestamps=False)
    assert result["language"] == "en"
    assert result["segments"][0]["text"] == "echo:/tmp/clip.wav"


def test_transcribe_exposes_chunks_from_segments(asr):
    # Dub reads only `chunks`; a sidecar reply with just `segments` used to
    # produce "Transcription produced no segments" (#2786).
    result = asr.transcribe("/tmp/clip.wav", word_timestamps=False)
    seg = result["segments"][0]
    assert result["chunks"] == [
        {"text": seg["text"], "timestamp": (seg.get("start"), seg.get("end"))}
    ]


def test_two_calls_reuse_one_sidecar(asr):
    asr.transcribe("/a.wav")
    proc1 = asr._proc.pid
    asr.transcribe("/b.wav")
    assert asr._proc.pid == proc1  # long-lived sidecar, not respawned per call


def test_shared_loader_contract_starts_and_reuses_sidecar(asr):
    """load_active_asr_backend calls ensure_loaded before transcribe."""
    asr.ensure_loaded()
    proc = asr._proc.pid
    result = asr.transcribe("/after-preflight.wav")
    assert asr._proc.pid == proc
    assert result["segments"][0]["text"] == "echo:/after-preflight.wav"


def test_crash_mid_transcribe_fails_then_respawns(monkeypatch, asr):
    # The sidecar exits BEFORE replying — a deterministic dead pipe simulating
    # a CTranslate2 GPU-teardown segfault mid-transcription.
    monkeypatch.setenv("OMNIVOICE_ECHO_CRASH_NO_REPLY", "1")
    with pytest.raises(RuntimeError) as ei:
        asr.transcribe("/boom.wav")
    msg = str(ei.value)
    assert "_echo_asr" in msg  # decorated with the engine id
    assert "device=" in msg

    # Backend is still healthy: a fresh call (crash hook off) respawns a new
    # sidecar via _spawn's dead-process check.
    monkeypatch.delenv("OMNIVOICE_ECHO_CRASH_NO_REPLY", raising=False)
    result = asr.transcribe("/after.wav")
    assert result["segments"][0]["text"] == "echo:/after.wav"


def test_registry_exposes_isolated_backend():
    # The lazy registry lists + resolves the crash-isolated backend.
    from services import asr_backend
    assert "faster-whisper-isolated" in asr_backend._REGISTRY
    cls = asr_backend._REGISTRY["faster-whisper-isolated"]
    assert cls.id == "faster-whisper-isolated"
    ok, msg = cls.is_available()
    assert isinstance(ok, bool)  # available iff faster-whisper installed + script present
    assert isinstance(msg, str) and msg  # honest reason either way


def test_isolated_backend_listed_in_settings_with_explanatory_hint():
    """#730 residual B: the escape-hatch engine must be a first-class row in the
    Settings engine list — subprocess isolation flagged, an install_hint that
    explains WHAT it's for (reclaiming hung transcribes + their VRAM), and an
    honest availability verdict."""
    from services import asr_backend

    entries = {b["id"]: b for b in asr_backend.list_backends()}
    entry = entries.get("faster-whisper-isolated")
    assert entry is not None, "isolated backend missing from list_backends()"
    assert entry["display_name"] == "Faster-Whisper (crash-isolated subprocess)"
    assert entry["isolation_mode"] == "subprocess"
    hint = entry["install_hint"] or ""
    assert "separate process" in hint and "VRAM" in hint, hint
    assert isinstance(entry["available"], bool)
    if not entry["available"]:
        assert entry["reason"]  # unavailable must always say why
    # Wraps the same CTranslate2 engine as faster-whisper → same device support
    # (the registry default ("cpu",) would dishonestly hide CUDA routing).
    # Lists and tuples serialise to the same JSON array; compare the contents.
    assert list(entry["gpu_compat"]) == ["cuda", "cpu"]


def test_get_active_asr_backend_caches_isolated_singleton(monkeypatch):
    """Selecting the isolated engine must not spawn a fresh sidecar (and leak an
    atexit hook) per request: SubprocessBackend instances own a child process,
    so get_active_asr_backend must hand back one process-wide instance."""
    from services import asr_backend

    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "faster-whisper-isolated")
    monkeypatch.setattr(asr_backend, "_ISOLATED_INSTANCES", {})
    a = asr_backend.get_active_asr_backend()
    b = asr_backend.get_active_asr_backend()
    try:
        assert a is b, "isolated backend must be a process-wide singleton"
        assert a.id == "faster-whisper-isolated"
    finally:
        try:
            a.shutdown()
        except Exception:
            pass


def test_generate_is_not_supported(asr):
    with pytest.raises(NotImplementedError):
        asr.generate("text")


def test_sidecar_accepts_max_profile_decode_width(monkeypatch):
    """The global Max preset sends beam_size/best_of=8 to this child."""
    from engines._asr_sidecar import main as sidecar

    calls = []

    class FakeModel:
        def transcribe(self, path, **options):
            calls.append((path, options))
            segments = [SimpleNamespace(start=0, end=1, text="heard", words=[])]
            return segments, SimpleNamespace(language="en")

    monkeypatch.setattr(sidecar, "_get_model", lambda: FakeModel())
    result = sidecar._transcribe(
        "/max.wav",
        False,
        {"beam_size": 8, "best_of": 8},
    )
    assert calls == [
        (
            "/max.wav",
            {"word_timestamps": False, "condition_on_previous_text": False,
             "beam_size": 8, "best_of": 8},
        )
    ]
    assert result["text"] == "heard"
