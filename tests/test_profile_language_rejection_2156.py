"""#2156: a language the user never picked must not be blamed on the picker.

The reporter was on mlx-audio (Kokoro) with the language picker on "Auto" and
got:

    400 Bad Request: mlx-audio's Kokoro model (mlx-community/Kokoro-82M-bf16)
    doesn't support language='Persian'. … Pick one of those, leave language as
    'Auto', or switch to a multilingual engine …

They had left it on Auto. The UI omits `language` entirely while its picker
reads "Auto" (`frontend/src/hooks/useProfiles.js`: `if (reqLang && reqLang !==
'Auto') formData.append(...)`), and #533 fills that gap from the selected voice
profile. So "Auto" is precisely how 'Persian' got there — the one remedy the
message leads with is the state the user was already in, and nothing in it
points at the voice profile that actually supplied the language.

This completes #1257's line of work rather than reopening it: that issue chose
to name the engine and the way out instead of maintaining per-model language
maps ("a brittle map that goes stale on each engine update"). Same principle
here — say where the language came from, don't enumerate languages.
"""
import importlib
import os
import uuid

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

def _gen_mod():
    """Imported lazily so the end-to-end tests below still run (and fail on
    their assertions, not on a missing symbol) against a tree without the fix."""
    return importlib.import_module("api.routers.generation")


# The real wording from services/tts_backend.py::resolve_kokoro_lang_code.
KOKORO_REFUSAL = (
    "mlx-audio's Kokoro model (mlx-community/Kokoro-82M-bf16) doesn't support "
    "language='Persian'. Kokoro supports: Chinese, English, French, Hindi, "
    "Italian, Japanese, Portuguese, Spanish. Pick one of those, leave language "
    "as 'Auto', or switch to a multilingual engine (e.g. OmniVoice) for other "
    "languages."
)

KOKORO_SUPPORTED = ("Chinese", "English", "French", "Hindi",
                    "Italian", "Japanese", "Portuguese", "Spanish")


def _tts_mod():
    return importlib.import_module("services.tts_backend")


def _make_refusing_engine(engine_id="fake-kokoro-2156"):
    """An engine that refuses unknown languages the way Kokoro really does."""
    class _FakeEngine(_tts_mod().TTSBackend):
        id = engine_id
        display_name = "Fake Kokoro (test)"
        applies_own_mastering = False
        gpu_compat = ("cpu",)
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append((text, kw))
            language = kw.get("language")
            if language and language not in KOKORO_SUPPORTED:
                raise ValueError(
                    f"mlx-audio's Kokoro model (mlx-community/Kokoro-82M-bf16) "
                    f"doesn't support language={language!r}. Kokoro supports: "
                    f"{', '.join(KOKORO_SUPPORTED)}. Pick one of those, leave "
                    f"language as 'Auto', or switch to a multilingual engine "
                    f"(e.g. OmniVoice) for other languages."
                )
            return torch.zeros(1, 24000)

    return _FakeEngine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def _init_db():
    from core.db import init_db

    init_db()


def _profile(language):
    from core.db import db_conn

    pid = f"vp-{uuid.uuid4().hex[:8]}"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, language, kind, created_at) "
            "VALUES (?,?,?,?,?)",
            (pid, f"{language} Narrator", language, "clone", 0.0),
        )
    return pid


def _drop(pid):
    from core.db import db_conn

    with db_conn() as conn:
        conn.execute("DELETE FROM generation_history WHERE profile_id=?", (pid,))
        conn.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))


@pytest.fixture()
def persian_profile(_init_db):
    pid = _profile("Persian")
    yield pid
    _drop(pid)


@pytest.fixture()
def english_profile(_init_db):
    pid = _profile("English")
    yield pid
    _drop(pid)


# ── the reported failure ────────────────────────────────────────────────────


def test_a_profile_supplied_language_names_the_profile_not_the_picker(
    client, monkeypatch, persian_profile
):
    fake = _make_refusing_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    fake.calls.clear()

    # `language` omitted — exactly what the UI sends with the picker on "Auto".
    res = client.post("/generate", data={
        "text": "Salam", "profile_id": persian_profile, "engine": fake.id,
    })

    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    if isinstance(detail, dict):
        assert detail["code"] == "profile_language_rejected"
        assert detail["language"] == "Persian"
        detail = detail["message"]
    # Says where the language actually came from …
    assert "voice profile" in detail.lower()
    assert "Persian" in detail
    # … and that Auto is not an escape from it, since Auto is what filled it in.
    assert "does not override" in detail
    # … and keeps the engine's own capability list, quoted once, not nested.
    assert "Kokoro supports:" in detail
    assert detail.count("Engine's own message:") == 1


def test_the_profile_language_still_reached_the_engine(
    client, monkeypatch, persian_profile
):
    """Guards the premise: this is a profile fill, not the user's choice."""
    fake = _make_refusing_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    fake.calls.clear()

    client.post("/generate", data={
        "text": "Salam", "profile_id": persian_profile, "engine": fake.id,
    })

    assert [kw.get("language") for _t, kw in fake.calls] == ["Persian"]


def test_an_explicitly_requested_language_is_not_blamed_on_the_profile(
    client, monkeypatch, english_profile
):
    """The user really did pick it, so the profile wording would be a lie —
    they get the engine's own message, unchanged."""
    fake = _make_refusing_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    fake.calls.clear()

    res = client.post("/generate", data={
        "text": "Salam", "profile_id": english_profile, "engine": fake.id,
        "language": "Persian",
    })

    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    if isinstance(detail, dict):
        assert detail["code"] == "profile_language_rejected"
        assert detail["language"] == "Persian"
        detail = detail["message"]
    assert "voice profile" not in detail.lower()
    assert "does not override" not in detail
    assert "doesn't support language='Persian'" in detail


def test_a_supported_profile_language_still_drives_generation(
    client, monkeypatch, english_profile
):
    """#533 is untouched: a profile language the engine *can* speak still
    reaches it and still renders."""
    fake = _make_refusing_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    fake.calls.clear()

    res = client.post("/generate", data={
        "text": "Hello", "profile_id": english_profile, "engine": fake.id,
    })

    assert res.status_code == 200, res.text
    assert [kw.get("language") for _t, kw in fake.calls] == ["English"]


def test_a_non_language_failure_under_a_profile_is_untouched(
    client, monkeypatch, persian_profile
):
    """Over-matching guard: having a profile language must not rewrite every
    ValueError as a language problem."""
    class _Boom(_make_refusing_engine("fake-boom-2156")):
        def generate(self, text, **kw):
            raise ValueError("Reference clip is shorter than 3 seconds.")

    monkeypatch.setitem(_tts_mod()._REGISTRY, _Boom.id, _Boom)

    res = client.post("/generate", data={
        "text": "Salam", "profile_id": persian_profile, "engine": _Boom.id,
    })

    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    if isinstance(detail, dict):
        assert detail["code"] == "profile_language_rejected"
        assert detail["language"] == "Persian"
        detail = detail["message"]
    assert "shorter than 3 seconds" in detail
    assert "voice profile" not in detail.lower()


# ── units ───────────────────────────────────────────────────────────────────


def test_the_real_kokoro_wording_is_recognised_as_a_language_rejection():
    # #1257's signature list never matched this — "doesn't support language="
    # contains none of "invalid language code" / "unsupported language …" — so
    # the provenance check would have skipped the engine actually reported.
    assert _gen_mod()._is_language_rejection(KOKORO_REFUSAL)


def test_a_self_describing_rejection_is_not_wrapped_twice():
    """Kokoro already names its engine and its languages. #1257's rewrite must
    leave it alone, or the user reads "Engine's own message:" twice."""
    class _Engine:
        id = "mlx-audio"
        display_name = "MLX Audio"

    original = ValueError(KOKORO_REFUSAL)
    assert _gen_mod()._language_rejection_or(original, _Engine(), "Persian") is original


@pytest.mark.parametrize("reason", [
    "Invalid language code. Supported languages: ar (Arabic), da (Danish)",
    "Unsupported language: bn",
])
def test_generic_rejections_are_still_rewritten_with_engine_context(reason):
    """#1257 keeps working for the messages it was written for."""
    class _Engine:
        id = "mlx-audio"
        display_name = "MLX Audio"

    rewritten = _gen_mod()._language_rejection_or(ValueError(reason), _Engine(), "bn")
    assert rewritten is not ValueError
    assert "MLX Audio" in str(rewritten)


# ── review findings on the first cut of this fix ────────────────────────────


def test_a_generic_rejection_is_quoted_once_not_twice(client, monkeypatch, persian_profile):
    """Greptile P2. `_language_rejection_or` wraps a *generic* rejection with
    the engine remedy before the handler sees it. Building the profile message
    from that wrapper repeated both the engine-switch advice and "Engine's own
    message:" twice — so the profile message is built from the engine's own
    text, not from the wrapper around it."""
    class _Generic(_make_refusing_engine("fake-generic-2156")):
        def generate(self, text, **kw):
            raise ValueError(
                "Invalid language code. Supported languages: ar (Arabic), "
                "da (Danish), de (German)"
            )

    monkeypatch.setitem(_tts_mod()._REGISTRY, _Generic.id, _Generic)

    res = client.post("/generate", data={
        "text": "Salam", "profile_id": persian_profile, "engine": _Generic.id,
    })

    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    if isinstance(detail, dict):
        assert detail["code"] == "profile_language_rejected"
        assert detail["language"] == "Persian"
        detail = detail["message"]
    assert "voice profile" in detail.lower()
    assert detail.count("Engine's own message:") == 1
    assert detail.count("switch engine in Model Catalogue") == 1
    # The engine's own text survives exactly once.
    assert detail.count("Invalid language code") == 1


def _route_remotely(monkeypatch, failure):
    """Send the render to a worker, and fail it there with `failure`."""
    from types import SimpleNamespace

    from services import gpu_gateway

    gen = _gen_mod()
    monkeypatch.setattr(
        gen, "_routing_decision",
        lambda: SimpleNamespace(remote=True, label="gpu-box", reason=""),
    )

    async def _boom(*_a, **_k):
        raise failure

    monkeypatch.setattr(gpu_gateway, "run", _boom)


def test_a_remote_language_refusal_is_a_400_not_a_retryable_503(
    client, monkeypatch, persian_profile
):
    """Greptile P1. A worker's rejection comes home as RemoteJobFailed, which is
    caught ahead of the ValueError branch — so the profile-aware 400 never ran
    and the user was told to retry on this machine, where the same engine
    refuses the same language."""
    from services import gpu_gateway

    fake = _make_refusing_engine("fake-remote-2156")
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    _route_remotely(monkeypatch, gpu_gateway.RemoteJobFailed(
        KOKORO_REFUSAL, worker_label="gpu-box"))

    res = client.post("/generate", data={
        "text": "Salam", "profile_id": persian_profile, "engine": fake.id,
    })

    assert res.status_code == 400, f"{res.status_code}: {res.text}"
    assert res.headers.get("X-OmniVoice-Retryable") != "true"
    detail = res.json()["detail"]
    if isinstance(detail, dict):
        assert detail["code"] == "profile_language_rejected"
        assert detail["language"] == "Persian"
        detail = detail["message"]
    assert "voice profile" in detail.lower()
    assert "Run it on this machine instead" not in detail


def test_a_remote_non_language_failure_is_still_a_retryable_503(
    client, monkeypatch, persian_profile
):
    """Guard on the same branch: only language refusals change class — a real
    worker failure keeps its retryable 503 and its 'run it here' offer."""
    from services import gpu_gateway

    fake = _make_refusing_engine("fake-remote-ok-2156")
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    _route_remotely(monkeypatch, gpu_gateway.RemoteJobFailed(
        "CUDA out of memory on the worker", worker_label="gpu-box"))

    res = client.post("/generate", data={
        "text": "Salam", "profile_id": persian_profile, "engine": fake.id,
    })

    assert res.status_code == 503, f"{res.status_code}: {res.text}"
    assert res.headers.get("X-OmniVoice-Retryable") == "true"


def test_the_wrapper_keeps_the_engines_own_error_reachable():
    class _Engine:
        id = "mlx-audio"
        display_name = "MLX Audio"

    original = ValueError("Invalid language code. Supported languages: ar (Arabic)")
    wrapped = _gen_mod()._language_rejection_or(original, _Engine(), "Persian")
    assert wrapped is not original
    assert _gen_mod()._root_language_error(wrapped) is original
    # An unwrapped error is its own root.
    assert _gen_mod()._root_language_error(original) is original


def _row(**over):
    row = {
        "kind": "clone", "instruct": None, "is_locked": 0,
        "ref_audio_path": None, "locked_audio_path": None, "ref_text": None,
        "seed": None, "vd_states": None, "language": None,
    }
    row.update(over)
    return row


def test_the_resolver_flags_a_profile_filled_language():
    out = _gen_mod()._resolve_profile_conditioning(_row(language="Persian"))
    assert out["language"] == "Persian"
    assert out["language_from_profile"] is True


def test_the_resolver_does_not_flag_an_explicit_request_language():
    out = _gen_mod()._resolve_profile_conditioning(_row(language="Persian"), language="French")
    assert out["language"] == "French"
    assert out["language_from_profile"] is False


def test_the_resolver_does_not_flag_when_the_profile_has_no_language():
    out = _gen_mod()._resolve_profile_conditioning(_row(language=None))
    assert out["language"] is None
    assert out["language_from_profile"] is False


@pytest.mark.parametrize("language", ["Auto", "auto", " AUTO "])
def test_explicit_auto_is_not_filled_from_the_profile(language):
    # Omission preserves the profile default; explicit Auto is a user choice.
    out = _gen_mod()._resolve_profile_conditioning(_row(language="Persian"), language=language)
    assert out["language"] is None
    assert out["language_from_profile"] is False

@pytest.mark.parametrize('remote', [False, True])
def test_streamed_profile_language_refusal_is_terminal(client, monkeypatch, persian_profile, remote):
    import json
    from services import gpu_gateway
    fake = _make_refusing_engine('fake-stream-language-2156')
    monkeypatch.setitem(_tts_mod()._REGISTRY, fake.id, fake)
    if remote:
        _route_remotely(monkeypatch, gpu_gateway.RemoteJobFailed(KOKORO_REFUSAL, worker_label='gpu-box'))
    response = client.post('/generate', data={
        'text': 'Salam', 'profile_id': persian_profile, 'engine': fake.id, 'stream': 'true',
    })
    assert response.status_code == 200, response.text
    frames = [json.loads(line) for line in response.text.splitlines() if line]
    error = next(frame for frame in frames if frame['type'] == 'error')
    assert error['code'] == 'profile_language_rejected'
    assert error['language'] == 'Persian'
    assert error['retryable'] is False
    assert error['terminal'] is True


@pytest.mark.parametrize("language, expected", [("French", "French"), ("Auto", None)])
def test_cross_language_keeps_the_original_reference(language, expected):
    row = _row(language="German", ref_audio_path="german.wav", ref_text="Guten Tag")
    out = _gen_mod()._resolve_profile_conditioning(row, language=language)
    assert out["language"] == expected
    assert out["ref_text"] == "Guten Tag"
    assert out["ref_audio_path"].endswith("german.wav")
    assert out["persist_ref_text"] is False
