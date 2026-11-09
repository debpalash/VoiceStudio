"""VoxCPM2 engine guardrails — version floor, reference-clip prep, tail trim.

Three independent hardenings of the voxcpm2 path, all backend-only and
platform-identical:

  1. Version FLOOR (>=2.0.3): 2.0.3 fixed an audio-quality bug on Apple
     Silicon (low-precision dtypes on MPS). Every install hint carries the
     floor; an already-installed older version keeps working (available=True,
     no forced reinstall) but surfaces an actionable upgrade hint.
  2. Reference-clip preparation: the `voxcpm` package no longer trims
     reference audio internally, so raw user clips reached the model
     unconditioned. The clone path now trims edge silence and caps the
     reference at 30 s — fail-open, and a no-op for short clean clips.
  3. Trailing-silence guard: generations often end with a long near-silent
     tail. Output is trimmed to the last voiced sample + ~0.3 s. Silence-trim
     ONLY (no content analysis); a no-op on outputs without a silent tail.

All tests use a fake `voxcpm` module / fake model (test_engines.py pattern) —
no real model, so they run on every CI box.
"""
import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import logging
import sys
import types

import numpy as np
import pytest
import soundfile as sf
import torch

from services import tts_backend
from services.audio_dsp import trim_trailing_silence


# ── Helpers ──────────────────────────────────────────────────────────────────

SPEECH = 0.5    # well above the -50 dBFS (~0.00316) silence floor
SR = 16000


def _write_wav(path, segments, sr=SR):
    """Write a mono wav from (amplitude, seconds) segments; return its path."""
    audio = np.concatenate(
        [np.full(int(s * sr), a, dtype=np.float32) for a, s in segments]
    )
    sf.write(str(path), audio, sr)
    return str(path)


@pytest.fixture()
def fresh_ref_cache(monkeypatch):
    """Isolate the prepared-reference cache per test."""
    monkeypatch.setattr(tts_backend, "_VOXCPM_REF_PREP_CACHE", {})


# ── 1. Version floor ─────────────────────────────────────────────────────────


def test_version_floor_hint_on_old_version(monkeypatch):
    monkeypatch.setitem(sys.modules, "voxcpm", types.ModuleType("voxcpm"))
    monkeypatch.setattr(tts_backend, "_voxcpm_installed_version", lambda: "2.0.1")
    ok, msg = tts_backend.VoxCPM2Backend.is_available()
    assert ok is True  # floor, not pin — existing installs keep working
    assert "2.0.1" in msg and "2.0.3" in msg
    assert 'pip install --upgrade "voxcpm>=2.0.3"' in msg


def test_version_floor_no_hint_at_or_above_floor(monkeypatch):
    monkeypatch.setitem(sys.modules, "voxcpm", types.ModuleType("voxcpm"))
    for v in ("2.0.3", "2.0.10", "2.1.0", "3.0.0"):
        monkeypatch.setattr(tts_backend, "_voxcpm_installed_version", lambda v=v: v)
        assert tts_backend.VoxCPM2Backend.is_available() == (True, "ready")


def test_version_floor_unknown_version_does_not_nag(monkeypatch):
    monkeypatch.setitem(sys.modules, "voxcpm", types.ModuleType("voxcpm"))
    for v in (None, "unknown", ""):
        monkeypatch.setattr(tts_backend, "_voxcpm_installed_version", lambda v=v: v)
        assert tts_backend.VoxCPM2Backend.is_available() == (True, "ready")


def test_version_floor_in_not_installed_message(monkeypatch):
    monkeypatch.delitem(sys.modules, "voxcpm", raising=False)
    ok, msg = tts_backend.VoxCPM2Backend.is_available()
    if not ok:  # only asserts on boxes without a real voxcpm install
        assert 'pip install "voxcpm>=2.0.3"' in msg
        assert "voxcpm==" not in msg  # floor only, never an exact pin


def test_version_floor_load_time_warning(monkeypatch, caplog):
    fake = types.ModuleType("voxcpm")

    class _FakeVoxCPM:
        @classmethod
        def from_pretrained(cls, *a, **kw):
            return types.SimpleNamespace()

    fake.VoxCPM = _FakeVoxCPM
    monkeypatch.setitem(sys.modules, "voxcpm", fake)
    monkeypatch.setattr(tts_backend, "_voxcpm_installed_version", lambda: "2.0.2")
    backend = tts_backend.VoxCPM2Backend()
    with caplog.at_level(logging.WARNING, logger="omnivoice.tts"):
        backend._ensure_loaded()
    assert any("voxcpm>=2.0.3" in r.getMessage() for r in caplog.records)


def test_version_tuple_parsing():
    vt = tts_backend._version_tuple
    assert vt("2.0.3") == (2, 0, 3)
    assert vt("2.0.10") > vt("2.0.3")
    assert vt("2.1rc1") == (2, 1)
    assert vt("garbage") is None


# ── 2. Reference-clip preparation ────────────────────────────────────────────


def test_ref_prep_trims_edge_silence(tmp_path, fresh_ref_cache):
    raw = _write_wav(tmp_path / "ref.wav", [(0.0, 1.0), (SPEECH, 0.5), (0.0, 1.0)])
    prepared = tts_backend._prepare_voxcpm_ref(raw)
    assert prepared != raw
    audio, sr = sf.read(prepared, dtype="float32")
    # 0.5 s of speech + the 50 ms edge pad on each side.
    assert abs(len(audio) / sr - 0.6) < 0.02
    # Leading silence really gone: speech starts within the edge pad.
    first_voiced = np.flatnonzero(np.abs(audio) > 0.01)[0]
    assert first_voiced / sr <= 0.06


def test_ref_prep_caps_length(tmp_path, fresh_ref_cache):
    raw = _write_wav(tmp_path / "long.wav", [(SPEECH, 40.0)], sr=8000)
    prepared = tts_backend._prepare_voxcpm_ref(raw)
    assert prepared != raw
    audio, sr = sf.read(prepared, dtype="float32")
    assert abs(len(audio) / sr - 30.0) < 0.01  # capped at 30 s


def test_ref_prep_noop_on_short_clean_clip(tmp_path, fresh_ref_cache):
    raw = _write_wav(tmp_path / "clean.wav", [(SPEECH, 0.5)])
    # Untouched means: the ORIGINAL path comes back, file not rewritten.
    assert tts_backend._prepare_voxcpm_ref(raw) == raw


def test_ref_prep_noop_on_all_silence_clip(tmp_path, fresh_ref_cache):
    # Nothing above the floor to anchor a trim — fail-open, original path.
    raw = _write_wav(tmp_path / "silent.wav", [(0.0, 2.0)])
    assert tts_backend._prepare_voxcpm_ref(raw) == raw


def test_ref_prep_fail_open_on_unreadable_path(fresh_ref_cache):
    missing = "/nonexistent/dir/ref.wav"
    assert tts_backend._prepare_voxcpm_ref(missing) == missing


def test_ref_prep_result_is_cached(tmp_path, fresh_ref_cache):
    raw = _write_wav(tmp_path / "ref.wav", [(0.0, 1.0), (SPEECH, 0.5), (0.0, 1.0)])
    first = tts_backend._prepare_voxcpm_ref(raw)
    assert tts_backend._prepare_voxcpm_ref(raw) == first  # no second temp file


def test_generate_passes_prepared_ref_to_model(tmp_path, fresh_ref_cache):
    raw = _write_wav(tmp_path / "ref.wav", [(0.0, 1.0), (SPEECH, 0.5), (0.0, 1.0)])
    backend = tts_backend.VoxCPM2Backend()
    backend._ensure_loaded = lambda: None
    captured = {}

    def _fake_generate(**kw):
        captured.update(kw)
        return np.full(4800, SPEECH, dtype=np.float32)

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    backend.generate("hello", ref_audio=raw, ref_text="the reference line")

    assert captured["reference_wav_path"] != raw          # prepared copy
    assert captured["prompt_wav_path"] == captured["reference_wav_path"]
    assert os.path.exists(captured["reference_wav_path"])
    assert captured["prompt_text"] == "the reference line"


def test_generate_clean_ref_reaches_model_unchanged(tmp_path, fresh_ref_cache):
    raw = _write_wav(tmp_path / "clean.wav", [(SPEECH, 0.5)])
    backend = tts_backend.VoxCPM2Backend()
    backend._ensure_loaded = lambda: None
    captured = {}

    def _fake_generate(**kw):
        captured.update(kw)
        return np.full(4800, SPEECH, dtype=np.float32)

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    backend.generate("hello", ref_audio=raw)
    assert captured["reference_wav_path"] == raw  # byte-identical original


# ── 3. Trailing-silence guard ────────────────────────────────────────────────


def test_tail_trim_cuts_long_silent_tail():
    sr = 1000
    audio = torch.cat([torch.full((sr,), SPEECH), torch.zeros(2 * sr)])
    out = trim_trailing_silence(audio, sr)
    assert out.shape[-1] == sr + int(0.3 * sr)  # keeps the ~0.3 s natural tail


def test_tail_trim_noop_without_silent_tail():
    sr = 1000
    audio = torch.full((sr,), SPEECH)
    assert trim_trailing_silence(audio, sr) is audio  # same object, untouched


def test_tail_trim_noop_on_short_natural_tail():
    sr = 1000
    audio = torch.cat([torch.full((sr,), SPEECH), torch.zeros(int(0.2 * sr))])
    assert trim_trailing_silence(audio, sr) is audio


def test_tail_trim_noop_on_all_silence():
    # Dead renders must pass through so downstream dead-render guards see them.
    sr = 1000
    audio = torch.zeros(2 * sr)
    assert trim_trailing_silence(audio, sr) is audio


def test_tail_trim_preserves_channel_dim():
    sr = 1000
    audio = torch.cat([torch.full((sr,), SPEECH), torch.zeros(2 * sr)]).unsqueeze(0)
    out = trim_trailing_silence(audio, sr)
    assert out.ndim == 2 and out.shape[0] == 1
    assert out.shape[-1] == sr + int(0.3 * sr)


def test_generate_output_tail_is_trimmed(fresh_ref_cache):
    backend = tts_backend.VoxCPM2Backend()
    backend._ensure_loaded = lambda: None
    sr = backend.sample_rate
    tail = np.zeros(2 * sr, dtype=np.float32)
    voiced = np.full(sr, SPEECH, dtype=np.float32)

    backend._model = types.SimpleNamespace(
        generate=lambda **kw: np.concatenate([voiced, tail]))
    out = backend.generate("hello")
    assert out.shape == (1, sr + int(0.3 * sr))


def test_generate_output_without_tail_is_unchanged(fresh_ref_cache):
    backend = tts_backend.VoxCPM2Backend()
    backend._ensure_loaded = lambda: None
    backend._model = types.SimpleNamespace(
        generate=lambda **kw: np.full(4800, SPEECH, dtype=np.float32))
    out = backend.generate("hello")
    assert out.shape == (1, 4800)


def test_generate_voice_design_output_tail_is_trimmed(fresh_ref_cache):
    # The guard covers the voxcpm2 design path too — same engine output.
    backend = tts_backend.VoxCPM2Backend()
    backend._ensure_loaded = lambda: None
    sr = backend.sample_rate
    backend._model = types.SimpleNamespace(
        generate=lambda **kw: np.concatenate([
            np.full(sr, SPEECH, dtype=np.float32),
            np.zeros(sr, dtype=np.float32),
        ]))
    out = backend.generate("hello", description="young female, warm tone")
    assert out.shape == (1, sr + int(0.3 * sr))


def test_in_process_load_disables_cuda_graph_compile(monkeypatch):
    """#3567: voxcpm's default optimize=True compiles with CUDA graph trees,
    whose thread-local state asserts when generation runs on another thread.
    The in-process backend must load the same way as the isolated sidecar."""
    seen = {}

    class _FakeVoxCPM:
        @staticmethod
        def from_pretrained(checkpoint, **kw):
            seen.update(kw)
            return types.SimpleNamespace()

    monkeypatch.setitem(sys.modules, "voxcpm", types.SimpleNamespace(VoxCPM=_FakeVoxCPM))
    monkeypatch.setattr(
        tts_backend.VoxCPM2Backend, "is_available", classmethod(lambda cls: (True, ""))
    )
    monkeypatch.setattr(tts_backend, "_voxcpm_upgrade_hint", lambda: None)

    tts_backend.VoxCPM2Backend()._ensure_loaded()

    assert seen.get("optimize") is False
    assert seen.get("load_denoiser") is False
