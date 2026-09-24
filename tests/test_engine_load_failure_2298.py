"""A weight download that FAILS must not surface as "internal error" (#2298).

Field report (#2298): KittenTTS on Windows, first generate, HTTP 500 —
"VoiceStudio hit an internal error; check the backend log for details."
The reporter had a Hugging Face mirror configured and it was unreachable, so
the engine's lazy first-use weight fetch raised a connectivity error.

`/generate` already warms the adapter under the model-LOAD budget before the
generate clock starts (#1033), precisely because a cold engine downloads its
weights inside `generate()`. But that warm-up only translated
``TimeoutError``/``ModelLoadTimeout`` into an actionable 503. Every other
load failure — the download refused, DNS gone, the mirror down, the file
truncated, the cache corrupt — escaped the route uncaught and was rendered by
the global 500 handler as "VoiceStudio hit an internal error", which names
neither the engine nor the fact that a model download was what failed.

So the two halves of the same event disagreed: a download that STALLS is
explained, a download that FAILS is an internal error. These tests hold both
halves to the same standard, on both routes that warm an adapter.

Engine-stub pattern from tests/test_load_budget_split_1033.py.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import importlib

import pytest
import torch

# The wording huggingface_hub/transformers produce when the configured
# endpoint cannot be reached. Verbatim shape, so the classifier is exercised
# the way the field hits it.
MIRROR_UNREACHABLE = (
    "We couldn't connect to 'https://hf-mirror.com' to load the files, and "
    "they couldn't be found in the cached files. Check your internet "
    "connection or see how to run the library in offline mode."
)


def _tts_mod():
    return importlib.import_module("services.tts_backend")


def _make_failing_engine(engine_id, error):
    """An engine whose lazy weight load raises — the cold-start failure."""

    class _FailsToLoad(_tts_mod().TTSBackend):
        id = engine_id
        display_name = "Failing-Load Engine (test)"
        load_calls: list = []

        def __init__(self):
            self._loaded = False

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            # The PACKAGE imports fine; only the weights are missing. This is
            # exactly KittenTTS's shape — `import kittentts` succeeding says
            # nothing about whether the checkpoint has been fetched.
            return True, "ready"

        def _ensure_loaded(self):
            type(self).load_calls.append(1)
            raise error

        def generate(self, text, **kw) -> torch.Tensor:  # pragma: no cover
            self._ensure_loaded()
            return torch.zeros(1, 2400)

    return _FailsToLoad


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False)


@pytest.fixture()
def mirror_configured(monkeypatch):
    """A non-default HF endpoint, which is what makes the mirror class exist."""
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")


def _register(monkeypatch, engine_id, error):
    fake_cls = _make_failing_engine(engine_id, error)
    monkeypatch.setitem(_tts_mod()._REGISTRY, engine_id, fake_cls)
    return fake_cls


def test_generate_names_the_failed_model_load_not_an_internal_error(
    client, monkeypatch, mirror_configured
):
    """The reported request. The reply must say a model load failed, and for
    which engine — an "internal error" tells the user nothing to act on."""
    _register(monkeypatch, "mirror-down-engine", OSError(MIRROR_UNREACHABLE))

    res = client.post(
        "/generate", data={"text": "Hello.", "engine": "mirror-down-engine"}
    )

    assert res.status_code == 503, res.text
    assert res.headers["Retry-After"] == "30"
    assert res.headers["X-OmniVoice-Retryable"] == "true"
    detail = res.json()["detail"]
    assert "hit an internal error" not in detail
    assert "mirror-down-engine" in detail
    assert "model" in detail.lower()


def test_generate_keeps_the_mirror_remedy_that_the_500_already_carried(
    client, monkeypatch, mirror_configured
):
    """The one useful thing the old 500 did carry was the mirror hint. The
    classified reply must not lose it while gaining the rest."""
    _register(monkeypatch, "mirror-hint-engine", OSError(MIRROR_UNREACHABLE))

    res = client.post(
        "/generate", data={"text": "Hello.", "engine": "mirror-hint-engine"}
    )

    assert res.status_code == 503, res.text
    assert res.headers["Retry-After"] == "30"
    assert res.headers["X-OmniVoice-Retryable"] == "true"
    body = res.json()
    assert "Hugging Face mirror" in body["detail"]
    assert body.get("docs_topic") == "HF_MIRROR_UNREACHABLE"


def test_generate_explains_a_load_failure_with_no_known_class(client, monkeypatch):
    """No mirror, no recognised signature — still not an "internal error".
    The floor is naming the engine and that its model failed to load."""
    _register(monkeypatch, "odd-failure-engine", RuntimeError("something odd"))

    res = client.post(
        "/generate", data={"text": "Hello.", "engine": "odd-failure-engine"}
    )

    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert "hit an internal error" not in detail
    assert "odd-failure-engine" in detail
    # Never the private diagnostic (Constitution I).
    assert "something odd" not in detail


def test_speech_route_gets_the_same_treatment(client, monkeypatch, mirror_configured):
    """The OpenAI-compatible route warms the same adapter through the same
    helper; the twin catch must not drift from its sibling."""
    _register(monkeypatch, "speech-mirror-engine", OSError(MIRROR_UNREACHABLE))

    res = client.post(
        "/v1/audio/speech",
        json={
            "model": "speech-mirror-engine",
            "input": "Hello.",
            "response_format": "wav",
        },
    )

    assert res.status_code == 503, res.text
    assert res.headers["Retry-After"] == "30"
    assert res.headers["X-OmniVoice-Retryable"] == "true"
    assert "hit an internal error" not in res.text


def test_a_stalled_download_still_reports_the_load_budget(client, monkeypatch):
    """The sibling case must keep its existing, more specific wording — this
    change must not swallow the timeout class into the generic load error."""
    import services.model_manager as mm

    def _slow(self):
        raise TimeoutError("load budget exceeded")

    fake_cls = _register(monkeypatch, "stalled-engine", TimeoutError("x"))
    monkeypatch.setattr(mm, "_model_load_timeout", lambda: 0.2)

    res = client.post("/generate", data={"text": "Hi.", "engine": "stalled-engine"})

    assert res.status_code == 503, res.text
    assert "model-load budget" in res.json()["detail"]
    assert res.headers["Retry-After"] == "30"
    assert res.headers["X-OmniVoice-Retryable"] == "true"


def test_speech_shutdown_preserves_graceful_response(client, monkeypatch):
    """Shutdown is not a failed download or a reportable model crash."""
    from services.model_manager import ModelLoadInterruptedByShutdown

    _register(monkeypatch, "shutdown-engine", ModelLoadInterruptedByShutdown("stopping"))
    res = client.post(
        "/v1/audio/speech",
        json={"model": "shutdown-engine", "input": "Hi.", "response_format": "wav"},
    )
    assert res.status_code == 503, res.text
    assert "[shutting_down]" in res.json()["detail"]
    assert res.headers["Retry-After"] == "5"
