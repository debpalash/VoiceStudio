"""Use-time licence gates at every TTS / dictation / translation entry point (#2689).

Each entry point must refuse an unaccepted gated engine BEFORE it constructs an
engine, loads weights, routes to a worker or touches the network, and must let
an accepted gated engine and a commercial-category engine through. The engines
are tiny fakes; nothing heavy loads. Every gate here is also reachable through
a deeper choke point, so each probe is shaped to reach only its own gate.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytestmark = pytest.mark.model_licence_gate  # real enforcement, see conftest

GATED_ENGINE, GATED_REPO = "indextts2", "IndexTeam/IndexTTS-2.5"  # unknown category
COMMERCIAL_ENGINE = "kittentts"  # Apache-2.0


def _mod(name):
    return importlib.import_module(name)


@pytest.fixture
def ma():
    return _mod("services.model_acceptance")


@pytest.fixture
def tb():
    return _mod("services.tts_backend")


@pytest.fixture
def store(monkeypatch):
    settings_store = _mod("services.settings_store")
    data: dict[str, str] = {}
    monkeypatch.setattr(settings_store, "get_text", lambda key, default=None: data.get(key, default))
    monkeypatch.setattr(settings_store, "set_text", lambda key, value: data.__setitem__(key, value))
    monkeypatch.setattr(settings_store, "get_license_accepted", lambda engine: False)
    return data


def _accept(ma, repo_id):
    ma.accept(repo_id, ma.status(repo_id)["fingerprint"])


def _fake(engine_id):
    class FakeEngine:
        id = engine_id
        built = 0
        sample_rate = 24000

        def __init__(self):
            type(self).built += 1

        @classmethod
        def configured_identity(cls):
            return None

        def built_identity(self):
            return None

        @classmethod
        def is_available(cls):
            return False, "sentinel-not-available"  # first thing after the gate

        def unload(self):
            pass

    return FakeEngine


@pytest.fixture
def engine(monkeypatch, tb):
    """Make ``cls`` the active/registered engine everywhere an id is resolved."""
    saved = (tb._active_instance, tb._active_instance_id, tb._active_mlx_model_key)

    def install(engine_id):
        cls = _fake(engine_id)
        monkeypatch.setattr(tb, "active_backend_id", lambda: engine_id)
        monkeypatch.setattr(tb, "get_backend_class", lambda _id: cls)
        for name in ("api.routers.batch", "api.routers.dub_generate"):
            mod = sys.modules.get(name)
            if mod is not None and hasattr(mod, "active_backend_id"):
                monkeypatch.setattr(mod, "active_backend_id", lambda: engine_id)
        tb._active_instance = tb._active_instance_id = None
        return cls

    yield install
    tb._active_instance, tb._active_instance_id, tb._active_mlx_model_key = saved
    for cls in [c for c in tb._ENGINE_INSTANCES if c.__name__ == "FakeEngine"]:
        tb._ENGINE_INSTANCES.pop(cls, None)
        tb._ENGINE_LAST_USED.pop(cls, None)


# Each probe returns True when the call got PAST the licence gate and False when
# it was refused with ModelLicenceNotAccepted. Any other outcome is a test bug.
def _outcome(fn, ma):
    try:
        fn()
    except ma.ModelLicenceNotAccepted:
        return False
    return True


def _probe_resolve_generation_backend(cls, tb, ma, monkeypatch):
    def run():
        try:
            asyncio.run(tb.resolve_generation_backend())
        except ValueError as exc:  # is_available() sentinel: the gate let it in
            assert "sentinel-not-available" in str(exc)
    return _outcome(run, ma)


def _probe_generation_route(cls, tb, ma, monkeypatch):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    gen = _mod("api.routers.generation")
    monkeypatch.setattr(gen, "_conditioning_refusal", lambda *a, **k: "gate-passed")
    app = FastAPI()
    app.include_router(gen.router)

    @app.exception_handler(ma.ModelLicenceNotAccepted)
    async def _h(request, exc):
        return JSONResponse(status_code=403, content={"detail": exc.detail()})

    resp = TestClient(app).post("/generate", data={"text": "hello", "engine": cls.id})
    if resp.status_code == 403:
        assert resp.json()["detail"]["code"] == "model_licence_required"
        return False
    assert resp.status_code == 422 and resp.json()["detail"] == "gate-passed", resp.text
    return True


def _probe_stream_cached_instance(cls, tb, ma, monkeypatch):
    """Explicit engine override that matches the already-built active instance."""
    stream = _mod("api.routers.tts_stream")
    tb._active_instance, tb._active_instance_id = cls(), cls.id
    cls.built = 0

    def run():
        assert asyncio.run(stream._resolve_stream_backend(cls.id)) is tb._active_instance
    return _outcome(run, ma)


def _probe_stream_omnivoice_override(cls, tb, ma, monkeypatch):
    """The explicit OmniVoice override constructs the class directly."""
    stream = _mod("api.routers.tts_stream")
    monkeypatch.setattr(tb, "OmniVoiceBackend", cls)

    def run():
        assert isinstance(asyncio.run(stream._resolve_stream_backend(cls.id)), cls)
    return _outcome(run, ma)


def _remote_gateway(monkeypatch):
    gw = _mod("services.gpu_gateway")
    calls = []

    async def preflight(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(gw, "decide", lambda op, **k: types.SimpleNamespace(remote=True, label="w"))
    monkeypatch.setattr(gw, "preflight", preflight)
    return calls


def _probe_dub_execution(cls, tb, ma, monkeypatch):
    dub = _mod("api.routers.dub_generate")
    _remote_gateway(monkeypatch)
    return _outcome(lambda: asyncio.run(dub._resolve_dub_execution()), ma)


def _probe_batch_execution(cls, tb, ma, monkeypatch):
    batch = _mod("api.routers.batch")
    _remote_gateway(monkeypatch)
    return _outcome(
        lambda: asyncio.run(batch._resolve_batch_execution({"requires_cloning": False})), ma)


def _probe_audiobook_build_synth(cls, tb, ma, monkeypatch):
    ab = _mod("api.routers.audiobook")
    return _outcome(lambda: ab._build_synth("voice"), ma)


def _probe_audiobook_preview(cls, tb, ma, monkeypatch):
    from fastapi import HTTPException
    ab = _mod("api.routers.audiobook")

    def run():
        # An empty script parses to no chapters: 400 only if the gate let it in.
        with pytest.raises(HTTPException) as err:
            asyncio.run(ab.audiobook_preview(ab.AudiobookPreviewRequest(text="")))
        assert err.value.status_code == 400
    return _outcome(run, ma)


def _probe_audiobook_synthesize(cls, tb, ma, monkeypatch):
    ab = _mod("api.routers.audiobook")
    return _outcome(
        lambda: asyncio.run(ab.audiobook_synthesize(ab.AudiobookRequest(text=""))), ma)


def _probe_longform_render(cls, tb, ma, monkeypatch):
    ab = _mod("api.routers.audiobook")
    return _outcome(
        lambda: asyncio.run(ab.longform_render(ab.LongformRenderRequest())), ma)


PROBES = {
    "resolve_generation_backend": _probe_resolve_generation_backend,
    "generation_route": _probe_generation_route,
    "tts_stream_cached_instance": _probe_stream_cached_instance,
    "tts_stream_omnivoice_override": _probe_stream_omnivoice_override,
    "dub_generate_execution": _probe_dub_execution,
    "batch_execution": _probe_batch_execution,
    "audiobook_build_synth": _probe_audiobook_build_synth,
    "audiobook_preview": _probe_audiobook_preview,
    "audiobook_synthesize": _probe_audiobook_synthesize,
    "longform_render": _probe_longform_render,
}
# Probes that build the engine themselves when the gate lets them through.
_BUILDS_WHEN_PASSED = {"audiobook_build_synth", "tts_stream_omnivoice_override"}


@pytest.mark.parametrize("name", PROBES)
def test_unaccepted_gated_engine_is_refused_before_construction(name, store, engine, tb, ma, monkeypatch):
    cls = engine(GATED_ENGINE)
    assert PROBES[name](cls, tb, ma, monkeypatch) is False
    assert cls.built == 0


@pytest.mark.parametrize("name", PROBES)
def test_accepted_gated_engine_passes_the_gate(name, store, engine, tb, ma, monkeypatch):
    cls = engine(GATED_ENGINE)
    _accept(ma, GATED_REPO)
    assert PROBES[name](cls, tb, ma, monkeypatch) is True
    if name in _BUILDS_WHEN_PASSED:
        assert cls.built == 1


@pytest.mark.parametrize("name", PROBES)
def test_commercial_engine_needs_no_acceptance(name, store, engine, tb, ma, monkeypatch):
    cls = engine(COMMERCIAL_ENGINE)
    assert PROBES[name](cls, tb, ma, monkeypatch) is True


@pytest.mark.parametrize("name", ["tts_stream_cached_instance", "audiobook_build_synth"])
def test_revocation_applies_to_already_built_engines(name, store, engine, tb, ma, monkeypatch):
    cls = engine(GATED_ENGINE)
    _accept(ma, GATED_REPO)
    assert PROBES[name](cls, tb, ma, monkeypatch) is True
    ma.revoke(GATED_REPO)
    assert PROBES[name](cls, tb, ma, monkeypatch) is False


def test_remote_dub_and_batch_are_refused_before_the_worker_is_contacted(store, engine, tb, ma, monkeypatch):
    engine(GATED_ENGINE)
    calls = _remote_gateway(monkeypatch)
    dub, batch = _mod("api.routers.dub_generate"), _mod("api.routers.batch")
    with pytest.raises(ma.ModelLicenceNotAccepted):
        asyncio.run(dub._resolve_dub_execution())
    with pytest.raises(ma.ModelLicenceNotAccepted):
        asyncio.run(batch._resolve_batch_execution({"requires_cloning": False}))
    assert calls == []


# ── Dictation: live WebSocket preflight ─────────────────────────────────────

GATED_SHERPA = "sherpa-whisper-tiny"
COMMERCIAL_SHERPA = "sherpa-zipformer-en-20m"
GATED_SHERPA_REPO = "csukuangfj/sherpa-onnx-whisper-tiny"


def _capture_ws_app(monkeypatch, model_id):
    from fastapi import FastAPI

    cws = _mod("api.routers.capture_ws")
    ab = _mod("services.asr_backend")
    sd = _mod("services.sherpa_dictation")
    spec = sd.get_spec(model_id)
    monkeypatch.setattr(cws, "_select_sherpa_spec", lambda websocket: spec)
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **kw: None)
    monkeypatch.setattr(ab.SherpaDictationBackend, "is_available", classmethod(lambda cls: (True, "")))
    app = FastAPI()
    app.include_router(cws.router)
    return app


def test_dictation_socket_sends_typed_licence_frame_and_closes(store, monkeypatch, ma):
    from fastapi.testclient import TestClient

    ab = _mod("services.asr_backend")
    built = []
    monkeypatch.setattr(ab, "get_sherpa_dictation_backend", lambda *a, **k: built.append(a))
    client = TestClient(_capture_ws_app(monkeypatch, GATED_SHERPA), client=("127.0.0.1", 50000))
    with client.websocket_connect("/ws/transcribe") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error" and frame["kind"] == "model_licence_required"
        assert frame["code"] == "model_licence_required"
        assert [m["repo_id"] for m in frame["models"]] == [GATED_SHERPA_REPO]
    assert built == []


def test_dictation_socket_lets_accepted_and_commercial_models_start(store, monkeypatch, ma):
    from fastapi.testclient import TestClient

    ab = _mod("services.asr_backend")

    class Started(Exception):
        pass

    def stop(*a, **k):
        raise Started

    for model_id, repo in ((GATED_SHERPA, GATED_SHERPA_REPO), (COMMERCIAL_SHERPA, None)):
        if repo:
            _accept(ma, repo)
        # Past the gate the session builds its recognizer: stop it there.
        monkeypatch.setattr(ab, "get_sherpa_dictation_backend", stop)
        monkeypatch.setattr(ab, "capture_lease", stop, raising=False)
        client = TestClient(_capture_ws_app(monkeypatch, model_id), client=("127.0.0.1", 50000))
        try:
            with client.websocket_connect("/ws/transcribe") as ws:
                frame = ws.receive_json()
                assert frame.get("kind") != "model_licence_required"
        except Exception as exc:  # noqa: BLE001 — closing mid-handshake is fine
            assert "model_licence_required" not in str(exc)


def test_dictation_preflight_covers_the_non_sherpa_capture_engine(store, monkeypatch, ma):
    cws, ab = _mod("api.routers.capture_ws"), _mod("services.asr_backend")
    monkeypatch.setattr(ab, "_capture_backend", None)
    monkeypatch.setattr(ab, "_capture_backend_key", None)
    monkeypatch.setattr(ab, "dictation_model_id", lambda: None)
    monkeypatch.setattr(ab, "_capture_prefers_parakeet", lambda: False)
    monkeypatch.setattr(ab.MLXWhisperBackend, "is_available", classmethod(lambda cls: (True, "")))
    detail = cws._dictation_licence_error(None)
    assert detail["code"] == "model_licence_required"
    assert [m["repo_id"] for m in detail["models"]] == ["mlx-community/whisper-large-v3-turbo"]
    _accept(ma, "mlx-community/whisper-large-v3-turbo")
    assert cws._dictation_licence_error(None) is None


# ── NLLB translation ────────────────────────────────────────────────────────

NLLB_REPO = "facebook/nllb-200-distilled-600M"


def _nllb_request():
    schemas = _mod("schemas.requests")
    return schemas.TranslateRequest(
        segments=[schemas.TranslateSegment(id="1", text="hello")],
        target_lang="es", source_lang="en", provider="nllb",
    )


@pytest.fixture
def nllb(monkeypatch):
    dt = _mod("api.routers.dub_translate")
    loads = []
    monkeypatch.setattr(dt, "_nllb_model", None)
    monkeypatch.setattr(dt, "_nllb_tokenizer", None)
    monkeypatch.setattr(dt, "_load_nllb_component", lambda factory: loads.append(factory) or (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(dt, "_nllb_language", lambda code: "spa_Latn")  # needs transformers' tables
    fake = types.ModuleType("transformers")
    fake.AutoTokenizer = fake.AutoModelForSeq2SeqLM = object
    monkeypatch.setitem(sys.modules, "transformers", fake)
    return dt, loads


def test_nllb_refused_before_weights_load_and_typed_error_escapes(store, nllb, ma):
    dt, loads = nllb
    with pytest.raises(ma.ModelLicenceNotAccepted) as err:
        asyncio.run(dt.dub_translate(_nllb_request()))
    assert [m["repo_id"] for m in err.value.detail()["models"]] == [NLLB_REPO]
    assert loads == []


def test_nllb_accepted_reaches_the_weights_load(store, nllb, ma):
    dt, loads = nllb
    _accept(ma, NLLB_REPO)
    asyncio.run(dt.dub_translate(_nllb_request()))
    assert loads  # got past the gate; the stubbed load then stopped it
    ma.revoke(NLLB_REPO)
    loads.clear()
    with pytest.raises(ma.ModelLicenceNotAccepted):  # revocation applies to warm weights too
        asyncio.run(dt.dub_translate(_nllb_request()))
    assert loads == []


# ── OpenAI-compatible speech and dub diarisation (typed error propagation) ───

def test_openai_speech_returns_403_with_the_typed_licence_code(store, engine, tb, ma, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    oc = _mod("api.routers.openai_compat")
    cls = engine(GATED_ENGINE)
    cls.is_available = classmethod(lambda c: (True, ""))
    app = FastAPI()
    app.include_router(oc.router)
    client = TestClient(app, raise_server_exceptions=False)
    body = {"model": GATED_ENGINE, "input": "hello", "voice": "alloy"}

    resp = client.post("/v1/audio/speech", json=body)
    assert resp.status_code == 403 and cls.built == 0
    assert resp.json()["error"]["code"] == "model_licence_required"

    _accept(ma, GATED_REPO)
    assert client.post("/v1/audio/speech", json=body).status_code != 403


def test_dub_diarisation_returns_typed_detail_without_loading_the_pipeline(store, monkeypatch, ma):
    dc = _mod("api.routers.dub_core")
    dr = _mod("services.diarization_runtime")
    loads = []
    monkeypatch.delenv("OMNIVOICE_DIARIZATION_MODEL", raising=False)
    monkeypatch.setattr(dr, "selected_backend", lambda: dr.SORTFORMER)
    monkeypatch.setattr(dc, "get_diarization_pipeline", lambda **kw: loads.append(kw) or ("pipe", None))
    pipe, detail = dc._diarization_pipeline_checked()
    assert pipe is None and detail["code"] == "model_licence_required" and loads == []
    _accept(ma, "audio-cpp/audio.cpp-gguf")
    assert dc._diarization_pipeline_checked() == ("pipe", None) and loads
