"""Issue #312 — POST /generate must honor the selected TTS engine.

Before the fix, /generate always ran the OmniVoice model via
`services.model_manager.get_model()` and ignored both the Settings engine
selection (POST /engines/select → prefs / OMNIVOICE_TTS_BACKEND) and any
per-request override. These tests prove:

  1. The Settings-selected engine is the one that generates.
  2. An explicit per-request `engine` form field overrides the selection
     (same pattern as /ws/tts's `engine` and /v1/audio/speech's `model`).
  3. Unknown / unavailable engines fail with an actionable 400.
  4. The default path (no selection, no override) still runs OmniVoice —
     backward compatible for existing API consumers.
  5. Engines with `applies_own_mastering=True` skip the broadcast mastering
     chain, mirroring /v1/audio/speech and /ws/tts.

The engine layer is stubbed (no real model loads), matching test_api.py.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import importlib

from unittest.mock import MagicMock, patch

import pytest
import torch


def _tts_mod():
    """Resolve services.tts_backend at RUN time, not import time.

    Pytest imports every test module during collection, but tests/backend/**
    (which runs before tests/test_*.py) stubs modules in sys.modules and
    re-imports the services tree — so a module-level ``from services import
    tts_backend`` binding here can be a stale pre-pollution copy that the app's
    request-time imports no longer see. Resolving through sys.modules inside
    each test keeps the patches and the routes on the same module object.
    """
    return importlib.import_module("services.tts_backend")


def _make_fake_engine(engine_id="fake-engine", *, available=True, own_mastering=False,
                      gpu_compat=("cpu",)):
    """Build a fresh TTSBackend stub class. Fresh per call so the per-process
    instance cache in api.routers.engines can't leak state across tests."""

    _compat = gpu_compat

    class _FakeEngine(_tts_mod().TTSBackend):
        id = engine_id
        display_name = "Fake Engine (test)"
        applies_own_mastering = own_mastering
        gpu_compat = _compat
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            if available:
                return True, "ready"
            return False, "fake engine deliberately unavailable (test)"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append((text, kw))
            return torch.zeros(1, 24000)

    return _FakeEngine


@pytest.fixture()
def client():
    # Function-scoped, NOT context-managed — running the app lifespan here
    # (module-scoped `with TestClient(app)`) bound event_bus queues to this
    # module's event loop and broke teardown when the full suite mixes it
    # with the non-lifespan TestClients every other test file uses
    # (test_api.py pattern). Loopback client addr: required by the
    # router-level require_admin dependency.
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def no_omnivoice_model(monkeypatch):
    """Fail loudly if /generate falls back to the OmniVoice model path."""

    async def _boom():
        raise AssertionError(
            "get_model() was called — /generate ignored the selected engine (#312)"
        )

    import api.routers.generation as gen_mod
    monkeypatch.setattr(gen_mod, "get_model", _boom)


def test_generate_honors_settings_selected_engine(client, monkeypatch, no_omnivoice_model):
    """Engine selected via Settings (env/prefs resolution) runs the request."""
    fake = _make_fake_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-engine", fake)
    # Env var is the top of the same resolution chain prefs.json feeds
    # (active_backend_id: env > prefs > default).
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-engine")

    res = client.post("/generate", data={"text": "Hello engine", "language": "Auto", "seed": "42"})

    assert res.status_code == 200, res.text
    assert res.headers.get("content-type") == "audio/wav"
    assert res.headers.get("x-audio-id")
    assert len(res.content) > 44  # valid WAV payload
    assert len(fake.calls) == 1
    text, kw = fake.calls[0]
    assert text == "Hello engine"
    # "Auto" must reach the adapter as None (engines don't know the sentinel).
    assert kw.get("language") is None


def test_generate_engine_param_overrides_selection(client, monkeypatch, no_omnivoice_model):
    """Explicit per-request `engine` form field wins, like /ws/tts's `engine`."""
    fake = _make_fake_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-engine", fake)
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)

    res = client.post("/generate", data={"text": "Override me", "engine": "fake-engine"})

    assert res.status_code == 200, res.text
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "Override me"


def test_generate_unknown_engine_is_400(client, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    res = client.post("/generate", data={"text": "x", "engine": "not-a-real-engine"})
    assert res.status_code == 400
    assert "Unknown TTS engine" in res.json()["detail"]


def test_generate_unavailable_engine_is_400_with_reason(client, monkeypatch):
    fake = _make_fake_engine(available=False)
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-engine", fake)
    res = client.post("/generate", data={"text": "x", "engine": "fake-engine"})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "not available" in detail
    assert "deliberately unavailable" in detail
    assert not fake.calls


# ── #21 synth-time routing gate ─────────────────────────────────────────────

def _force_host(monkeypatch, family="cpu"):
    from core.device_caps import HostCaps
    avail = (family, "cpu") if family != "cpu" else ("cpu",)
    caps = HostCaps(family=family, available_families=avail)
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: caps)


def test_generate_routing_unavailable_is_400(client, monkeypatch, no_omnivoice_model):
    """A GPU-only engine (no cpu path) on a CPU host → 400 before any synth."""
    _force_host(monkeypatch, "cpu")
    fake = _make_fake_engine(gpu_compat=("cuda",))  # no cpu fallback
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-engine", fake)
    res = client.post("/generate", data={"text": "x", "engine": "fake-engine"})
    assert res.status_code == 400
    assert not fake.calls  # never synthesized


def test_generate_cpu_fallback_emits_routing_headers(client, monkeypatch, no_omnivoice_model):
    """An MPS+CPU engine on a CUDA host → cpu_fallback → 200 + routing headers."""
    _force_host(monkeypatch, "cuda")
    fake = _make_fake_engine(gpu_compat=("mps", "cpu"))  # no CUDA path
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-engine", fake)
    res = client.post("/generate", data={"text": "hi", "engine": "fake-engine"})
    assert res.status_code == 200, res.text
    assert res.headers.get("x-omnivoice-routing") == "cpu_fallback"
    reason = res.headers.get("x-omnivoice-routing-reason")
    assert reason and reason.encode("ascii")  # present + latin-1/ASCII-safe
    assert len(fake.calls) == 1  # synth still ran (fallback, not block)


def test_generate_cpu_only_host_emits_no_routing_header(client, monkeypatch, no_omnivoice_model):
    """A cpu-capable engine on a no-GPU host is benign cpu_only → no header."""
    _force_host(monkeypatch, "cpu")
    fake = _make_fake_engine(gpu_compat=("cpu",))
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-engine", fake)
    res = client.post("/generate", data={"text": "hi", "engine": "fake-engine"})
    assert res.status_code == 200, res.text
    assert "x-omnivoice-routing" not in {k.lower() for k in res.headers}


@pytest.mark.usefixtures("cpu_host")
def test_generate_default_path_still_runs_omnivoice(client, monkeypatch, tmp_path):
    """No selection + no override → the OmniVoice model path, unchanged."""
    # Neutralize any persisted Settings pick so the default resolution applies.
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)

    mock_model = MagicMock()
    mock_model.sampling_rate = 24000
    mock_model.generate.return_value = [torch.zeros(1, 24000)]

    async def _get():
        return mock_model

    import api.routers.generation as gen_mod
    monkeypatch.setattr(gen_mod, "get_model", _get)

    res = client.post("/generate", data={"text": "Default path"})

    assert res.status_code == 200, res.text
    assert mock_model.generate.called
    assert res.headers.get("x-audio-id")


def test_generate_respects_applies_own_mastering(client, monkeypatch, no_omnivoice_model):
    """Studio engines (applies_own_mastering=True) skip apply_mastering;
    regular engines still get the broadcast chain — parity with
    /v1/audio/speech and /ws/tts."""
    audio_dsp = importlib.import_module("services.audio_dsp")  # run-time resolve, see _tts_mod

    mastering = MagicMock(side_effect=lambda t, sample_rate=24000: t)
    monkeypatch.setattr(audio_dsp, "apply_mastering", mastering)

    studio = _make_fake_engine("fake-studio", own_mastering=True)
    plain = _make_fake_engine("fake-plain", own_mastering=False)
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-studio", studio)
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-plain", plain)

    res = client.post("/generate", data={"text": "studio", "engine": "fake-studio"})
    assert res.status_code == 200, res.text
    assert mastering.call_count == 0

    res = client.post("/generate", data={"text": "plain", "engine": "fake-plain"})
    assert res.status_code == 200, res.text
    assert mastering.call_count == 1


@pytest.mark.parametrize("stream", [False, True])
def test_generate_preserves_retryable_engine_contention(client, monkeypatch, no_omnivoice_model, stream):
    import json

    from api.routers import generation

    fake = _make_fake_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-engine", fake)

    def busy(self, text, **kwargs):
        raise generation.GpuPoolBusyError("MLX-Audio is already generating speech.", retry_after=1)

    monkeypatch.setattr(fake, "generate", busy)
    response = client.post("/generate", data={
        "text": "Try again later.", "engine": "fake-engine", "stream": str(stream).lower(),
    })
    if stream:
        assert response.status_code == 200
        event = json.loads(response.text.splitlines()[-1])
        assert event["type"] == "error"
        assert event["retryable"] is True
        assert event["retry_after"] == 1
    else:
        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"
        assert response.headers["x-omnivoice-retryable"] == "true"
