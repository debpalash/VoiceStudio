"""The dub speaker-count hint must be honored on EVERY diarization path.

Field report (v0.3.9): setting the dub "Speakers" count changed nothing — the
`?num_speakers=` hint reached `_diarize()` and then died on 3 of its 4
branches (FunASR inline-turns shortcut, pyannote-unavailable heuristic
fallback, pyannote-crash heuristic fallback). Speakers blended and auto-clones
were cut from mixed-speaker audio.

These tests drive `dub_transcribe_stream`'s async generator directly (the
established pattern in test_dub_transcribe.py — no TestClient, no GPU, no
pyannote) and pin:

  * an explicit num_speakers routes a turns-capable job through pyannote
    (with the hint) instead of the inline-turns shortcut;
  * when a branch cannot honor the hint exactly, a `warning` SSE event says so
    honestly instead of dropping it silently;
  * heuristic labels skip auto voice-clone extraction (clone-purity guard)
    with their own warning;
  * the legacy POST /dub/transcribe/{job_id} endpoint exposes the same
    (clamped) num_speakers parameter.
"""
from __future__ import annotations

import asyncio
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest
# These tests exercise ASR-consumer mechanics and assume ASR weights are
# installed - neutralize the no-ASR preflight (its own suite:
# tests/test_asr_model_missing.py).
pytestmark = pytest.mark.usefixtures("asr_model_installed")


from api.routers import dub_core as dc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav(path: Path, seconds: float = 1.0, sr: int = 16000) -> None:
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))


class _FakeASR:
    """Minimal ASR backend; `turns` simulates FunASR-style inline diarization."""

    id = "fake"

    def __init__(self, turns=None):
        self._turns = turns or []

    def ensure_loaded(self):
        pass

    def transcribe(self, path, *, word_timestamps=True):
        return {
            "chunks": [
                {"text": "Hello there my friend.", "timestamp": (0.0, 0.4)},
                {"text": "I am doing quite well today.", "timestamp": (0.5, 0.9)},
            ],
            "segments": self._turns,
            "language": "en",
        }

    def unload(self):
        pass


class _FakeTurn:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class _FakeDiar:
    def itertracks(self, yield_label=True):
        yield _FakeTurn(0.0, 0.45), None, "SPEAKER_00"
        yield _FakeTurn(0.45, 1.0), None, "SPEAKER_01"


class _RecordingPipe:
    """Stands in for the pyannote pipeline; records the num_speakers kwarg."""

    def __init__(self, crash=False):
        self.calls: list = []
        self._crash = crash

    def __call__(self, path, num_speakers=None):
        self.calls.append(num_speakers)
        if self._crash:
            raise RuntimeError("pyannote exploded: simulated")
        return _FakeDiar()


def _wire_stream(tmp_path, monkeypatch, *, job_id, asr, diar_pipeline):
    """Common monkeypatching for driving the stream happy path end-to-end."""
    audio = tmp_path / "a.wav"
    _make_wav(audio, seconds=1.0)
    dc._dub_jobs[job_id] = {
        "audio_path": str(audio), "vocals_path": None, "scene_cuts": [],
    }

    fake_model = MagicMock()
    fake_model._asr_pipe = MagicMock()

    async def _ok_model():
        return fake_model

    monkeypatch.setattr(dc, "get_model", _ok_model)
    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend", lambda *a, **k: asr,
    )
    monkeypatch.setattr(dc, "get_diarization_pipeline", diar_pipeline)
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda *a, **k: None)
    monkeypatch.setattr(dc, "restore_tts_after_asr", lambda *a, **k: None)
    monkeypatch.setattr(dc, "_save_job", lambda *a, **k: None)
    # Keep the no-token branch deterministic (never read this machine's HF creds).
    monkeypatch.setattr("services.token_resolver.resolve", lambda *a, **k: None)


def _run_stream(job_id, num_speakers=None) -> str:
    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id, num_speakers=num_speakers)
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(parts)

    try:
        return asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)


_TURNS = [
    {"start": 0.0, "end": 0.45, "speaker": "Speaker 1"},
    {"start": 0.5, "end": 0.9, "speaker": "Speaker 2"},
]

_NO_PIPE = lambda return_error=False: (None, "NO_TOKEN") if return_error else None  # noqa: E731


# ---------------------------------------------------------------------------
# Branch 1 — FunASR inline-turns shortcut must not eat the hint
# ---------------------------------------------------------------------------

def test_hint_routes_turns_job_through_pyannote(tmp_path, monkeypatch):
    """With inline ASR turns present AND pyannote loadable, an explicit
    num_speakers must skip the turns shortcut and reach pyannote as a kwarg.
    Pre-fix the shortcut returned early and the hint was never consulted."""
    pipe = _RecordingPipe()
    _wire_stream(
        tmp_path, monkeypatch, job_id="t_hint_pyannote",
        asr=_FakeASR(turns=_TURNS),
        diar_pipeline=lambda return_error=False: (pipe, None) if return_error else pipe,
    )
    body = _run_stream("t_hint_pyannote", num_speakers=2)

    assert pipe.calls == [2], f"pyannote must be called once with the hint: {pipe.calls}"
    assert "event: final" in body, body
    # The hint was fully honored — no hint warning may fire.
    assert "Speaker-count hint ignored" not in body, body


def test_no_hint_keeps_turns_fast_path(tmp_path, monkeypatch):
    """Without a hint, the inline-turns shortcut stays the fast path: pyannote
    is never invoked and no warning fires."""
    pipe = _RecordingPipe()
    _wire_stream(
        tmp_path, monkeypatch, job_id="t_nohint_turns",
        asr=_FakeASR(turns=_TURNS),
        diar_pipeline=lambda return_error=False: (pipe, None) if return_error else pipe,
    )
    body = _run_stream("t_nohint_turns", num_speakers=None)

    assert pipe.calls == [], "turns fast path must skip pyannote when no hint is set"
    assert "event: final" in body, body
    assert "event: warning" not in body, body


def test_hint_with_turns_but_no_pyannote_warns_hint_ignored(tmp_path, monkeypatch):
    """Turns present, hint set, pyannote unavailable: keep the turns (best
    labels available) but tell the user the hint was ignored — never silence."""
    _wire_stream(
        tmp_path, monkeypatch, job_id="t_hint_turns_warn",
        asr=_FakeASR(turns=_TURNS), diar_pipeline=_NO_PIPE,
    )
    body = _run_stream("t_hint_turns_warn", num_speakers=3)

    assert "event: warning" in body, body
    assert "Speaker-count hint ignored" in body, body
    assert "may differ from the 3 you set" in body, body
    # Turns are trusted labels — the clone-purity guard must NOT fire.
    assert "auto voice cloning skipped" not in body, body


# ---------------------------------------------------------------------------
# Branches 2+3 — heuristic fallbacks: hint threaded + honest warning + clone guard
# ---------------------------------------------------------------------------

def test_heuristic_fallback_warns_approximate_and_skips_clones(tmp_path, monkeypatch):
    """No turns, no pyannote, hint set: the heuristic cycles the requested
    count (approximate honesty), the warning says exactly that, and auto
    voice-clone extraction is skipped because gap-based labels are estimates."""
    _wire_stream(
        tmp_path, monkeypatch, job_id="t_hint_heur",
        asr=_FakeASR(turns=[]), diar_pipeline=_NO_PIPE,
    )
    body = _run_stream("t_hint_heur", num_speakers=3)

    assert "event: warning" in body, body
    assert "only approximately honored" in body, body
    assert "cycles 3 speaker labels" in body, body
    # Clone-purity guard fires with its own warning.
    assert dc.CLONE_SKIP_HEURISTIC_MSG in body, body
    assert "event: final" in body, body


def test_heuristic_fallback_without_hint_keeps_legacy_warning(tmp_path, monkeypatch):
    """No hint → the pre-existing fallback warning is unchanged (no hint text),
    but the clone-purity guard still protects against gap-based labels."""
    _wire_stream(
        tmp_path, monkeypatch, job_id="t_nohint_heur",
        asr=_FakeASR(turns=[]), diar_pipeline=_NO_PIPE,
    )
    body = _run_stream("t_nohint_heur", num_speakers=None)

    assert "event: warning" in body, body
    assert "silence-gap" in body, body
    assert "approximately honored" not in body, body
    assert dc.CLONE_SKIP_HEURISTIC_MSG in body, body


def test_pyannote_crash_with_turns_falls_back_to_turns_not_heuristic(tmp_path, monkeypatch):
    """When the hint routes a turns-job through pyannote and pyannote crashes
    mid-run, the inline turns are the fallback (better than the heuristic) and
    the warning says the hint was ignored."""
    pipe = _RecordingPipe(crash=True)
    _wire_stream(
        tmp_path, monkeypatch, job_id="t_crash_turns",
        asr=_FakeASR(turns=_TURNS),
        diar_pipeline=lambda return_error=False: (pipe, None) if return_error else pipe,
    )
    body = _run_stream("t_crash_turns", num_speakers=2)

    assert pipe.calls == [2], pipe.calls
    assert "crashed mid-run" in body, body
    assert "built-in speaker turns" in body, body
    assert "Speaker-count hint ignored" in body, body
    # Turns labels → clones stay allowed.
    assert "auto voice cloning skipped" not in body, body


# ---------------------------------------------------------------------------
# Shared clamp + legacy endpoint parity
# ---------------------------------------------------------------------------

class TestClampNumSpeakers:
    @pytest.mark.parametrize("raw,expected", [
        (None, None), (1, 1), (5, 5), (20, 20),
        (0, None), (-2, None), (21, None), ("7", 7), ("junk", None),
    ])
    def test_clamp(self, raw, expected):
        assert dc._clamp_num_speakers(raw) == expected


def test_legacy_transcribe_endpoint_accepts_num_speakers():
    """POST /dub/transcribe/{job_id} (the CLI's endpoint) exposes the same
    optional num_speakers query parameter as the SSE stream. Pre-fix the
    legacy route had no way to express a speaker count at all."""
    import inspect
    from typing import Optional

    sig = inspect.signature(dc.dub_transcribe)
    assert "num_speakers" in sig.parameters, "legacy endpoint lost num_speakers"
    p = sig.parameters["num_speakers"]
    assert p.default is None, "num_speakers must be optional (auto-detect default)"
    assert p.annotation == Optional[int]

    # And the stream endpoint still has it too (parity in both directions).
    stream_sig = inspect.signature(dc.dub_transcribe_stream)
    assert "num_speakers" in stream_sig.parameters


def test_cli_exposes_speakers_flag(capsys):
    """omnivoice-dub --speakers N must parse and be forwarded as the
    num_speakers query param on the legacy transcribe endpoint."""
    import importlib.util

    cli_path = Path(__file__).resolve().parents[1] / "omnivoice" / "cli" / "dub.py"
    spec = importlib.util.spec_from_file_location("_omnivoice_cli_dub", cli_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Parse-level: --help lists the flag (argparse would reject an unknown one).
    with pytest.raises(SystemExit):
        mod.main(["--help"])
    assert "--speakers" in capsys.readouterr().out

    # Wiring-level: the flag is forwarded as the num_speakers query param.
    src = cli_path.read_text(encoding="utf-8")
    assert "num_speakers={args.speakers}" in src
