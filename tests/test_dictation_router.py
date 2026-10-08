"""
Tests for the dictation router (GET /dictation/models, GET/POST /dictation/prefs)
— the exact contract the frontend dictation UI binds to.
"""
import os
import importlib

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    # Import the app first (it pulls in core.prefs for real), then patch the
    # REAL core.prefs get/set_ functions with an in-memory store so the test
    # never touches the real prefs.json (and is immune to reference-swap order).
    from main import app
    store: dict = {}
    # Patch the prefs object the dictation handler actually holds (its own
    # module-level `prefs` reference), so the test is immune to any prior
    # test that swapped sys.modules["core.prefs"].
    from api.routers import dictation as dr
    monkeypatch.setattr(dr.prefs, "get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(dr.prefs, "set_", lambda k, v: store.__setitem__(k, v))
    c = TestClient(app, client=("127.0.0.1", 50000))
    c._store = store
    return c


def test_list_models_shape(client):
    r = client.get("/dictation/models")
    assert r.status_code == 200
    body = r.json()
    assert body["default_model_id"] == "sherpa-whisper-tiny"
    assert len(body["models"]) == 7
    keys = {"id", "repo_id", "label", "tag", "recommended", "size_gb",
            "languages", "kind", "installed"}
    for m in body["models"]:
        assert keys <= set(m), f"missing keys in {m}"
        assert m["tag"] in ("offline", "streaming")
    rec = [m for m in body["models"] if m["recommended"]]
    assert [m["id"] for m in rec] == ["sherpa-whisper-tiny"]


def test_list_models_omits_probe_diagnostic(client, monkeypatch):
    from api.routers import dictation as dr

    private = "Traceback: token=private-value at /home/alice/sherpa.py"
    monkeypatch.setattr(dr.sd, "sherpa_available", lambda: (False, private))
    body = client.get("/dictation/models").json()
    assert body["engine_available"] is False
    assert body["engine_reason"] == (
        "Engine unavailable. Check installation and configuration."
    )
    assert private not in repr(body)


def test_get_prefs_defaults(client):
    r = client.get("/dictation/prefs")
    assert r.status_code == 200
    body = r.json()
    assert body == {"enabled": True, "mode": "toggle",
                    "model_id": "sherpa-whisper-tiny", "prompt": ""}


def test_set_prefs_persists_and_validates(client):
    r = client.post("/dictation/prefs", json={
        "enabled": False, "mode": "hold", "model_id": "sherpa-whisper-tiny"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"enabled": False, "mode": "hold",
                    "model_id": "sherpa-whisper-tiny", "prompt": ""}
    # Persistence: a follow-up GET sees the written values (round-trips through
    # the store the handler actually used — robust to prefs-reference swaps).
    got = client.get("/dictation/prefs").json()
    assert got == {"enabled": False, "mode": "hold",
                   "model_id": "sherpa-whisper-tiny", "prompt": ""}

    # Bad mode rejected.
    assert client.post("/dictation/prefs", json={"mode": "nope"}).status_code == 400
    # Bad model rejected.
    assert client.post("/dictation/prefs", json={"model_id": "nope"}).status_code == 400


def test_prompt_round_trips_trimmed_and_clears(client):
    r = client.post("/dictation/prefs", json={"prompt": "  Kubernetes, gRPC  "})
    assert r.status_code == 200
    assert r.json()["prompt"] == "Kubernetes, gRPC"
    assert client.get("/dictation/prefs").json()["prompt"] == "Kubernetes, gRPC"
    # Omitting the field leaves it alone; an empty string clears it.
    assert client.post("/dictation/prefs", json={"mode": "hold"}).json()["prompt"] == "Kubernetes, gRPC"
    assert client.post("/dictation/prefs", json={"prompt": ""}).json()["prompt"] == ""


def test_prompt_length_is_bounded(client):
    from api.routers import dictation as dr

    too_long = "x" * (dr.MAX_PROMPT_CHARS + 1)
    assert client.post("/dictation/prefs", json={"prompt": too_long}).status_code == 400
    assert dr.PREF_PROMPT not in client._store
    ok = "x" * dr.MAX_PROMPT_CHARS
    assert client.post("/dictation/prefs", json={"prompt": ok}).json()["prompt"] == ok


def test_prompt_only_update_keeps_loaded_capture_engine(client, monkeypatch):
    """The prompt is read per transcription; editing it must not unload the
    capture model (a multi-GB Whisper reload on the next dictation)."""
    from services import asr_backend

    loaded = object()
    monkeypatch.setattr(asr_backend, "_capture_backend", loaded)
    assert client.post("/dictation/prefs", json={"prompt": "gRPC"}).status_code == 200
    assert asr_backend._capture_backend is loaded
    assert client.post("/dictation/prefs", json={"mode": "hold"}).status_code == 200
    assert asr_backend._capture_backend is None


def test_set_prefs_accepts_repo_id_and_normalizes(client):
    r = client.post("/dictation/prefs",
                    json={"model_id": "csukuangfj/sherpa-onnx-whisper-tiny"})
    assert r.status_code == 200
    # Stored as the canonical dictation id, not the repo_id.
    assert r.json()["model_id"] == "sherpa-whisper-tiny"


def test_reset_failure_does_not_persist_new_preferences(monkeypatch):
    services = importlib.import_module("services")
    from api.routers import dictation as dr

    store = {dr.PREF_MODE: "toggle"}
    monkeypatch.setattr(dr.prefs, "get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(dr.prefs, "set_", lambda key, value: store.__setitem__(key, value))

    class _BrokenBackend:
        def __setattr__(self, _name, _value):
            raise RuntimeError("capture service unavailable")

    monkeypatch.setattr(services, "asr_backend", _BrokenBackend())
    with pytest.raises(Exception) as caught:
        dr.set_dictation_prefs(dr.DictationPrefsUpdate(mode="hold"))

    assert getattr(caught.value, "status_code", None) == 503
    assert store == {dr.PREF_MODE: "toggle"}

@pytest.mark.parametrize('missing', [None, {'error': 'asr_model_missing', 'recommended': {'repo_id': 'test/model'}}])
def test_readiness_uses_capture_preflight(client, monkeypatch, missing):
    from services import asr_backend
    calls = []
    def probe(**kwargs):
        calls.append(kwargs)
        return missing
    monkeypatch.setattr(asr_backend, 'asr_model_missing_error', probe)
    response = client.get('/dictation/readiness')
    assert response.status_code == 200
    assert response.json() == {'ready': missing is None, 'missing': missing}
    assert calls == [{'purpose': 'dictation', 'sherpa_model_id': 'sherpa-whisper-tiny'}]


def test_readiness_honors_recorder_model_override(client, monkeypatch):
    from services import asr_backend
    calls = []
    monkeypatch.setattr(asr_backend, 'asr_model_missing_error', lambda **kw: calls.append(kw))
    assert client.get('/dictation/readiness?model_id=sherpa-zipformer-en-20m').status_code == 200
    assert calls == [{'purpose': 'dictation', 'sherpa_model_id': 'sherpa-zipformer-en-20m'}]
