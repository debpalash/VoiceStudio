"""
A segment timing the engine could not determine arrives as ``end: None``, and
the REST `/transcribe` response builder used to raise on it.

`_sherpa_result()` (services/asr_backend.py) sets ``duration = None`` when it
cannot derive one from the sample rate, and `OpenAICompatASRBackend`
`_adapt_response()` emits ``end: None`` for every plain-text (`json`/`text`)
response from a server that rejects `verbose_json`. Both reach this endpoint —
sherpa is the first capture engine and the OpenAI-compatible backend is
selectable as the active one.

`round(s.get("end", 0), 2)` does not defend against that: ``.get`` returns the
stored ``None`` rather than the default, because the key is present. The route
answered 500 for a transcript that was otherwise fine, which is the server-side
half of #1904.
"""
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

pytestmark = pytest.mark.usefixtures("asr_model_installed")


class _UntimedBackend:
    """What sherpa and the OpenAI-compatible server both hand back when no
    timing is available: text, a start of 0.0, and an honest null end."""
    id = "untimed"

    def transcribe(self, _path, **_kw):
        return {
            "text": "the meeting is at three",
            "segments": [
                {"start": 0.0, "end": None, "text": "the meeting is at three"},
            ],
            "language": "en",
        }


class _PartlyTimedBackend:
    """One timed segment and one the engine gave up on — the duration must come
    from the half that is known, not from the null."""
    id = "partly-timed"

    def transcribe(self, _path, **_kw):
        return {
            "text": "first second",
            "segments": [
                {"start": 0.0, "end": 1.25, "text": "first"},
                {"start": 1.25, "end": None, "text": "second"},
            ],
            "language": "en",
        }


def _client(monkeypatch, backend):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        "services.asr_backend.get_capture_asr_backend", lambda **_k: backend())
    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend", lambda **_k: backend())
    monkeypatch.setattr(
        "services.asr_backend.load_active_asr_backend", lambda **_k: backend())

    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


def _post(client, **data):
    return client.post(
        "/transcribe",
        files={"audio": ("a.wav", b"\x00" * 32000, "audio/wav")},
        data=data,
    )


# The route picks its engine from a `mode` form field, not an `accurate`
# flag, so parametrising on `accurate` sent a field the route ignores and ran
# the default fast path twice. Both engines must pass a null end through.
@pytest.mark.parametrize("mode", ["fast", "accurate"])
def test_null_end_is_passed_through_not_rounded(monkeypatch, mode):
    client = _client(monkeypatch, _UntimedBackend)
    r = _post(client, mode=mode)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segments"][0]["end"] is None
    assert body["segments"][0]["start"] == 0.0
    assert body["segments"][0]["text"] == "the meeting is at three"
    # Nothing known to measure, so the duration stays 0 rather than becoming null.
    assert body["duration_s"] == 0.0


def test_duration_comes_from_the_timed_segments(monkeypatch):
    client = _client(monkeypatch, _PartlyTimedBackend)
    r = _post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["end"] for s in body["segments"]] == [1.25, None]
    assert body["duration_s"] == 1.25


@pytest.mark.parametrize("mode", ["fast", "accurate"])
@pytest.mark.parametrize("engine", ["sherpa", "moonshine"])
def test_clip_duration_segments_are_not_recognition_timestamps(monkeypatch, mode, engine):
    import sys
    from types import SimpleNamespace
    from services import asr_backend as asr

    if engine == "sherpa":
        class Backend:
            id = "sherpa"

            def transcribe(self, _path, **_kwargs):
                return asr._sherpa_result("the meeting is at three", [0] * 16000, 16000)
    else:
        monkeypatch.setitem(sys.modules, "moonshine_onnx", SimpleNamespace(
            transcribe=lambda *_a, **_k: "the meeting is at three",
        ))
        import soundfile
        monkeypatch.setattr(soundfile, "info", lambda _path: SimpleNamespace(duration=1.0))
        Backend = asr.MoonshineASRBackend

    response = _post(_client(monkeypatch, Backend), mode=mode)
    assert response.status_code == 200, response.text
    body = response.json()
    # Keep the segment shape used by dubbing; a clip's duration is not an ASR cue.
    assert body["segments"] == [{"start": 0.0, "end": 1.0, "text": "the meeting is at three"}]
    assert body["timestamps_available"] is False


@pytest.mark.parametrize("mode", ["fast", "accurate"])
@pytest.mark.parametrize(
    "timing,provenance,expected",
    [
        ({"start": 0.25, "end": 1.25}, None, True),
        ({"start": 0.25, "end": 1.25}, False, False),
        ({"start": 0.25, "end": None}, None, False),
        ({"end": 1.25}, None, False),
        ({"start": True, "end": 1.25}, None, False),
        ({"start": 0.001, "end": 0.002}, None, False),
    ],
)
def test_api_preserves_only_valid_recognition_timing_provenance(
    monkeypatch, mode, timing, provenance, expected,
):
    class Backend:
        id = "faster-whisper"

        def transcribe(self, _path, **_kwargs):
            result = {"text": "speech", "segments": [{"text": "speech", **timing}]}
            if provenance is not None:
                result["timestamps_available"] = provenance
            return result

    response = _post(_client(monkeypatch, Backend), mode=mode)
    assert response.status_code == 200, response.text
    assert response.json()["timestamps_available"] is expected


# ── The live-dictation socket's own final-result builder ────────────────────
#
# Every existing capture_ws test stubs `_transcribe_buffer_full` out, so its
# response builder was never exercised. Call it directly: sherpa is the first
# capture engine and its `_sherpa_result` degrades to end=None, so this half is
# reachable through the default dictation path.

def test_ws_full_result_passes_null_end_through(monkeypatch):
    import asyncio

    from api.routers import capture_ws as cw

    monkeypatch.setattr(
        "services.asr_backend.get_capture_asr_backend",
        lambda **_k: _PartlyTimedBackend())

    async def _straight_through(_pool, run, **_kw):
        return run()

    monkeypatch.setattr(
        "services.asr_backend.run_transcribe_guarded", _straight_through)

    result = asyncio.run(
        cw._transcribe_buffer_full([b"\x00" * 32000], pcm_sr=16000))

    assert [s["end"] for s in result["segments"]] == [1.25, None]
    assert result["duration_s"] == 1.25
    assert result["timestamps_available"] is False


@pytest.mark.parametrize("synthetic", [False, True])
def test_ws_full_result_preserves_timing_provenance(monkeypatch, synthetic):
    import asyncio
    from api.routers import capture_ws as cw

    class Backend:
        id = "test-asr"

        def transcribe(self, _path, **_kwargs):
            result = {"text": "speech", "segments": [{"start": 0, "end": 1, "text": "speech"}]}
            if synthetic:
                result["timestamps_available"] = False
            return result

    monkeypatch.setattr("services.asr_backend.get_capture_asr_backend", lambda **_kw: Backend())

    async def straight_through(_pool, run, **_kw):
        return run()

    monkeypatch.setattr("services.asr_backend.run_transcribe_guarded", straight_through)
    result = asyncio.run(cw._transcribe_buffer_full([b"\x00" * 32000], pcm_sr=16000))
    assert result["timestamps_available"] is (not synthetic)
