"""
The dictation vocabulary prompt (``dictation.prompt``) reaches the capture
engine as Whisper's ``initial_prompt`` — and only engines whose
``transcribe()`` declares it.

Whisper-family capture engines (faster-whisper, MLX, the OpenAI-compatible
backend) mis-hear names, jargon and code-switched terms without a prompt, and
may answer in the wrong script for Chinese. The OpenAI-compatible route
already forwarded ``prompt``; the hotkey dictation paths (live socket and
REST ``/transcribe`` with ``dictation=true``) had no way to supply one.
Sherpa/CTC engines cannot take a prompt and must never be handed one; file
transcription, reference transcripts, the phone-call agent, the public
streaming API and the silent-model rescue stay unbiased.
"""
import asyncio
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

pytestmark = pytest.mark.usefixtures("asr_model_installed")

PROMPT = "VoiceStudio, Breeze-ASR-25, Kubernetes"


class _WhisperLike:
    """Declares ``initial_prompt`` like the Whisper-family backends."""
    id = "whisper-like"

    def __init__(self):
        self.calls = []

    def transcribe(self, _path, *, word_timestamps=True, language=None,
                   initial_prompt=None, temperature=None, task="transcribe"):
        self.calls.append({"word_timestamps": word_timestamps,
                           "initial_prompt": initial_prompt})
        return {"text": "ok", "segments": [{"start": 0.0, "end": 1.0, "text": "ok"}],
                "language": "en"}


class _SherpaLike:
    """No prompt parameter — like sherpa, NeMo, Moonshine, FunASR."""
    id = "sherpa-like"

    def __init__(self):
        self.calls = 0

    def transcribe(self, _path, *, word_timestamps=True):
        self.calls += 1
        return {"text": "ok", "segments": [{"start": 0.0, "end": 1.0, "text": "ok"}],
                "language": "en"}


@pytest.fixture
def prompt_store(monkeypatch):
    from api.routers import dictation as dr

    store: dict = {}
    monkeypatch.setattr(dr.prefs, "get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(dr.prefs, "set_", lambda k, v: store.__setitem__(k, v))
    return store


# ── Option filtering ─────────────────────────────────────────────────────────

def test_request_kwargs_follow_the_backend_signature():
    from services.asr_backend import transcribe_request_kwargs

    opts = {"initial_prompt": PROMPT, "language": None, "bogus": 1}
    assert transcribe_request_kwargs(_WhisperLike(), opts) == {"initial_prompt": PROMPT}
    assert transcribe_request_kwargs(_SherpaLike(), opts) == {}


@pytest.mark.parametrize("module,name", [
    ("services.asr_backend", "FasterWhisperBackend"),
    ("services.asr_backend", "MLXWhisperBackend"),
    ("services.asr_backend", "OpenAICompatASRBackend"),
    ("services.subprocess_asr", "IsolatedFasterWhisperBackend"),
])
def test_engines_named_in_the_settings_copy_declare_initial_prompt(module, name):
    """Settings → Dictation shortcut tells users these engines use the hint.
    The filter matches on the signature, so if one of them stops declaring
    ``initial_prompt`` (e.g. collapses to ``**kwargs``) the hint would be
    dropped silently while every stub-based test above stays green."""
    import importlib
    import inspect

    cls = getattr(importlib.import_module(module), name)
    assert "initial_prompt" in inspect.signature(cls.transcribe).parameters


def test_prompt_kwargs_empty_until_a_prompt_is_saved(prompt_store):
    from api.routers import dictation as dr

    assert dr.dictation_transcribe_kwargs(_WhisperLike()) == {}
    prompt_store[dr.PREF_PROMPT] = f"  {PROMPT}  "
    assert dr.dictation_transcribe_kwargs(_WhisperLike()) == {"initial_prompt": PROMPT}
    assert dr.dictation_transcribe_kwargs(_SherpaLike()) == {}


def test_non_string_pref_is_ignored(prompt_store):
    from api.routers import dictation as dr

    prompt_store[dr.PREF_PROMPT] = ["not", "a", "string"]
    assert dr.dictation_prompt() == ""


def test_hand_edited_oversized_pref_is_bounded_on_read(prompt_store):
    """The write path caps the prompt; a hand-edited prefs.json must not push
    an engine past its own limit (the isolated sidecar rejects >4096 chars)."""
    from api.routers import dictation as dr

    prompt_store[dr.PREF_PROMPT] = "x" * 5000
    assert len(dr.dictation_prompt()) == dr.MAX_PROMPT_CHARS


# ── Live dictation socket ────────────────────────────────────────────────────

@pytest.fixture
def inline_ws(monkeypatch, tmp_path):
    from api.routers import capture_ws as cw

    wav = tmp_path / "buf.wav"
    wav.write_bytes(b"placeholder")

    async def run_inline(_pool, fn, **_kw):
        return fn()

    monkeypatch.setattr(cw, "_pcm16_to_wav", lambda _pcm, _sr: str(wav))
    monkeypatch.setattr("services.asr_backend.run_transcribe_guarded", run_inline)
    return cw


@pytest.mark.parametrize("final", [False, True])
def test_socket_passes_prompt_to_whisper_engines(monkeypatch, prompt_store, inline_ws, final):
    from api.routers import dictation as dr

    prompt_store[dr.PREF_PROMPT] = PROMPT
    backend = _WhisperLike()
    monkeypatch.setattr("services.asr_backend.get_capture_asr_backend", lambda **_k: backend)
    run = inline_ws._transcribe_buffer_full if final else inline_ws._transcribe_buffer
    asyncio.run(run([b"\x00" * 4000], pcm_sr=16000, dictation=True))
    assert backend.calls == [{"word_timestamps": False, "initial_prompt": PROMPT}]


@pytest.mark.parametrize("final", [False, True])
def test_socket_helpers_stay_unprompted_by_default(monkeypatch, prompt_store, inline_ws, final):
    """The helpers are shared: the phone-call agent transcribes through
    ``_transcribe_buffer`` too, and must not pick up dictation vocabulary."""
    from api.routers import dictation as dr

    prompt_store[dr.PREF_PROMPT] = PROMPT
    backend = _WhisperLike()
    monkeypatch.setattr("services.asr_backend.get_capture_asr_backend", lambda **_k: backend)
    run = inline_ws._transcribe_buffer_full if final else inline_ws._transcribe_buffer
    asyncio.run(run([b"\x00" * 4000], pcm_sr=16000))
    assert backend.calls == [{"word_timestamps": False, "initial_prompt": None}]


@pytest.mark.parametrize("final", [False, True])
def test_socket_never_hands_prompt_to_sherpa(monkeypatch, prompt_store, inline_ws, final):
    from api.routers import dictation as dr

    prompt_store[dr.PREF_PROMPT] = PROMPT
    backend = _SherpaLike()
    monkeypatch.setattr("services.asr_backend.get_capture_asr_backend", lambda **_k: backend)
    run = inline_ws._transcribe_buffer_full if final else inline_ws._transcribe_buffer
    asyncio.run(run([b"\x00" * 4000], pcm_sr=16000, dictation=True))  # TypeError if handed a prompt
    assert backend.calls == 1


@pytest.mark.parametrize("path,expected", [
    ("/ws/transcribe?pcm=1&sr=16000", True),
    # The public streaming API shares the handler but is not dictation.
    ("/v1/audio/transcriptions/stream?pcm=1&sr=16000", False),
])
def test_only_the_dictation_socket_opts_in(monkeypatch, path, expected):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import capture_ws as cw

    seen = []

    async def fake_partial(_chunks, **kwargs):
        seen.append(kwargs.get("dictation", False))
        return ""

    async def fake_full(_chunks, **kwargs):
        seen.append(kwargs.get("dictation", False))
        return {"text": "ok", "segments": [], "language": "en", "engine": "stub"}

    monkeypatch.setattr(cw, "_transcribe_buffer", fake_partial)
    monkeypatch.setattr(cw, "_transcribe_buffer_full", fake_full)
    # Take the buffered (non-sherpa) path regardless of any dictation model a
    # previous test left in prefs.
    monkeypatch.setattr(cw, "_select_sherpa_spec", lambda _ws: None)
    app = FastAPI()
    app.include_router(cw.router)
    client = TestClient(app, client=("127.0.0.1", 50000))

    with client.websocket_connect(path) as websocket:
        websocket.send_bytes(b"\x00" * 5000)
        websocket.send_json({"type": "input_audio.end"})
        for _ in range(10):
            if websocket.receive_json().get("type") == "final":
                break
        else:
            pytest.fail("no final frame")

    assert seen and set(seen) == {expected}


def test_silent_model_rescue_decodes_without_prompt(monkeypatch, prompt_store, inline_ws):
    """The rescue's text is the evidence for demoting a sherpa model; a
    prompted Whisper can echo the prompt on noise and fake that evidence."""
    from api.routers import dictation as dr

    prompt_store[dr.PREF_PROMPT] = PROMPT
    backend = _WhisperLike()
    monkeypatch.setattr("services.asr_backend.get_capture_asr_backend", lambda **_k: backend)
    asyncio.run(inline_ws._transcribe_buffer_full([b"\x00" * 4000], pcm_sr=16000, skip_sherpa=True))
    assert backend.calls == [{"word_timestamps": False, "initial_prompt": None}]


# ── REST /transcribe ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,dictation,expected", [
    ("fast", "true", PROMPT),
    ("accurate", "true", PROMPT),
    # File transcription and MCP/CLI callers don't opt in: never biased.
    ("fast", None, None),
    ("accurate", None, None),
    # A voice-clone reference transcript must not be biased by dictation terms.
    ("reference", "true", None),
])
def test_rest_transcribe_applies_prompt_only_for_dictation(
    monkeypatch, prompt_store, mode, dictation, expected,
):
    from fastapi.testclient import TestClient
    from api.routers import dictation as dr

    prompt_store[dr.PREF_PROMPT] = PROMPT
    backend = _WhisperLike()
    for name in ("get_capture_asr_backend", "get_active_asr_backend", "load_active_asr_backend"):
        monkeypatch.setattr(f"services.asr_backend.{name}", lambda **_k: backend)

    from main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    data = {"mode": mode}
    if dictation is not None:
        data["dictation"] = dictation
    r = client.post(
        "/transcribe",
        files={"audio": ("a.wav", b"\x00" * 32000, "audio/wav")},
        data=data,
    )
    assert r.status_code == 200, r.text
    assert [c["initial_prompt"] for c in backend.calls] == [expected]
