"""Confucius4-TTS sidecar unit tests (#590 — finalization).

The upstream synthesis API (``confuciustts.cli.inference.ConfuciusTTS`` →
``generate(text, lang, prompt_wav)`` → tensor, ``model.sample_rate``) is
**validated end-to-end** (2026-07-02, Apple Silicon, CPU): audible speech at
22 050 Hz. Full generation needs ~5 GB of weights, so it can't run in CI — but
the sidecar's *pure* logic (language normalization, tensor→PCM, config-path
resolution, sys.path clone injection, wire framing) and the bootstrap probe are
fully testable here, with the model mocked.

The sidecar is stdlib-only at import time (the model/torch imports are lazy),
so we import it directly without spawning the engine venv.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import os
import struct
from pathlib import Path

import numpy as np
import pytest

_SIDECAR = (
    Path(__file__).resolve().parent.parent
    / "backend" / "engines" / "confucius4" / "main.py"
)


def _load_sidecar():
    spec = importlib.util.spec_from_file_location("confucius4_sidecar_main", _SIDECAR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sc():
    return _load_sidecar()


# ── Language normalization ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("en", "en"), ("EN", "en"), ("zh", "zh"), ("zh-CN", "zh"),
    ("ja", "ja"), ("", "en"), ("auto", "en"), ("AUTO", "en"),
    (None, "en"), ("Vietnamese", "vi"), ("  fr ", "fr"),
])
def test_normalize_language(sc, raw, expected):
    assert sc._normalize_language(raw) == expected


# ── Tensor → PCM base64 ───────────────────────────────────────────────────

def test_pcm_roundtrip_mono(sc):
    import torch
    t = torch.tensor([0.0, 0.5, -0.5, 1.0, -1.0])
    b64, sr, n = sc._tensor_to_pcm_b64(t, 24000)
    pcm = np.frombuffer(base64.b64decode(b64), dtype=np.int16)
    assert (sr, n) == (24000, 5)
    assert pcm.max() == 32767 and pcm.min() == -32767   # full-scale clamp


def test_pcm_clips_out_of_range(sc):
    import torch
    t = torch.tensor([2.0, -3.0])          # beyond [-1, 1]
    b64, _sr, n = sc._tensor_to_pcm_b64(t, 24000)
    pcm = np.frombuffer(base64.b64decode(b64), dtype=np.int16)
    assert n == 2 and pcm.max() == 32767 and pcm.min() == -32767


def test_pcm_downmixes_stereo(sc):
    import torch
    stereo = torch.tensor([[0.2, 0.4], [0.6, 0.8]])   # (2, 2)
    _b64, _sr, n = sc._tensor_to_pcm_b64(stereo, 24000)
    assert n == 2   # mean over channel dim → 2 samples


def test_pcm_accepts_numpy(sc):
    arr = np.array([0.1, -0.1, 0.0], dtype=np.float32)
    _b64, sr, n = sc._tensor_to_pcm_b64(arr, 16000)
    assert (sr, n) == (16000, 3)


# ── Sample rate (confirmed 22 050 Hz by the 2026-07-02 live run) ──────────

def test_sample_rate_constant_is_confirmed_upstream_rate(sc):
    # Upstream config target_sample_rate — regression-pins the live-run value
    # so the pre-validation 24 000 guess can't come back.
    assert sc.CONFUCIUS_SAMPLE_RATE == 22050


def test_sample_rate_lockstep_with_backend_default(sc):
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "backend"))
    from engines.confucius4 import Confucius4Backend
    assert Confucius4Backend._DEFAULT_SAMPLE_RATE == sc.CONFUCIUS_SAMPLE_RATE


# ── Clone sys.path injection (upstream is not pip-installable) ────────────

def test_clone_dir_inserted_at_sys_path_front(sc, monkeypatch):
    import sys as _sys
    monkeypatch.setenv("OMNIVOICE_CONFUCIUS4_TTS_DIR", "/clone")
    monkeypatch.setattr(_sys, "path", ["existing"])
    sc._ensure_clone_on_sys_path()
    assert _sys.path[0] == "/clone"
    sc._ensure_clone_on_sys_path()                      # idempotent — no dup
    assert _sys.path.count("/clone") == 1


def test_no_sys_path_change_without_clone_dir(sc, monkeypatch):
    import sys as _sys
    monkeypatch.delenv("OMNIVOICE_CONFUCIUS4_TTS_DIR", raising=False)
    monkeypatch.setattr(_sys, "path", ["existing"])
    sc._ensure_clone_on_sys_path()
    assert _sys.path == ["existing"]


# ── Config path resolution ────────────────────────────────────────────────

def test_config_path_explicit_override(sc, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_CONFUCIUS4_CONFIG", "/x/custom.yaml")
    assert sc._config_path() == "/x/custom.yaml"


def test_config_path_from_clone_dir(sc, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_CONFUCIUS4_CONFIG", raising=False)
    monkeypatch.setenv("OMNIVOICE_CONFUCIUS4_TTS_DIR", "/clone")
    assert sc._config_path() == os.path.join("/clone", "config", "inference_config.yaml")


# ── Wire framing (length-prefixed JSON) ───────────────────────────────────

def test_send_recv_roundtrip(sc):
    buf = io.BytesIO()
    sc._send(buf, {"op": "ready", "engine": "confucius4-tts"})
    buf.seek(0)
    assert sc._recv(buf) == {"op": "ready", "engine": "confucius4-tts"}


def test_recv_eof_returns_none(sc):
    assert sc._recv(io.BytesIO(b"")) is None


def test_recv_rejects_oversize_frame(sc):
    hdr = struct.pack("!I", sc.MAX_FRAME_BYTES + 1)
    with pytest.raises(IOError):
        sc._recv(io.BytesIO(hdr))


# ── synthesize dispatch (model mocked — no GPU/weights) ───────────────────

def test_synthesize_calls_generate_and_emits_audio(sc, monkeypatch):
    import torch

    class _FakeModel:
        sample_rate = 22050

        def generate(self, **kw):
            _FakeModel.last_kwargs = kw
            return torch.tensor([0.0, 1.0, -1.0])

    monkeypatch.setattr(sc, "_load_model", lambda stdout: _FakeModel())
    out = io.BytesIO()
    sc._handle_synthesize(
        {"text": "hello", "language": "AUTO", "ref_audio": "/ref.wav"}, out,
    )
    out.seek(0)
    frame = sc._recv(out)
    assert frame["op"] == "audio"
    assert frame["sample_rate"] == 22050        # read from model.sample_rate
    assert frame["n_samples"] == 3
    # language normalized, ref audio forwarded as prompt_wav
    assert _FakeModel.last_kwargs == {"text": "hello", "lang": "en", "prompt_wav": "/ref.wav"}


def test_synthesize_rejects_empty_text(sc):
    with pytest.raises(ValueError, match="text"):
        sc._handle_synthesize({"text": ""}, io.BytesIO())


@pytest.mark.parametrize('family,legacy', [(None, False), ('cuda', False), ('xpu', False), ('npu', False), ('mps', False), (None, True), ('cuda', True)])
def test_load_model_matches_routing(sc, monkeypatch, family, legacy):
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing
    from engines.confucius4 import Confucius4Backend

    accelerator = Mock(return_value=SimpleNamespace(type=family) if family else None)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(
        accelerator=SimpleNamespace() if legacy else SimpleNamespace(current_accelerator=accelerator),
        cuda=SimpleNamespace(is_available=lambda: family == 'cuda'),
        device=lambda value: SimpleNamespace(type=value),
    ))
    constructor = Mock()
    monkeypatch.setitem(sys.modules, 'confuciustts.cli.inference', SimpleNamespace(ConfuciusTTS=constructor))
    monkeypatch.setattr(sc, '_model', None)
    monkeypatch.setattr(sc, '_config_path', lambda: 'fixture.yaml')
    monkeypatch.setattr(sc, '_ensure_clone_on_sys_path', lambda: None)
    sc._load_model(io.BytesIO())
    expected = family if family not in (None, 'mps') else 'cpu'
    constructor.assert_called_once_with(config_path='fixture.yaml', device=expected)
    if not legacy:
        accelerator.assert_called_once_with(check_available=True)
    caps = HostCaps(family=family or 'cpu', available_families=(family, 'cpu') if family else ('cpu',))
    assert resolve_routing(Confucius4Backend.gpu_compat, caps)['effective_device'] == expected


@pytest.mark.parametrize("legacy", [False, True])
def test_load_model_falls_back_when_accelerator_probe_raises(sc, monkeypatch, legacy):
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock

    probe = Mock(side_effect=RuntimeError("accelerator driver unavailable"))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        accelerator=SimpleNamespace() if legacy else SimpleNamespace(current_accelerator=probe),
        cuda=SimpleNamespace(is_available=probe),
    ))
    constructor = Mock()
    monkeypatch.setitem(sys.modules, "confuciustts.cli.inference", SimpleNamespace(ConfuciusTTS=constructor))
    monkeypatch.setattr(sc, "_model", None)
    monkeypatch.setattr(sc, "_config_path", lambda: "fixture.yaml")
    monkeypatch.setattr(sc, "_ensure_clone_on_sys_path", lambda: None)
    sc._load_model(io.BytesIO())
    constructor.assert_called_once_with(config_path="fixture.yaml", device="cpu")
    assert probe.call_count == 1


# ── CWD anchoring (#2099) ────────────────────────────────────────────────


def test_chdir_anchors_cwd_to_clone_dir(sc, monkeypatch, tmp_path):
    """Upstream's inference_config.yaml uses './checkpoints/...' relative
    paths; without chdir they resolve against the parent process and the
    sidecar crashes with FileNotFoundError on the weights (#2099).
    """
    monkeypatch.setenv("OMNIVOICE_CONFUCIUS4_TTS_DIR", str(tmp_path))
    original = os.getcwd()
    try:
        sc._chdir_to_clone_if_available()
        assert os.path.realpath(os.getcwd()) == os.path.realpath(str(tmp_path))
    finally:
        os.chdir(original)


def test_chdir_noop_without_clone_dir(sc, monkeypatch):
    """When the user has not configured a clone, cwd must be left alone."""
    monkeypatch.delenv("OMNIVOICE_CONFUCIUS4_TTS_DIR", raising=False)
    original = os.getcwd()
    sc._chdir_to_clone_if_available()
    assert os.getcwd() == original


def test_chdir_noop_when_clone_dir_missing(sc, monkeypatch, tmp_path):
    """A bogus clone path must not raise — the model load will fail loudly
    on its own with a clearer error than 'cwd does not exist'."""
    monkeypatch.setenv("OMNIVOICE_CONFUCIUS4_TTS_DIR", str(tmp_path / "does-not-exist"))
    original = os.getcwd()
    sc._chdir_to_clone_if_available()
    assert os.getcwd() == original


# ── Adapter: ref_audio is required (#2099) ────────────────────────────────


def test_adapter_generate_requires_ref_audio(monkeypatch):
    """The pinned Confucius4 upstream ``generate`` requires ``prompt_wav``;
    surface that constraint at the adapter boundary so the UI sees a clear
    message instead of a stack traceback that mentions ``prompt_wav``
    without saying why.
    """
    import os as _os, sys as _sys
    backend_root = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "backend"
    )
    if backend_root not in _sys.path:
        _sys.path.insert(0, backend_root)
    from engines.confucius4 import Confucius4Backend

    backend = Confucius4Backend()
    with pytest.raises(RuntimeError, match="prompt_wav"):
        backend.generate("anything")


def test_relative_clone_and_config_remain_stable_after_chdir(monkeypatch, tmp_path):
    """Resolve caller-relative settings before the sidecar changes directories."""
    import sys
    sc = _load_sidecar()
    clone = tmp_path / "engine"
    clone.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMNIVOICE_CONFUCIUS4_TTS_DIR", "engine")
    monkeypatch.setenv("OMNIVOICE_CONFUCIUS4_CONFIG", "custom.yaml")
    monkeypatch.setattr(sys, "path", list(sys.path))
    sc._chdir_to_clone_if_available()
    sc._ensure_clone_on_sys_path()
    assert Path(sys.path[0]) == clone
    assert Path(sc._config_path()) == tmp_path / "custom.yaml"
    sc._chdir_to_clone_if_available()
    assert Path.cwd() == clone
    monkeypatch.delenv("OMNIVOICE_CONFUCIUS4_CONFIG")
    assert Path(sc._config_path()) == clone / "config" / "inference_config.yaml"


@pytest.mark.parametrize("name", ["HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "XDG_CACHE_HOME"])
def test_relative_cache_setting_keeps_existing_weights(monkeypatch, tmp_path, name):
    """Changing cwd must not silently select a different model cache."""
    sc = _load_sidecar()
    clone = tmp_path / "engine"
    clone.mkdir()
    cache = tmp_path / "existing-cache"
    cache.mkdir()
    (cache / "weight.bin").write_bytes(b"existing model")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OMNIVOICE_CONFUCIUS4_TTS_DIR", str(clone))
    monkeypatch.setenv(name, "existing-cache")
    sc._chdir_to_clone_if_available()
    assert Path(os.environ[name]).resolve() == cache
    assert (Path(os.environ[name]) / "weight.bin").read_bytes() == b"existing model"


def test_adapter_absolutizes_reference_before_sidecar_chdir(monkeypatch, tmp_path):
    """The parent owns the meaning of a caller-relative reference path."""
    from engines.confucius4 import Confucius4Backend
    # Other suites reload services modules; patch this adapter's actual base.
    base = Confucius4Backend.__bases__[0]
    calls = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(base, "generate", lambda self, text, **kw: calls.append(kw))
    Confucius4Backend().generate("hello", ref_audio="speaker.wav", language="en")
    assert calls[0]["ref_audio"] == str(tmp_path / "speaker.wav")


def test_home_relative_cache_survives_chdir(monkeypatch, tmp_path):
    sc = _load_sidecar()
    clone = tmp_path / 'engine'
    clone.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('OMNIVOICE_CONFUCIUS4_TTS_DIR', str(clone))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('XDG_CACHE_HOME', '~/xdg')
    expected = os.path.expanduser('~/xdg')
    sc._chdir_to_clone_if_available()
    assert Path(os.environ['XDG_CACHE_HOME']).is_absolute()
    assert Path(os.environ['XDG_CACHE_HOME']) == Path(expected)


@pytest.mark.parametrize("ref_audio", [None, "", 42])
def test_synthesize_rejects_invalid_reference_before_loading(sc, monkeypatch, ref_audio):
    from unittest.mock import Mock
    load = Mock()
    monkeypatch.setattr(sc, "_load_model", load)
    with pytest.raises(ValueError, match="reference audio"):
        sc._handle_synthesize({"text": "hello", "ref_audio": ref_audio}, io.BytesIO())
    load.assert_not_called()


def test_home_relative_clone_is_expanded_before_chdir(monkeypatch, tmp_path):
    sc = _load_sidecar()
    clone = tmp_path / "home" / "Confucius4-TTS"
    clone.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(clone.parent))
    monkeypatch.setenv("USERPROFILE", str(clone.parent))
    monkeypatch.setenv("OMNIVOICE_CONFUCIUS4_TTS_DIR", "~/Confucius4-TTS")
    monkeypatch.chdir(tmp_path)
    sc._chdir_to_clone_if_available()
    assert Path.cwd() == clone
    assert Path(sc._config_path()) == clone / "config" / "inference_config.yaml"
    assert os.environ["OMNIVOICE_CONFUCIUS4_TTS_DIR"] == str(clone)
