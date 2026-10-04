"""Audio file I/O must not depend on TorchCodec / removed torchaudio APIs (#2378).

torchaudio 2.9 removed ``info`` and routes ``load``/``save`` through TorchCodec
(an optional install that needs FFmpeg shared libraries). Dub assembly died at
``torchaudio.load`` on the locked 2.9.1; the sibling ``torchaudio.info`` call
sites were worse because their ``except Exception`` blocks turned the resulting
``AttributeError`` into "segment not cached", silently re-rendering every
cached segment. All backend code reads and writes audio through the
``services.audio_io`` helpers (soundfile/ffmpeg fallbacks), except isolated
sidecars with their own tested audio adapters; this gate keeps the whole class
from coming back and restricts compatibility exceptions to audited adapters.
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
# and carries its own guarded soundfile fallback.
ALLOWED = {"services/audio_io.py", "engines/indextts/main.py"}
AUDIO_ADAPTERS = {
    "services/pyannote_audio_compat.py": {"ensure_pyannote_audio_compat"},
    "engines/cosyvoice_subprocess/main.py": {
        "_install_windows_rocm_audio_adapter", "_verify_windows_rocm_audio",
    },
}


def _direct_audio_calls(source: str, relative_path: str) -> list[str]:
    tree = ast.parse(source)
    allowed_lines = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in AUDIO_ADAPTERS.get(relative_path, set()):
            allowed_lines.update(range(node.lineno, node.end_lineno + 1))
    return [
        f"{relative_path}:{node.lineno} torchaudio.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in GATED
        and isinstance(node.value, ast.Name) and node.value.id in {"torchaudio", "ta"}
        and node.lineno not in allowed_lines
    ]


def test_backend_never_calls_torchaudio_io_directly():
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in ALLOWED or rel.startswith("tests/"):
            continue
        offenders.extend(_direct_audio_calls(path.read_text(encoding="utf-8"), rel))
    assert not offenders, (
        "Use services.audio_io.load_audio / audio_info / _safe_torchaudio_save "
        f"(TorchCodec-independent) instead of: {offenders}"
    )


def test_cosyvoice_exception_is_limited_to_its_isolated_audio_adapter():
    source = "def _install_windows_rocm_audio_adapter():\n    torchaudio.load = adapter\ntorchaudio.load('unsafe.wav')\n"
    assert _direct_audio_calls(source, "engines/cosyvoice_subprocess/main.py") == [
        "engines/cosyvoice_subprocess/main.py:3 torchaudio.load",
    ]
    assert len(_direct_audio_calls(source, "services/unrelated.py")) == 2


def test_pyannote_exception_is_limited_to_dispatcher_registration():
    source = "def ensure_pyannote_audio_compat():\n    torchaudio.load = adapter\ntorchaudio.load('unsafe.wav')\n"
    assert _direct_audio_calls(source, "services/pyannote_audio_compat.py") == [
        "services/pyannote_audio_compat.py:3 torchaudio.load",
    ]
    assert len(_direct_audio_calls(source, "services/unrelated.py")) == 2


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
