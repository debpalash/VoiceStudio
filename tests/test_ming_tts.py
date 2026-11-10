"""Tests for the Ming-3.0-tts engine (issue #3793).

Ming runs in a dedicated subprocess venv (transformers==4.46.3, torch==2.6.0),
so these tests never import the upstream package or load a model. They cover the
parent-side wiring (registry, gating, language and kwarg arbitration), the
sidecar's pure logic, and the real sidecar process over the stdio protocol with
the model never loaded.
"""
from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

_SIDECAR = Path(__file__).resolve().parent.parent / "backend" / "engines" / "ming_tts" / "main.py"


def _backend_cls():
    from engines.ming_tts import MingTTSBackend
    return MingTTSBackend


def _load_sidecar():
    spec = importlib.util.spec_from_file_location("ming_sidecar_main", _SIDECAR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── registry wiring ────────────────────────────────────────────────────────


def test_registry_contains_ming_tts():
    from services.tts_backend import _INSTALL_HINTS, _REGISTRY, get_backend_class

    cls = _REGISTRY["ming-tts"]
    assert cls.__name__ == "MingTTSBackend"
    assert get_backend_class("ming-tts") is cls
    assert getattr(cls, "_is_subprocess_isolated", False)
    assert "OMNIVOICE_MING_TTS_DIR" in _INSTALL_HINTS["ming-tts"]
    assert "inclusionAI" in _INSTALL_HINTS["ming-tts"]


def test_it_is_never_the_default_engine():
    from services import tts_backend

    assert tts_backend.active_backend_id() != "ming-tts"


def test_hardware_claim_is_cuda_only():
    cls = _backend_cls()
    assert cls.gpu_compat == ("cuda",)
    assert cls().sample_rate == 44100
    assert cls().supported_languages == ["zh", "en", "ja", "ko", "fr", "de", "es"]


# ── is_available() gating ──────────────────────────────────────────────────


def test_no_cuda_host_is_unavailable_with_a_reason(monkeypatch):
    monkeypatch.setattr("engines.ming_tts._host_has_cuda", lambda: False)
    ok, msg = _backend_cls().is_available()
    assert ok is False
    assert "CUDA" in msg and "docs/engines/ming-tts.md" in msg
    assert "mps" not in msg.lower().replace("apple silicon", "")


def test_cuda_host_without_the_venv_says_how_to_install(monkeypatch):
    monkeypatch.setattr("engines.ming_tts._host_has_cuda", lambda: True)
    monkeypatch.setattr("engines.ming_tts.bootstrap.is_ming_tts_installed", lambda: False)
    ok, msg = _backend_cls().is_available()
    assert ok is False
    assert "OMNIVOICE_MING_TTS_DIR" in msg and "docs/engines/ming-tts.md" in msg


def test_cuda_host_with_the_venv_is_available(monkeypatch):
    monkeypatch.setattr("engines.ming_tts._host_has_cuda", lambda: True)
    monkeypatch.setattr("engines.ming_tts.bootstrap.is_ming_tts_installed", lambda: True)
    monkeypatch.setattr("engines.ming_tts.bootstrap.ming_weights_installed", lambda: True)
    ok, _ = _backend_cls().is_available()
    assert ok is True


def test_missing_weights_are_unavailable_with_a_catalogue_hint(monkeypatch):
    monkeypatch.setattr("engines.ming_tts._host_has_cuda", lambda: True)
    monkeypatch.setattr("engines.ming_tts.bootstrap.is_ming_tts_installed", lambda: True)
    monkeypatch.setattr("engines.ming_tts.bootstrap.ming_weights_installed", lambda: False)
    ok, msg = _backend_cls().is_available()
    assert ok is False and "Model Catalogue" in msg


def test_a_failing_host_probe_counts_as_no_cuda(monkeypatch):
    import core.device_caps as caps

    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(caps, "detect_host_caps", boom)
    from engines.ming_tts import _host_has_cuda
    assert _host_has_cuda() is False


def test_one_click_install_is_linux_nvidia_only(monkeypatch):
    from services import sidecar_install as si

    spec = si.get_spec("ming-tts")
    for platform, family, expected in (
        ("linux", "cuda", True), ("win32", "cuda", False),
        ("darwin", "mps", False), ("linux", "cpu", False), ("linux", "rocm", False),
    ):
        monkeypatch.setattr(si.sys, "platform", platform)
        monkeypatch.setattr(si, "_host_family", lambda f=family: f)
        ok, why = si.host_support(spec)
        assert ok is expected, (platform, family)
        assert ok or "NVIDIA" in why


def test_the_install_spec_pins_an_exact_upstream_commit():
    from services import sidecar_install as si

    spec = si.get_spec("ming-tts")
    assert len(spec.source_revision) == 40
    assert spec.source_revision in spec.tarball_url
    assert spec.repo_url == "https://github.com/inclusionAI/Ming-3.0-tts.git"
    assert spec.install_args == ("-r", "{checkout}/requirements.txt")


# ── generate() parent-side arbitration ─────────────────────────────────────


def _capture_generate(monkeypatch):
    captured: dict = {}

    def fake_super_generate(self, text, **kw):
        import torch
        captured["text"] = text
        captured.update(kw)
        return torch.zeros(1, 8)

    cls = _backend_cls()
    sub = next(c for c in cls.__mro__ if c.__name__ == "SubprocessBackend")
    monkeypatch.setattr(sub, "generate", fake_super_generate)
    return captured


def test_clone_kwargs_are_forwarded(monkeypatch):
    captured = _capture_generate(monkeypatch)
    _backend_cls()().generate(
        "bonjour", ref_audio="ref.wav", ref_text="le transcript",
        language="fr", guidance_scale=1.5, num_step=99,
    )
    assert Path(captured["ref_audio"]).is_absolute()
    assert captured["ref_text"] == "le transcript"
    assert captured["language"] == "fr"
    assert captured["guidance_scale"] == 1.5
    assert "num_step" not in captured and "num_steps" not in captured


def test_a_reference_clip_is_required(monkeypatch):
    _capture_generate(monkeypatch)
    with pytest.raises(ValueError, match="reference audio"):
        _backend_cls()().generate("hello", ref_text="orphan")


@pytest.mark.parametrize("language", ["ru", "pt", "ar", "xx"])
def test_unsupported_languages_get_the_standard_language_error(monkeypatch, language):
    captured = _capture_generate(monkeypatch)
    with pytest.raises(ValueError, match="(?i)can't speak|cannot speak|support"):
        _backend_cls()().generate("hi", ref_audio="ref.wav", language=language)
    assert "text" not in captured  # nothing reached the sidecar


# ── sidecar pure logic ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sc():
    return _load_sidecar()


@pytest.mark.parametrize("raw,expected", [
    ("zh", "Chinese"), ("ZH-CN", "Chinese"), ("en", "English"), ("ja", "Japanese"),
    ("ko", "Korean"), ("fr", "French"), ("de", "German"), ("es", "Spanish"),
    ("German", "German"), ("fr_FR", "French"),
    (None, None), ("", None), ("auto", None),
])
def test_sidecar_language_mapping(sc, raw, expected):
    assert sc._normalize_language(raw) == expected


@pytest.mark.parametrize("raw", ["ru", "pt", "yue-x", "Klingon"])
def test_sidecar_rejects_unsupported_languages(sc, raw):
    with pytest.raises(ValueError, match="supports zh, en, ja, ko, fr, de and es"):
        sc._normalize_language(raw)


def test_default_weights_load_at_the_pinned_revision(sc, monkeypatch):
    import huggingface_hub
    from services.hf_revisions import revision_for

    assert sc._DEFAULT_REVISION == revision_for(sc._DEFAULT_MODEL)
    calls = []
    monkeypatch.delenv("OMNIVOICE_MING_TTS_MODEL", raising=False)
    monkeypatch.setattr(
        huggingface_hub, "snapshot_download",
        lambda repo, revision=None, **kw: calls.append((repo, revision, kw)) or "/snap",
    )
    assert sc._model_source() == "/snap"
    assert calls == [(sc._DEFAULT_MODEL, sc._DEFAULT_REVISION, {"local_files_only": True})]


def test_missing_weights_fail_fast_without_downloading(sc, monkeypatch):
    import huggingface_hub

    def _offline(repo, revision=None, local_files_only=False, **kw):
        assert local_files_only, "generation must never download"
        raise OSError("not cached")

    monkeypatch.delenv("OMNIVOICE_MING_TTS_MODEL", raising=False)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", _offline)
    with pytest.raises(RuntimeError, match="Model Catalogue"):
        sc._model_source()


def test_a_mismatched_model_sample_rate_is_refused(sc, monkeypatch):
    class _M:
        sample_rate = 24000

        def synthesize(self, **kw):
            return None

    monkeypatch.setattr(sc, "_load_model", lambda out: _M())
    with pytest.raises(RuntimeError, match="sample_rate"):
        sc._handle_synthesize({"text": "hi", "ref_audio": "/r.wav"}, None)


def test_custom_repo_needs_a_commit_sha_and_a_local_dir_needs_none(sc, monkeypatch, tmp_path):
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **k: pytest.fail("download"))
    monkeypatch.setenv("OMNIVOICE_MING_TTS_MODEL", "someone/other-ming")
    monkeypatch.delenv("OMNIVOICE_MING_TTS_REVISION", raising=False)
    with pytest.raises(RuntimeError, match="OMNIVOICE_MING_TTS_REVISION"):
        sc._model_source()
    monkeypatch.setenv("OMNIVOICE_MING_TTS_REVISION", "main")
    with pytest.raises(RuntimeError):
        sc._model_source()
    monkeypatch.setenv("OMNIVOICE_MING_TTS_MODEL", str(tmp_path))
    assert sc._model_source() == str(tmp_path)


def test_synthesis_calls_upstream_with_the_documented_arguments(sc, monkeypatch):
    torch = pytest.importorskip("torch")
    calls: dict = {}

    class FakeModel:
        sample_rate = 44100

        def synthesize(self, **kw):
            calls.update(kw)
            return torch.tensor([[0.0, 0.5, -0.5]])

    sent: list = []
    monkeypatch.setattr(sc, "_load_model", lambda stdout: FakeModel())
    monkeypatch.setattr(sc, "_send", lambda stream, obj: sent.append(obj))
    sc._handle_synthesize(
        {"text": "hola", "ref_audio": "/r.wav", "ref_text": " transcript ",
         "language": "es", "guidance_scale": 1.7}, None)
    assert calls == {
        "task": "TTS", "text": "hola", "prompt_wav_path": "/r.wav",
        "use_spk_emb": True, "cfg": 1.7, "language_id": "Spanish",
        "prompt_text": "transcript",
    }
    audio = sent[-1]
    assert audio["op"] == "audio" and audio["sample_rate"] == 44100
    assert audio["n_samples"] == 3
    assert len(base64.b64decode(audio["audio_pcm_b64"])) == 6


def test_a_bad_language_fails_before_any_model_load(sc, monkeypatch):
    def must_not_load(stdout):
        raise AssertionError("model load attempted")

    monkeypatch.setattr(sc, "_load_model", must_not_load)
    with pytest.raises(ValueError, match="supports zh"):
        sc._handle_synthesize({"text": "x", "ref_audio": "/r.wav", "language": "ru"}, None)


def test_clone_dir_is_put_on_sys_path_and_cwd(sc, monkeypatch, tmp_path):
    import os

    monkeypatch.setenv("OMNIVOICE_MING_TTS_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    cwd = os.getcwd()
    try:
        sc._prepare_clone()
        assert sys.path[0] == str(tmp_path.resolve()) or sys.path[0] == str(tmp_path)
        assert Path(os.getcwd()).resolve() == tmp_path.resolve()
    finally:
        os.chdir(cwd)


# ── the real sidecar over the stdio protocol (model never loaded) ──────────


def _frame(obj: dict) -> bytes:
    body = json.dumps(obj).encode("utf-8")
    return struct.pack("!I", len(body)) + body


def _frames(raw: bytes) -> list[dict]:
    out, i = [], 0
    while i < len(raw):
        (n,) = struct.unpack("!I", raw[i:i + 4])
        out.append(json.loads(raw[i + 4:i + 4 + n]))
        i += 4 + n
    return out


def test_fake_sidecar_protocol_errors_leave_the_process_serving():
    """Bad requests come back as error frames; the process keeps answering, so
    the user's session (and previously selected engine) is not wedged."""
    requests = b"".join(_frame(m) for m in (
        {"op": "ping"},
        {"op": "synthesize", "text": "hi", "ref_audio": "/r.wav", "language": "ru"},
        {"op": "synthesize", "text": "hi"},
        {"op": "bogus"},
        {"op": "ping"},
        {"op": "shutdown"},
    ))
    proc = subprocess.run(
        [sys.executable, "-I", str(_SIDECAR)], input=requests,
        capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    frames = _frames(proc.stdout)
    assert [f["op"] for f in frames] == ["ready", "pong", "error", "error", "error", "pong"]
    assert frames[0]["engine"] == "ming-tts" and frames[0]["sample_rate"] == 44100
    assert "supports zh, en, ja, ko, fr, de and es" in frames[2]["message"]
    assert "reference clip" in frames[3]["message"]
    assert "unknown op" in frames[4]["message"]


def test_a_load_failure_is_an_error_frame_not_a_crash(tmp_path):
    """On a host with no CUDA (or no upstream clone) the load fails clearly; the
    sidecar reports it and stays alive for the next request."""
    import os

    env = {**os.environ, "OMNIVOICE_MING_TTS_DIR": str(tmp_path)}
    requests = b"".join(_frame(m) for m in (
        {"op": "synthesize", "text": "hi", "ref_audio": "/r.wav", "language": "en"},
        {"op": "ping"},
        {"op": "shutdown"},
    ))
    proc = subprocess.run(
        [sys.executable, "-I", str(_SIDECAR)], input=requests,
        capture_output=True, timeout=120, env=env,
    )
    frames = _frames(proc.stdout)
    ops = [f["op"] for f in frames]
    assert ops[0] == "ready" and "error" in ops and ops[-1] == "pong"
    error = next(f for f in frames if f["op"] == "error")
    assert error["message"]  # names the cause (no CUDA, or the missing clone)


def test_speed_is_applied_to_the_take_not_dropped(monkeypatch):
    import subprocess
    import numpy as np
    import torch
    import services.ffmpeg_utils as fu

    _capture_generate(monkeypatch)
    monkeypatch.setattr(fu, "find_ffmpeg", lambda: "ffmpeg")
    seen = {}

    def run(cmd, input, **kw):
        seen["af"] = cmd[cmd.index("-af") + 1]
        out = np.frombuffer(input, dtype=np.float32)[: len(input) // 4 * 2 // 3]
        return subprocess.CompletedProcess(cmd, 0, out.tobytes(), b"")

    monkeypatch.setattr(subprocess, "run", run)
    audio = _backend_cls()().generate("hello", ref_audio="ref.wav", speed=1.5)
    assert seen["af"] == "atempo=1.500000"
    assert audio.shape == (1, 5)  # the 8-sample take, shortened

    seen.clear()
    assert _backend_cls()().generate("hello", ref_audio="ref.wav", speed=1.0).shape == (1, 8)
    assert not seen  # unchanged speed never spawns ffmpeg


def test_cached_custom_repo_without_a_commit_sha_is_not_available(monkeypatch):
    import huggingface_hub
    from engines.ming_tts.bootstrap import ming_weights_installed

    monkeypatch.setenv("OMNIVOICE_MING_TTS_MODEL", "someone/ming-fork")
    monkeypatch.delenv("OMNIVOICE_MING_TTS_REVISION", raising=False)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **k: "/cache/model.safetensors")
    assert ming_weights_installed() is False
    monkeypatch.setenv("OMNIVOICE_MING_TTS_REVISION", "a" * 40)
    assert ming_weights_installed() is True


def test_a_bad_speed_is_refused_before_synthesis(monkeypatch):
    calls = _capture_generate(monkeypatch)
    for bad in ("fast", -1, 0, float("nan")):
        with pytest.raises(ValueError, match="speed"):
            _backend_cls()().generate("hello", ref_audio="ref.wav", speed=bad)
    assert not calls


def test_the_default_repo_id_set_explicitly_uses_the_pinned_revision(monkeypatch):
    import huggingface_hub
    from engines.ming_tts.bootstrap import ming_weights_installed

    monkeypatch.setenv("OMNIVOICE_MING_TTS_MODEL", "inclusionAI/Ming-3.0-tts-0.5b")
    monkeypatch.delenv("OMNIVOICE_MING_TTS_REVISION", raising=False)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **k: "/cache/model.safetensors")
    assert ming_weights_installed() is True


def test_a_failed_speed_stretch_is_an_error_not_normal_speed_audio(monkeypatch):
    import subprocess
    import services.ffmpeg_utils as fu

    _capture_generate(monkeypatch)
    monkeypatch.setattr(fu, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"boom"))
    with pytest.raises(RuntimeError, match="speed adjustment failed"):
        _backend_cls()().generate("hello", ref_audio="ref.wav", speed=2.0)
    with pytest.raises(ValueError, match="positive"):
        _backend_cls()().generate("hello", ref_audio="ref.wav", speed=-1)
