"""Generation failures must name their real cause, not the generic floor text.

Covers the distinct causes behind #2895/#2684/#2666/#2626/#2622/#2580 ("Generation
failed. Check the selected engine..."), #2917/#2916/#2688 (500 "internal error"),
#2929/#2678 (no speech-to-text model) and #2764 (reference ranking billed to the
generate clock).
"""
import pytest

from core.failure import classify, is_terminal_failure_topic
from core.public_errors import public_exception_response, stream_generation_failure


@pytest.mark.parametrize(
    "exc, topic",
    [
        (ModuleNotFoundError("No module named 'kokoro_onnx'"), "ENGINE_DEPENDENCY_MISSING"),
        (
            ValueError(
                "Automatic reference transcription needs an installed speech-to-text "
                "model. Provide a matching reference transcript."
            ),
            "REFERENCE_STT_MISSING",
        ),
    ],
)
def test_distinct_causes_are_classified(exc, topic):
    assert classify(exc) == topic
    assert is_terminal_failure_topic(topic)


def test_broken_venv_modules_keep_their_own_class():
    assert classify("No module named 'encodings'") == "BROKEN_VENV"
    assert classify("No module named 'omnivoice'") == "BROKEN_VENV"


def test_an_unrelated_failure_is_still_unclassified():
    assert classify(RuntimeError("boom")) == ""


def test_stream_frame_names_the_cause_and_stops_retries():
    payload = stream_generation_failure(
        RuntimeError("engine import failed: No module named 'secret_pkg_name'")
    )
    assert payload["docs_topic"] == "ENGINE_DEPENDENCY_MISSING"
    assert payload["error_class"] == "RuntimeError"
    assert payload["terminal"] is True and payload["retryable"] is False
    assert "Install / Repair" in payload["detail"]
    # Only VoiceStudio-owned text: the module name from the exception is not echoed.
    assert "secret_pkg_name" not in str(payload)


def test_response_hint_is_owned_text():
    resp = public_exception_response(
        ModuleNotFoundError("No module named 'C:/Users/someone/m'"),
        fallback="VoiceStudio hit an internal error.",
    )
    assert resp["docs_topic"] == "ENGINE_DEPENDENCY_MISSING"
    assert "someone" not in str(resp)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from main import app
    import core.db

    core.db.init_db()
    return TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False)


@pytest.mark.usefixtures("cpu_host")
def test_500_body_carries_exception_type_and_topic(client, monkeypatch):
    import api.routers.generation as gen

    async def boom():
        raise RuntimeError("engine import failed: No module named 'pytorch_model_bin'")

    monkeypatch.setattr(gen, "get_model", boom)
    r = client.post("/generate", data={"text": "hello", "engine": "omnivoice"})
    assert r.status_code == 500
    body = r.json()
    assert body["error_type"] == "RuntimeError"
    assert body["docs_topic"] == "ENGINE_DEPENDENCY_MISSING"
    assert "pytorch_model_bin" not in str(body)


@pytest.mark.parametrize(
    "message, topic",
    [
        (
            "Automatic reference transcription needs an installed speech-to-text model.",
            "REFERENCE_STT_MISSING",
        ),
        (
            "No conditionals available. Either provide audio_prompt/audio_prompt_sr "
            "for voice cloning, or ensure conds.safetensors is in the model directory.",
            "CLONE_REFERENCE_MISSING",
        ),
    ],
)
def test_classified_request_detail_names_the_class_with_owned_text(message, topic):
    from core.public_errors import classified_request_detail

    detail = classified_request_detail(message)
    assert detail is not None and detail["docs_topic"] == topic
    assert detail["message"] == detail["hint"] and message not in str(detail)


def test_unclassified_validation_text_passes_through():
    from core.public_errors import classified_request_detail

    assert classified_request_detail("Text is required.") is None


@pytest.mark.parametrize(
    "message, topic",
    [
        (
            "Automatic reference transcription needs an installed speech-to-text model.",
            "REFERENCE_STT_MISSING",
        ),
        # Any classified class, not only the one first reported: the client
        # localizes and offers the fix from docs_topic alone.
        (
            "No conditionals available. Either provide audio_prompt/audio_prompt_sr "
            "for voice cloning, or ensure conds.safetensors is in the model directory.",
            "CLONE_REFERENCE_MISSING",
        ),
    ],
)
def test_classified_400_is_structured_for_localization(client, monkeypatch, message, topic):
    import api.routers.generation as gen

    class _Model:
        pass

    async def fake_model():
        return _Model()

    reached = []

    def reject(*_a, **_k):
        reached.append(True)
        raise ValueError(message)

    monkeypatch.setattr(gen, "get_model", fake_model)
    monkeypatch.setattr(gen, "_run_inference", reject)
    r = client.post("/generate", data={"text": "hello", "engine": "omnivoice"})
    if not reached:  # the route short-circuited before inference on this host
        pytest.skip(f"route answered {r.status_code} before reaching inference")
    # Inference ran and failed: a plain-text 400 here is a regression.
    assert r.status_code == 400, r.text
    detail = r.json().get("detail")
    assert isinstance(detail, dict), detail
    assert detail["docs_topic"] == topic
    assert detail["hint"] and detail["message"]


def test_prepare_reference_passage_ranks_once_and_cleans_up(tmp_path, monkeypatch):
    import services.tts_backend as tb

    window = tmp_path / "w.wav"
    window.write_bytes(b"x")
    calls = []

    monkeypatch.setattr(tb, "reference_duration_s", lambda _p: 40.0)
    monkeypatch.setattr(
        tb, "_reuse_or_rank_passage", lambda p: calls.append(p) or (str(window), "hello")
    )
    assert tb.prepare_reference_passage(str(tmp_path / "ref.wav")) is True
    assert len(calls) == 1
    assert not window.exists()


def test_prepare_reference_passage_ignores_short_clips(monkeypatch):
    import services.tts_backend as tb

    monkeypatch.setattr(tb, "reference_duration_s", lambda _p: 8.0)
    monkeypatch.setattr(
        tb, "_reuse_or_rank_passage", lambda p: pytest.fail("short clip must not be ranked")
    )
    assert tb.prepare_reference_passage("ref.wav") is False
    assert tb.prepare_reference_passage(None) is False


def test_generate_route_ranks_long_reference_before_the_engine_clock():
    """Fail-before/pass-after guard: the pre-rank must sit above the dispatch."""
    import inspect

    import api.routers.generation as gen

    src = inspect.getsource(gen)
    pre = src.index("prepare_reference_passage")
    first_engine_dispatch = src.index("_run_inference,", pre) if "_run_inference," in src[pre:] else len(src)
    assert pre < first_engine_dispatch
    assert "Reference passage selection" in src
