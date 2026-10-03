"""OpenAICompatASRBackend's client must be wall-clock bounded (#2583).

``transcribe_reference`` reaches this backend silently and best-effort
during profile save (see its docstring in services/asr_backend.py): a user
with the OpenAI-compatible remote engine selected and no network reported
"Cannot save a new voice unless connected to the internet" — the save just
hung. The SDK's own default timeout is 600s, and nothing here overrode it,
so an unreachable server (no network, wrong host) blocked the save for up
to ten minutes instead of letting the caller's except-and-degrade handler
run promptly. These tests pin the timeout default, its env override, and
that ``_client()`` actually passes it through to the HTTP client.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

openai = pytest.importorskip("openai")  # noqa: E402

from services import asr_backend  # noqa: E402
from services.asr_backend import (  # noqa: E402
    OpenAICompatASRBackend,
    resolve_openai_compat_asr_timeout,
)


def test_default_timeout_is_a_sane_positive_number():
    assert resolve_openai_compat_asr_timeout() == 45.0


def test_timeout_is_env_overridable(monkeypatch):
    monkeypatch.setenv("ASR_OPENAI_COMPAT_TIMEOUT", "12.5")
    assert resolve_openai_compat_asr_timeout() == 12.5


def test_malformed_env_timeout_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ASR_OPENAI_COMPAT_TIMEOUT", "not-a-number")
    assert resolve_openai_compat_asr_timeout() == 45.0


@pytest.mark.parametrize("raw", ["0", "-5", "nan", "inf", "-inf"])
def test_non_positive_or_non_finite_env_timeout_falls_back_to_default(monkeypatch, raw):
    # httpx passes the read timeout straight to socket.settimeout(): 0 makes
    # the socket non-blocking instead of disabling the timeout, and
    # negative/NaN/inf values are nonsensical here (#2595 review).
    monkeypatch.setenv("ASR_OPENAI_COMPAT_TIMEOUT", raw)
    assert resolve_openai_compat_asr_timeout() == 45.0


def test_client_passes_a_bounded_timeout_to_the_http_client(monkeypatch):
    """The regression itself: _client() must not fall through to the SDK's
    unbounded (600s) default. Spying on DefaultHttpxClient rather than
    inspecting SDK internals keeps this test stable across openai-python
    versions."""
    monkeypatch.setattr(
        asr_backend, "resolve_openai_compat_asr_base_url", lambda: "http://127.0.0.1:9"
    )
    monkeypatch.setattr(asr_backend, "resolve_openai_compat_asr_model", lambda: "whisper-1")
    monkeypatch.setattr(asr_backend, "resolve_openai_compat_asr_api_key", lambda: None)
    monkeypatch.setenv("ASR_OPENAI_COMPAT_TIMEOUT", "7")

    captured = {}
    real_init = openai.DefaultHttpxClient.__init__

    def _spy_init(self, *args, **kwargs):
        captured.update(kwargs)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(openai.DefaultHttpxClient, "__init__", _spy_init)

    OpenAICompatASRBackend()._client()

    timeout = captured.get("timeout")
    assert timeout is not None, "no timeout was passed — falls back to the SDK's 600s default"
    assert timeout.connect == 5.0
    assert timeout.read == 7.0
