"""Generic OpenAI-compatible ASR backend (#877) — a path to Qwen3-ASR,
FunASR/SenseVoice self-hosted servers, or OpenAI's own Whisper API, today,
without waiting on transformers to ship a direct Qwen3-ASR integration.

settings_store backed by in-memory dicts, OpenAI client faked at the SDK
boundary (no network) — house convention, same as test_llm_providers_router.py:
direct handler calls, no TestClient, so the loopback auth guard isn't in play.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_HAS_OPENAI = __import__("importlib").util.find_spec("openai") is not None
pytestmark = pytest.mark.skipif(not _HAS_OPENAI, reason="openai package not installed")


@pytest.fixture
def ss(monkeypatch):
    """services.settings_store, resolved fresh (no module-level import — see
    asr_mod's docstring for why staleness across sys.modules reimports is a
    real risk in this suite) and patched to in-memory dicts (no SQLite)."""
    from services import settings_store as _ss

    text: dict[str, str] = {}
    secrets: dict[str, str] = {}
    monkeypatch.setattr(_ss, "get_text", lambda k, default=None: text.get(k, default))
    monkeypatch.setattr(_ss, "set_text", lambda k, v: text.__setitem__(k, v))
    monkeypatch.setattr(_ss, "get_secret", lambda n: secrets.get(n))
    monkeypatch.setattr(
        _ss, "set_secret", lambda n, v: secrets.__setitem__(n, v) if v else secrets.pop(n, None)
    )
    monkeypatch.setattr(_ss, "list_secret_names", lambda: list(secrets))
    return _ss


@pytest.fixture
def asr_mod(ss, monkeypatch):
    """services.asr_backend with settings_store in-memory (no SQLite).

    Resolved via importlib.import_module INSIDE the fixture (not a top-level
    `import` in this file) so it's the module object actually live in
    sys.modules at test-run time — other test files in this ~2400-test suite
    pop+reimport shared service modules (services.model_manager,
    services.tts_backend), and a module-level import captured once at file
    COLLECTION time can go stale by the time an individual test in this file
    finally runs, hours of test-order later. A collection-time reference
    calling .set_text() and a fixture-time reference reading via .get_text()
    can silently be two different module objects — the write and the read
    land in different in-memory dicts, and the test fails with no obvious
    cause. Every test below takes `ss` as a fixture (not a module-level
    `from services import settings_store`) for the same reason.
    """
    for var in ("ASR_OPENAI_COMPAT_BASE_URL", "ASR_OPENAI_COMPAT_MODEL", "ASR_OPENAI_COMPAT_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    import importlib
    return importlib.import_module("services.asr_backend")


@pytest.fixture
def settings_mod(asr_mod):
    """api.routers.settings sharing the same monkeypatched settings_store."""
    import importlib
    return importlib.import_module("api.routers.settings")


def _fake_openai_transcribe(monkeypatch, *, verbose_ok=True, response=None, raise_exc=None):
    """Fake openai.OpenAI whose audio.transcriptions.create() either returns
    a canned response or raises. verbose_ok=False simulates a minimal server
    that rejects response_format="verbose_json" on the first call, forcing
    the plain-json fallback."""
    captured_kwargs = []
    calls = []

    class _FakeClient:
        def __init__(self, **kwargs):
            captured_kwargs.append(kwargs)
            self.audio = types.SimpleNamespace(
                transcriptions=types.SimpleNamespace(create=self._create)
            )

        def _create(self, **kw):
            calls.append(kw)
            if raise_exc is not None:
                raise raise_exc
            if kw.get("response_format") == "verbose_json" and not verbose_ok:
                raise RuntimeError("response_format not supported")
            return response

    import openai
    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    return captured_kwargs, calls


# ── is_available() gating ───────────────────────────────────────────────────


def test_unavailable_without_base_url(asr_mod):
    ok, msg = asr_mod.OpenAICompatASRBackend.is_available()
    assert ok is False
    # The hint must name where the endpoint is actually configured — the
    # Settings Models page, ASR tab.
    assert "Settings → Models → ASR" in msg


def test_available_once_base_url_configured(asr_mod, ss):
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    ok, _ = asr_mod.OpenAICompatASRBackend.is_available()
    assert ok is True


def test_insecure_stored_url_cannot_bypass_settings_validation(asr_mod, ss):
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://asr.example/v1")

    ok, reason = asr_mod.OpenAICompatASRBackend.is_available()

    assert ok is False
    assert "require HTTPS" in reason
    with pytest.raises(ValueError, match="require HTTPS"):
        asr_mod.OpenAICompatASRBackend()


# ── response adaptation ─────────────────────────────────────────────────────


def test_transcribe_adapts_verbose_json_segments(asr_mod, ss, monkeypatch, tmp_path):
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")

    class _Seg:
        def model_dump(self):
            return {"text": "hello world", "start": 0.0, "end": 1.5}

    resp = types.SimpleNamespace(segments=[_Seg()], language="en")
    _fake_openai_transcribe(monkeypatch, response=resp)

    audio = tmp_path / "seg.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")  # content is never read by the fake client
    out = asr_mod.OpenAICompatASRBackend().transcribe(str(audio))
    assert out["language"] == "en"
    assert out["segments"] == [{"text": "hello world", "start": 0.0, "end": 1.5, "words": []}]
    assert out["chunks"] == [{"text": "hello world", "timestamp": (0.0, 1.5)}]


def test_transcribe_falls_back_to_plain_text_when_verbose_json_rejected(asr_mod, ss, monkeypatch, tmp_path):
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    resp = types.SimpleNamespace(text="plain text only", segments=None, language=None)
    _captured, calls = _fake_openai_transcribe(monkeypatch, verbose_ok=False, response=resp)

    audio = tmp_path / "seg.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    out = asr_mod.OpenAICompatASRBackend().transcribe(str(audio))
    assert len(calls) == 2  # verbose_json attempt, then the plain fallback
    assert calls[0]["response_format"] == "verbose_json"
    assert calls[1]["response_format"] == "json"
    assert out["segments"] == [{"text": "plain text only", "start": 0.0, "end": None, "words": []}]
    assert out["language"] == "en"  # default when the server doesn't report one


def test_transcribe_network_failure_does_not_leak_raw_exception(asr_mod, ss, monkeypatch, tmp_path):
    """Mirrors the #977 convention: a raw SDK/httpx exception must never reach
    the caller unformatted — only a clean, actionable RuntimeError."""
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    _fake_openai_transcribe(monkeypatch, raise_exc=ConnectionError("connection refused"))

    audio = tmp_path / "seg.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    with pytest.raises(RuntimeError) as ei:
        asr_mod.OpenAICompatASRBackend().transcribe(str(audio))
    msg = str(ei.value)
    assert "localhost:8080" in msg
    assert "ConnectionError" in msg


def test_client_disables_sdk_retries(asr_mod, ss, monkeypatch, tmp_path):
    """max_retries=0 — mirrors llm_skills.resolve_skill_client: a slow/rate-
    limited server retrying inside the SDK would blow past the caller's own
    bounded timeout expectation for a single transcribe call."""
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    resp = types.SimpleNamespace(text="ok", segments=None, language="en")
    captured_kwargs, _ = _fake_openai_transcribe(monkeypatch, response=resp)

    audio = tmp_path / "seg.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    asr_mod.OpenAICompatASRBackend().transcribe(str(audio))
    assert captured_kwargs[0]["max_retries"] == 0
    transport = captured_kwargs[0]["http_client"]
    assert transport.follow_redirects is False
    transport.close()


# ── settings endpoints ───────────────────────────────────────────────────────


def test_get_default_empty(settings_mod):
    st = settings_mod.get_asr_openai_compat()
    assert st == {"base_url": "", "model": "whisper-1", "has_key": False}


def test_put_persists_and_never_echoes_the_key(settings_mod):
    st = settings_mod.set_asr_openai_compat(
        settings_mod._ASROpenAICompatBody(
            base_url="http://localhost:8080/v1/", model="qwen3-asr", api_key="sk-test-123",
        )
    )
    assert st["base_url"] == "http://localhost:8080/v1"  # trailing slash trimmed
    assert st["model"] == "qwen3-asr"
    assert st["has_key"] is True
    assert "sk-test-123" not in str(st)  # the key never round-trips

    st2 = settings_mod.get_asr_openai_compat()
    assert st2 == st


def test_empty_api_key_clears_it(settings_mod):
    settings_mod.set_asr_openai_compat(
        settings_mod._ASROpenAICompatBody(api_key="sk-test-123")
    )
    assert settings_mod.get_asr_openai_compat()["has_key"] is True

    settings_mod.set_asr_openai_compat(settings_mod._ASROpenAICompatBody(api_key=""))
    assert settings_mod.get_asr_openai_compat()["has_key"] is False


def test_none_fields_leave_existing_values_unchanged(settings_mod):
    settings_mod.set_asr_openai_compat(
        settings_mod._ASROpenAICompatBody(base_url="http://localhost:8080/v1", model="qwen3-asr")
    )
    # A save that only touches api_key must not clobber base_url/model.
    settings_mod.set_asr_openai_compat(settings_mod._ASROpenAICompatBody(api_key="sk-abc"))
    st = settings_mod.get_asr_openai_compat()
    assert st["base_url"] == "http://localhost:8080/v1"
    assert st["model"] == "qwen3-asr"
    assert st["has_key"] is True


def test_rejects_a_base_url_without_scheme(settings_mod):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        settings_mod.set_asr_openai_compat(
            settings_mod._ASROpenAICompatBody(base_url="localhost:8080/v1")
        )


def test_rejects_plain_http_for_non_loopback_server(settings_mod):
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="require HTTPS"):
        settings_mod.set_asr_openai_compat(
            settings_mod._ASROpenAICompatBody(base_url="http://asr.example/v1")
        )


def test_accepts_https_for_non_loopback_server(settings_mod):
    state = settings_mod.set_asr_openai_compat(
        settings_mod._ASROpenAICompatBody(base_url="https://asr.example/v1/")
    )
    assert state["base_url"] == "https://asr.example/v1"


def test_registered_in_backend_list(asr_mod):
    assert "openai-compat-asr" in asr_mod._REGISTRY
    assert asr_mod._REGISTRY["openai-compat-asr"] is asr_mod.OpenAICompatASRBackend
    assert "openai-compat-asr" in asr_mod._INSTALL_HINTS


# ── use-time config (no restart needed) ──────────────────────────────────────


def test_engine_reads_fresh_config_per_call(asr_mod, ss):
    """Saving a new base URL/model in Settings must take effect on the very
    next transcribe — the backend is instantiated fresh per call
    (get_active_asr_backend) and reads settings_store in __init__, so no
    backend restart is ever required after a config change."""
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "https://old.example/v1")
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_MODEL_KEY, "old-model")
    first = asr_mod.OpenAICompatASRBackend()
    assert first._base_url == "https://old.example/v1"
    assert first._model == "old-model"

    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "https://new.example/v1")
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_MODEL_KEY, "new-model")
    second = asr_mod.OpenAICompatASRBackend()
    assert second._base_url == "https://new.example/v1"
    assert second._model == "new-model"


# ── connection probe (Test connection button) ────────────────────────────────


def _fake_httpx(monkeypatch, *, status_code=200, json_data=None, text="", raise_exc=None):
    """Fake httpx.Client capturing the probe's URL + headers; GET either
    returns a canned response or raises."""
    import httpx

    captured: dict = {}

    class _Resp:
        def __init__(self):
            self.status_code = status_code
            self.text = text

        def json(self):
            if json_data is None:
                raise ValueError("no json")
            return json_data

    class _Client:
        def __init__(self, **kw):
            captured["client_kwargs"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            if raise_exc is not None:
                raise raise_exc
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    return captured


def test_probe_not_configured(asr_mod):
    out = asr_mod.probe_openai_compat_server()
    assert out["ok"] is False
    assert out["status"] == "not_configured"


def test_probe_rejects_schemeless_url_before_any_network(asr_mod, monkeypatch):
    import httpx

    def _boom(**kw):  # pragma: no cover — must never be constructed
        raise AssertionError("network client constructed for an invalid URL")

    monkeypatch.setattr(httpx, "Client", _boom)
    out = asr_mod.probe_openai_compat_server(base_url="localhost:8080/v1")
    assert out == {**out, "ok": False, "status": "invalid_url"}


def test_probe_rejects_non_loopback_http_before_any_network(asr_mod, monkeypatch):
    import httpx

    def _boom(**kw):  # pragma: no cover — must never be constructed
        raise AssertionError("network client constructed for an insecure URL")

    monkeypatch.setattr(httpx, "Client", _boom)
    out = asr_mod.probe_openai_compat_server(base_url="http://asr.example/v1")
    assert out == {**out, "ok": False, "status": "invalid_url"}


def test_probe_ok_reports_latency_and_model_found(asr_mod, ss, monkeypatch):
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1/")
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_MODEL_KEY, "qwen3-asr")
    captured = _fake_httpx(
        monkeypatch,
        json_data={"data": [{"id": "qwen3-asr"}, {"id": "whisper-1"}]},
    )
    out = asr_mod.probe_openai_compat_server()
    assert out["ok"] is True
    assert out["status"] == "ok"
    assert out["http_status"] == 200
    assert out["models_count"] == 2
    assert out["model_found"] is True
    assert isinstance(out["latency_ms"], float)
    assert captured["url"] == "http://localhost:8080/v1/models"  # trailing / trimmed
    assert captured["client_kwargs"]["follow_redirects"] is False
    # No key configured → the probe must not invent an Authorization header.
    assert "Authorization" not in captured["headers"]


def test_probe_flags_a_model_the_server_does_not_list(asr_mod, ss, monkeypatch):
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_MODEL_KEY, "not-served")
    _fake_httpx(monkeypatch, json_data={"data": [{"id": "whisper-1"}]})
    out = asr_mod.probe_openai_compat_server()
    assert out["ok"] is True
    assert out["model_found"] is False


def test_probe_sends_stored_key_but_never_echoes_it(asr_mod, ss, monkeypatch):
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    ss.set_secret(asr_mod._ASR_OPENAI_COMPAT_SECRET_NAME, "sk-secret-abc")
    captured = _fake_httpx(monkeypatch, json_data={"data": []})
    out = asr_mod.probe_openai_compat_server()
    assert captured["headers"]["Authorization"] == "Bearer sk-secret-abc"
    assert "sk-secret-abc" not in str(out)  # the key never reaches the response


def test_probe_auth_failure_is_classified(asr_mod, ss, monkeypatch):
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    _fake_httpx(monkeypatch, status_code=401)
    out = asr_mod.probe_openai_compat_server()
    assert out["ok"] is False
    assert out["status"] == "auth_failed"
    assert out["http_status"] == 401


def test_probe_treats_missing_models_endpoint_as_reachable(asr_mod, ss, monkeypatch):
    """Minimal transcription-only servers 404 on /models — the server IS
    reachable, so the verdict is a qualified success, not a failure."""
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    _fake_httpx(monkeypatch, status_code=404)
    out = asr_mod.probe_openai_compat_server()
    assert out["ok"] is True
    assert out["status"] == "ok_no_models"


def test_probe_connection_refused_is_unreachable_with_scrubbed_detail(asr_mod, ss, monkeypatch):
    import httpx

    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    _fake_httpx(monkeypatch, raise_exc=httpx.ConnectError("connection refused"))
    out = asr_mod.probe_openai_compat_server()
    assert out["ok"] is False
    assert out["status"] == "unreachable"
    assert "ConnectError" in out["detail"]


def test_probe_timeout_is_classified(asr_mod, ss, monkeypatch):
    import httpx

    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    _fake_httpx(monkeypatch, raise_exc=httpx.ConnectTimeout("timed out"))
    out = asr_mod.probe_openai_compat_server()
    assert out["ok"] is False
    assert out["status"] == "timeout"


def test_probe_never_raises_on_http_error_body(asr_mod, ss, monkeypatch):
    ss.set_text(asr_mod._ASR_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8080/v1")
    _fake_httpx(monkeypatch, status_code=500, text="internal error")
    out = asr_mod.probe_openai_compat_server()
    assert out["ok"] is False
    assert out["status"] == "http_error"
    assert out["http_status"] == 500


def test_route_probes_the_persisted_config(settings_mod, asr_mod, ss, monkeypatch):
    """POST /api/settings/asr-openai-compat/test — the save-first-then-test
    contract (same as /llm-providers/{id}/test): the route reads exactly what
    the PUT persisted, so a saved config change is testable immediately with
    no backend restart."""
    settings_mod.set_asr_openai_compat(
        settings_mod._ASROpenAICompatBody(
            base_url="http://localhost:9999/v1", model="whisper-1", api_key="sk-live-1"
        )
    )
    captured = _fake_httpx(monkeypatch, json_data={"data": [{"id": "whisper-1"}]})
    out = settings_mod.test_asr_openai_compat()
    assert out["ok"] is True
    assert out["model_found"] is True
    assert captured["url"] == "http://localhost:9999/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer sk-live-1"
    assert "sk-live-1" not in str(out)
