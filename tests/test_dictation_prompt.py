"""The dictation vocabulary hint: where it is stored, who may receive it, who must not."""
import asyncio
import inspect
import os
import tempfile

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")
os.environ["OMNIVOICE_STREAM_INTERVAL"] = "0.1"
os.environ["OMNIVOICE_STREAM_SILENCE"] = "0.2"

pytestmark = pytest.mark.usefixtures("asr_model_installed")

HINT = "Okonkwo, Kubernetes, naïve"


def make_engine(accepts_prompt: bool):
    """Build a throwaway recognizer; ``seen`` collects the kwargs of every call."""
    seen: list[dict] = []

    if accepts_prompt:
        def transcribe(self, path, word_timestamps=False, language=None,
                       initial_prompt=None, temperature=None, task=None):
            seen.append({"prompt": initial_prompt, "words": word_timestamps})
            return {"text": "ok", "segments": [], "language": "en"}
    else:
        def transcribe(self, path, word_timestamps=False):
            seen.append({"words": word_timestamps})
            return {"text": "ok", "segments": [], "language": "en"}

    engine_type = type("Engine", (), {"id": "fake-asr", "transcribe": transcribe})
    engine = engine_type()
    engine.seen = seen
    return engine


@pytest.fixture
def saved_hint(monkeypatch):
    from api.routers import dictation as dr

    store: dict = {}
    monkeypatch.setattr(dr.prefs, "get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(dr.prefs, "set_", lambda key, value: store.__setitem__(key, value))
    return lambda value: store.__setitem__(dr.PREF_PROMPT, value)


@pytest.fixture
def audio_stub(monkeypatch):
    from api.routers import capture_ws as cw

    def fake_wav(*_args, **_kwargs):
        handle, path = tempfile.mkstemp(suffix=".wav")
        os.close(handle)
        return path

    monkeypatch.setattr(cw, "_chunks_to_wav", fake_wav)
    monkeypatch.setattr(cw, "_pcm16_to_wav", fake_wav)


def route_capture_to(monkeypatch, engine, record=None):
    from services import asr_backend

    def chooser(**options):
        if record is not None:
            record.append(options)
        return engine

    monkeypatch.setattr(asr_backend, "get_capture_asr_backend", chooser)
    monkeypatch.setattr(asr_backend, "load_active_asr_backend", lambda **_o: engine)


# ---- the shared filter ---------------------------------------------------------

def test_filter_passes_declared_options_and_drops_the_rest():
    from services.asr_backend import transcribe_request_kwargs

    engine = make_engine(True)
    offered = {"initial_prompt": "p", "language": None, "temperature": 0.5, "nonsense": 9}
    assert transcribe_request_kwargs(engine, offered) == {"initial_prompt": "p", "temperature": 0.5}
    assert transcribe_request_kwargs(make_engine(False), offered) == {}


def test_filter_gives_up_quietly_when_signature_is_unreadable():
    from services.asr_backend import transcribe_request_kwargs

    broken = type("Broken", (), {"transcribe": 3})()
    assert transcribe_request_kwargs(broken, {"initial_prompt": "p"}) == {}


def test_real_whisper_family_engines_keep_the_prompt_parameter():
    from services import asr_backend, subprocess_asr

    for cls in (
        asr_backend.FasterWhisperBackend,
        asr_backend.MLXWhisperBackend,
        asr_backend.OpenAICompatASRBackend,
        subprocess_asr.IsolatedFasterWhisperBackend,
    ):
        assert "initial_prompt" in inspect.signature(cls.transcribe).parameters, cls


# ---- stored value ----------------------------------------------------------------

def test_kwargs_empty_until_a_hint_exists_then_follow_it(saved_hint):
    from api.routers import dictation as dr

    capable = make_engine(True)
    assert dr.dictation_transcribe_kwargs(capable) == {}
    saved_hint(f"  {HINT}\n")
    assert dr.dictation_transcribe_kwargs(capable) == {"initial_prompt": HINT}
    assert dr.dictation_transcribe_kwargs(make_engine(False)) == {}


@pytest.mark.parametrize(("stored", "expected"), [
    (["list"], ""),
    (None, ""),
    ("q" * 4321, "q" * 1000),
])
def test_reading_sanitises_whatever_prefs_contains(saved_hint, stored, expected):
    from api.routers import dictation as dr

    saved_hint(stored)
    assert dr.dictation_prompt() == expected
    assert dr.MAX_PROMPT_CHARS == 1000


# ---- live socket helpers ----------------------------------------------------------

def _run_helper(name, **options):
    from api.routers import capture_ws as cw

    return asyncio.run(getattr(cw, name)([b"\0" * 64], **options))


@pytest.mark.parametrize("helper", ["_transcribe_buffer", "_transcribe_buffer_full"])
def test_only_opted_in_socket_calls_carry_the_hint(saved_hint, audio_stub, monkeypatch, helper):
    saved_hint(HINT)
    engine = make_engine(True)
    route_capture_to(monkeypatch, engine)
    _run_helper(helper)
    _run_helper(helper, dictation=True)
    assert engine.seen == [
        {"prompt": None, "words": False},
        {"prompt": HINT, "words": False},
    ]


@pytest.mark.parametrize("helper", ["_transcribe_buffer", "_transcribe_buffer_full"])
def test_opt_in_with_a_prompt_less_engine_just_works(saved_hint, audio_stub, monkeypatch, helper):
    saved_hint(HINT)
    engine = make_engine(False)
    route_capture_to(monkeypatch, engine)
    _run_helper(helper, dictation=True)
    assert engine.seen == [{"words": False}]


def test_rescue_pass_never_gets_the_hint(saved_hint, audio_stub, monkeypatch):
    saved_hint(HINT)
    engine = make_engine(True)
    asked: list[dict] = []
    route_capture_to(monkeypatch, engine, record=asked)
    _run_helper("_transcribe_buffer_full", skip_sherpa=True)
    assert asked == [{"skip_sherpa": True}]
    assert engine.seen == [{"prompt": None, "words": False}]


@pytest.mark.parametrize(("url", "opts_in"), [
    ("/ws/transcribe", True),
    ("/v1/audio/transcriptions/stream", False),
])
def test_each_socket_route_decides_for_itself(monkeypatch, url, opts_in):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import capture_ws as cw

    flags: list = []

    async def partial(_chunks, **options):
        flags.append(options.get("dictation"))
        return "so far"

    async def final(_chunks, **options):
        flags.append(options.get("dictation"))
        return {"text": "all done", "segments": [], "language": "en",
                "duration_s": 1.0, "transcription_time_s": 0.0, "engine": "fake"}

    monkeypatch.setattr(cw, "_transcribe_buffer", partial)
    monkeypatch.setattr(cw, "_transcribe_buffer_full", final)
    app = FastAPI()
    app.include_router(cw.router)
    with TestClient(app, client=("127.0.0.1", 50000)).websocket_connect(f"{url}?sr=16000") as ws:
        ws.send_bytes(bytes(40_000))
        ws.send_text("EOF")
        while ws.receive_json().get("type") != "final":
            pass
    assert flags
    assert set(flags) == {opts_in}


# ---- REST /transcribe ---------------------------------------------------------------

@pytest.fixture
def upload(saved_hint, monkeypatch):
    from fastapi.testclient import TestClient

    from main import app

    saved_hint(HINT)
    engine = make_engine(True)
    route_capture_to(monkeypatch, engine)
    client = TestClient(app, client=("127.0.0.1", 50000))

    def send(mode, dictation):
        form = {"mode": mode, **({"dictation": "true"} if dictation else {})}
        reply = client.post("/transcribe", data=form,
                            files={"audio": ("clip.wav", b"RIFF0000WAVE", "audio/wav")})
        assert reply.status_code == 200, reply.text
        return engine.seen[-1]["prompt"]

    return send


def test_rest_applies_the_hint_only_for_opted_in_non_reference_modes(upload):
    assert upload("fast", True) == HINT
    assert upload("accurate", True) == HINT
    assert upload("fast", False) is None
    assert upload("accurate", False) is None
    assert upload("reference", True) is None
