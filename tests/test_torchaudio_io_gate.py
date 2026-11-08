"""Audio file I/O must not depend on TorchCodec / removed torchaudio APIs (#2378).

torchaudio 2.9 removed ``info`` and routes ``load``/``save`` through TorchCodec
(an optional install that needs FFmpeg shared libraries). Dub assembly died at
``torchaudio.load`` on the locked 2.9.1; the sibling ``torchaudio.info`` call
sites were worse because their ``except Exception`` blocks turned the resulting
``AttributeError`` into "segment not cached", silently re-rendering every
cached segment. All backend code reads and writes audio through the
``services.audio_io`` helpers (soundfile/ffmpeg fallbacks); this gate keeps the
whole class from coming back.
"""
from __future__ import annotations

import ast
import struct
import wave
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
GATED = {"load", "info", "save"}
# audio_io wraps the calls; the IndexTTS sidecar cannot import parent services
# and carries its own guarded soundfile fallback; pyannote_audio_compat never
# reads audio itself: it wraps the ``torchaudio.load``/``info`` that pyannote (a
# third-party library) calls, routing them to soundfile — the same fix this gate
# enforces, applied to code we can't edit.
ALLOWED = {"services/audio_io.py", "engines/indextts/main.py", "services/pyannote_audio_compat.py"}


def test_backend_never_calls_torchaudio_io_directly():
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in ALLOWED or rel.startswith("tests/"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in GATED
                and isinstance(node.value, ast.Name)
                and node.value.id in {"torchaudio", "ta"}
            ):
                offenders.append(f"{rel}:{node.lineno} torchaudio.{node.attr}")
    assert not offenders, (
        "Use services.audio_io.load_audio / audio_info / _safe_torchaudio_save "
        f"(TorchCodec-independent) instead of: {offenders}"
    )


REPO = BACKEND.parent
# Beyond backend code: the packaged OmniVoice CLI (omnivoice-infer and
# omnivoice-infer-batch), its training scripts, repo scripts and both test
# trees all run without TorchCodec too (#3884). omnivoice/utils/audio.py keeps
# one guarded torchaudio.load with a pydub fallback.
REPO_ROOTS = ("backend", "omnivoice", "scripts", "tests")
REPO_ALLOWED = {"omnivoice/utils/audio.py"} | {f"backend/{rel}" for rel in ALLOWED}


def torchaudio_io_calls(tree: ast.AST) -> list:
    """Calls that reach torchaudio's load/save/info, however they are bound.

    Covers ``torchaudio.load(...)``, an aliased module (``import torchaudio as
    T``), a from-import (``from torchaudio import load as read``) and a
    qualified receiver (``audio_io.torchaudio.save(...)``). Plain references,
    such as a test spy keeping ``real_load = torchaudio.load``, are not calls
    and stay allowed.
    """
    modules = {"torchaudio"}
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "torchaudio":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "torchaudio":
            for alias in node.names:
                if alias.name in GATED:
                    functions[alias.asname or alias.name] = alias.name
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in GATED:
            receiver = func.value
            if (isinstance(receiver, ast.Name) and receiver.id in modules) or (
                isinstance(receiver, ast.Attribute) and receiver.attr == "torchaudio"
            ):
                found.append((node.lineno, func.attr))
        elif isinstance(func, ast.Name) and func.id in functions:
            found.append((node.lineno, functions[func.id]))
    return found


@pytest.mark.parametrize(
    "source,expected",
    [
        ("import torchaudio\ntorchaudio.load('a.wav')", [(2, "load")]),
        ("import torchaudio as T\nT.save('a.wav', x, 1)", [(2, "save")]),
        ("from torchaudio import load as read\nread('a.wav')", [(2, "load")]),
        ("from services import audio_io\naudio_io.torchaudio.info('a.wav')", [(2, "info")]),
        ("import torchaudio\nreal_load = torchaudio.load", []),
        ("import torchaudio\ntorchaudio.functional.resample(x, 1, 2)", []),
    ],
)
def test_gate_resolves_torchaudio_bindings(source, expected):
    assert torchaudio_io_calls(ast.parse(source)) == expected


def test_cli_scripts_and_tests_never_call_torchaudio_io_directly():
    offenders = []
    for root in REPO_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel in REPO_ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders += [f"{rel}:{line} torchaudio.{name}" for line, name in torchaudio_io_calls(tree)]
    assert not offenders, (
        "Use omnivoice.utils.audio.read_audio / save_audio (package and CLI) or "
        "services.audio_io.load_audio / _safe_torchaudio_save (backend, scripts, "
        f"tests), which work without TorchCodec, instead of: {offenders}"
    )


def test_omnivoice_save_and_read_round_trip_without_torchcodec(tmp_path, monkeypatch):
    import torch
    import torchaudio

    def needs_torchcodec(*args, **kwargs):
        raise ImportError("TorchCodec is required for load_with_torchcodec.")

    # torchaudio >= 2.9 without TorchCodec: every load/save raises.
    monkeypatch.setattr(torchaudio, "load", needs_torchcodec)
    monkeypatch.setattr(torchaudio, "save", needs_torchcodec)
    from omnivoice.utils.audio import read_audio, save_audio

    wave_in = torch.linspace(-0.5, 0.5, 480).reshape(1, -1)
    save_audio(tmp_path / "f.wav", wave_in, 24000)
    back, rate = read_audio(str(tmp_path / "f.wav"))
    assert rate == 24000 and back.shape == (1, 480)
    assert torch.allclose(back, wave_in, atol=1e-6)  # 32-bit float WAV, like torchaudio

    save_audio(tmp_path / "p.flac", wave_in, 24000, bits_per_sample=16)
    back, rate = read_audio(str(tmp_path / "p.flac"))
    assert rate == 24000 and torch.allclose(back, wave_in, atol=1e-4)


def _write_wav(path, frames=2400, rate=24000, channels=1):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{frames * channels}h", *([100] * frames * channels)))


@pytest.fixture
def torchaudio_2_9(monkeypatch):
    """torchaudio 2.9: no ``info``, and ``load`` needs a TorchCodec that is absent."""
    import torchaudio

    def _no_codec(*_a, **_k):
        raise ImportError("TorchCodec is required for load_with_torchcodec.")

    monkeypatch.delattr(torchaudio, "info", raising=False)
    monkeypatch.setattr(torchaudio, "load", _no_codec)


def test_audio_info_reads_header_without_torchaudio_info(tmp_path, torchaudio_2_9):
    from services.audio_io import audio_info

    wav = tmp_path / "seg.wav"
    _write_wav(wav, frames=2400, rate=24000, channels=2)
    info = audio_info(wav)
    assert (info.sample_rate, info.num_frames, info.num_channels, info.bits_per_sample) == (
        24000, 2400, 2, 16,
    )


def test_cached_segment_check_accepts_a_real_cache_without_torchaudio_info(
    tmp_path, torchaudio_2_9
):
    from api.routers.dub_generate import _cached_payload_intact
    from services.audio_io import audio_info

    wav = tmp_path / "seg.wav"
    _write_wav(wav)
    assert _cached_payload_intact(str(wav), audio_info(str(wav)))
    # A truncated payload is still rejected (the check's reason for existing).
    wav.write_bytes(wav.read_bytes()[:-1000])
    assert not _cached_payload_intact(str(wav), audio_info(str(wav)))


def test_load_audio_reads_segment_when_torchcodec_is_missing(tmp_path, torchaudio_2_9):
    from services.audio_io import load_audio

    wav = tmp_path / "seg.wav"
    _write_wav(wav, frames=480, rate=24000)
    samples, rate = load_audio(str(wav))
    assert rate == 24000 and tuple(samples.shape) == (1, 480)


def test_truncated_float_wav_keeps_declared_size_so_the_cache_is_rejected(
    tmp_path, torchaudio_2_9
):
    import numpy as np
    import soundfile as sf

    from api.routers.dub_generate import _cached_payload_intact
    from services.audio_io import audio_info

    wav = tmp_path / "seg.wav"
    sf.write(str(wav), np.full(2400, 0.1, dtype="float32"), 24000, subtype="FLOAT")
    info = audio_info(wav)
    assert (info.num_frames, info.bits_per_sample) == (2400, 32)
    wav.write_bytes(wav.read_bytes()[:-1000])
    info = audio_info(wav)
    assert info.num_frames == 2400, "libsndfile would clamp this to the bytes present"
    assert not _cached_payload_intact(str(wav), info)


def test_audio_info_leaves_the_stream_where_it_found_it(tmp_path, monkeypatch, torchaudio_2_9):
    import io

    import soundfile as sf

    import services.audio_io as aio

    wav = tmp_path / "seg.wav"
    _write_wav(wav, frames=480)
    for payload in (wav.read_bytes(), b"not audio at all" * 8):
        buf = io.BytesIO(b"junk" + payload)
        buf.seek(4)
        seen = []

        def fake_load(source):
            seen.append(source.tell())
            return aio.torch.zeros(1, 480), 24000

        monkeypatch.setattr(aio, "load_audio", fake_load)
        if payload.startswith(b"RIFF"):
            assert aio.audio_info(buf).num_frames == 480
        else:
            # sf.info consumes header bytes before rejecting the stream; the
            # decoder fallback must start from the caller's position again.
            monkeypatch.setattr(sf, "info", lambda s: (s.read(16), (_ for _ in ()).throw(RuntimeError("bad")))[1])
            aio.audio_info(buf)
            assert seen == [4]
        if payload.startswith(b"RIFF"):
            assert buf.tell() == 4
