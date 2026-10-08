"""
Tests for the streaming-ASR WebSocket endpoint.

Focus: the EOF text-frame protocol (added so the React `CaptureButton` can
treat the WS `final` message as the source of truth and skip the duplicate
HTTP POST that used to run on every dictation). Ground truth: an EOF text
frame must let the server deliver `final` over the still-open socket
*without* the client having to disconnect first.

The ASR backends are mocked — we're testing protocol, not transcription
quality.
"""
import os
import time
import types

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")
# Tighten the partial-tick so the test doesn't sit waiting 2 s for the
# silence path.
os.environ["OMNIVOICE_STREAM_INTERVAL"] = "0.1"
os.environ["OMNIVOICE_STREAM_SILENCE"] = "0.2"

# These tests exercise the WS protocol with stubbed transcription and assume
# ASR weights are installed — neutralize the no-ASR preflight (which otherwise
# closes the socket with a typed asr_model_missing error frame in the hermetic
# no-HF-cache test env; the preflight has its own suite:
# tests/test_asr_model_missing.py).
pytestmark = pytest.mark.usefixtures("asr_model_installed")


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    # Stub the heavy transcription helpers so the test stays in-process.
    from api.routers import capture_ws as cw

    async def fake_partial(_chunks, **_kw):
        return "hello"

    async def fake_full(_chunks, **_kw):
        return {
            "text": "hello world",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
            "language": "en",
            "duration_s": 1.0,
            "transcription_time_s": 0.01,
            "engine": "stub",
        }

    monkeypatch.setattr(cw, "_transcribe_buffer", fake_partial)
    monkeypatch.setattr(cw, "_transcribe_buffer_full", fake_full)

    from main import app
    # client=("127.0.0.1", 50000) matches the loopback allow-list in
    # backend/api/routers/capture_ws.py:_LOOPBACK_HOSTS. Starlette's default
    # TestClient uses client=("testclient", 50000), which the WS guard rejects.
    # Matches the pattern PR #84 established for HTTP TestClient fixtures.
    return TestClient(app, client=("127.0.0.1", 50000))


def _audio_chunk(n_bytes: int = 20_000) -> bytes:
    # MIN_BUFFER_BYTES is 16_000 — give the server enough to trigger a partial
    # AND a final.
    return b"\x00" * n_bytes


def test_select_sherpa_spec_ignores_demoted_query_override(monkeypatch):
    """A persisted frontend query must not resurrect a silent recognizer."""
    from api.routers import capture_ws as cw
    from services import sherpa_dictation as sd

    model_id = "sherpa-parakeet-tdt-v3"
    websocket = types.SimpleNamespace(query_params={"model": model_id})
    monkeypatch.setattr(sd, "is_demoted", lambda mid: mid == model_id)

    assert cw._select_sherpa_spec(websocket) is None


def test_demoted_sherpa_query_keeps_pcm_transport_for_legacy_fallback(
    client, monkeypatch,
):
    """Demotion changes the recognizer, not the bytes already sent by the UI."""
    from api.routers import capture_ws as cw
    from services import sherpa_dictation as sd

    model_id = "sherpa-parakeet-tdt-v3"
    monkeypatch.setattr(sd, "is_demoted", lambda mid: mid == model_id)
    sample_rates = []

    async def fallback(_chunks, *, pcm_sr=None, dictation=False):
        sample_rates.append(pcm_sr)
        return {
            "text": "legacy fallback heard pcm",
            "segments": [],
            "language": "en",
            "engine": "stub",
        }

    monkeypatch.setattr(cw, "_transcribe_buffer_full", fallback)
    with client.websocket_connect(
        f"/ws/transcribe?model={model_id}&sr=16000"
    ) as ws:
        ws.send_bytes(_audio_chunk())
        ws.send_text("EOF")
        for _ in range(10):
            if ws.receive_json().get("type") == "final":
                break

    assert sample_rates == [16000]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"text": "top-level text"}, "top-level text"),
        (
            {"segments": [{"text": "segment one"}, {"text": "segment two"}]},
            "segment one segment two",
        ),
        (
            {"chunks": [{"text": "chunk one"}, {"text": "chunk two"}]},
            "chunk one chunk two",
        ),
    ],
)
async def test_partial_text_normalizes_every_asr_result_shape(
    monkeypatch, tmp_path, result, expected,
):
    """Live partials work for backends that expose only segments/chunks.

    WhisperX, Faster Whisper, Moonshine, and OpenAI-compatible ASR do not add a
    top-level ``text`` field. The capture seam must consume the shared ASR
    result contract instead of silently dropping their partial transcript.
    """
    from api.routers import capture_ws as cw
    from services import asr_backend

    wav = tmp_path / "partial.wav"
    wav.write_bytes(b"placeholder")

    class StubBackend:
        def transcribe(self, _path, *, word_timestamps=False):
            assert word_timestamps is False
            return result

    async def run_inline(_executor, fn, **_kwargs):
        return fn()

    monkeypatch.setattr(cw, "_pcm16_to_wav", lambda _pcm, _sr: str(wav))
    monkeypatch.setattr(asr_backend, "get_capture_asr_backend", lambda: StubBackend())
    monkeypatch.setattr(asr_backend, "run_transcribe_guarded", run_inline)

    assert await cw._transcribe_buffer([b"\x00" * 4000], pcm_sr=16000) == expected


def test_eof_text_frame_triggers_final_without_disconnect(client):
    """Client sends audio + 'EOF' text frame, expects `final` over open socket."""
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_bytes(_audio_chunk())
        ws.send_text("EOF")
        # Drain whatever the server sends (partials may or may not arrive
        # depending on timing). The first message we care about is `final`.
        final = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "final":
                final = msg
                break
        assert final is not None, "server never delivered final after EOF"
        # Finals are polished (dictation v2): leading capital + terminal
        # punctuation. The stub returns "hello world" raw.
        assert final["text"] == "Hello world."
        assert final["engine"] == "stub"


def test_legacy_disconnect_still_finalizes(client):
    """Closing the socket without EOF should still deliver final (legacy path)."""
    # Even if the client closes, the server runs final and *attempts* to send
    # before the close handshake completes. Whether the test client receives
    # it is timing-dependent — we mostly care that no exception bubbles up
    # and the server doesn't deadlock.
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_bytes(_audio_chunk())
        # Just close — don't wait. Endpoint should clean up gracefully.


def test_empty_binary_frame_acts_as_eof(client):
    """An empty binary frame is the same end-of-audio signal as 'EOF' text."""
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_bytes(_audio_chunk())
        ws.send_bytes(b"")
        final = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "final":
                final = msg
                break
        assert final is not None
        assert final["engine"] == "stub"


def test_slow_llm_never_blocks_final_beyond_budget(client, monkeypatch):
    """P0 regression (the measured ~51s stall): with refinement armed and a
    slow/dead LLM, the `final` must arrive within the hard
    OMNIVOICE_REFINE_TIMEOUT_S budget, NOT after the LLM's full latency.

    Fail-before: the handler awaited ``maybe_refine`` unbounded, so a 3s (in
    prod, ~51s) LLM held the `final` — the pill hung "Transcribing…". Pass-
    after: the final ships the unrefined (but polished) text within the budget.
    """
    monkeypatch.setenv("OMNIVOICE_REFINE_TIMEOUT_S", "0.3")

    def _slow(_t, **_kw):
        time.sleep(3.0)  # a dead endpoint would never answer in the test window
        return "REFINED (must never arrive)"

    # Patch at the source module — the handler runs maybe_refine off-thread and
    # maybe_refine_async resolves the name from services.refinement at call time.
    monkeypatch.setattr("services.refinement.maybe_refine", _slow)

    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_bytes(_audio_chunk())
        ws.send_text("EOF")
        t0 = time.perf_counter()
        final = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "final":
                final = msg
                break
        elapsed = time.perf_counter() - t0

    assert final is not None, "server never delivered final"
    # The unrefined, polished text — refinement timed out and fell back.
    assert final["text"] == "Hello world."
    assert "refined_text" not in final
    # Well under the 3s LLM sleep; the 0.3s budget + overhead is the ceiling.
    assert elapsed < 2.0, f"final blocked {elapsed:.1f}s on the slow LLM"


# ── Capture-ASR background warm-up gating (dictation v2) ─────────────────────
#
# The dictation model warms in the background BY DEFAULT (~30s post-boot);
# OMNIVOICE_PRELOAD_CAPTURE_ASR=0 opts out, and the warm-up is skipped when
# the machine is under 4 GB of free RAM.


def test_capture_preload_defaults_on(monkeypatch):
    import main
    monkeypatch.delenv("OMNIVOICE_PRELOAD_CAPTURE_ASR", raising=False)
    assert main._env_flag("OMNIVOICE_PRELOAD_CAPTURE_ASR", default=True)
    monkeypatch.setenv("OMNIVOICE_PRELOAD_CAPTURE_ASR", "0")
    assert not main._env_flag("OMNIVOICE_PRELOAD_CAPTURE_ASR", default=True)
    monkeypatch.setenv("OMNIVOICE_PRELOAD_CAPTURE_ASR", "1")
    assert main._env_flag("OMNIVOICE_PRELOAD_CAPTURE_ASR", default=True)


def test_capture_preload_delay_default_and_override(monkeypatch):
    import main
    monkeypatch.delenv("OMNIVOICE_CAPTURE_PRELOAD_DELAY", raising=False)
    assert main._capture_preload_delay_s() == 30.0
    monkeypatch.setenv("OMNIVOICE_CAPTURE_PRELOAD_DELAY", "0")
    assert main._capture_preload_delay_s() == 0.0
    monkeypatch.setenv("OMNIVOICE_CAPTURE_PRELOAD_DELAY", "junk")
    assert main._capture_preload_delay_s() == 30.0


def test_capture_preload_ram_guard(monkeypatch):
    import types
    import main
    import psutil

    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: types.SimpleNamespace(available=2 * 1024**3))
    assert not main._capture_preload_ram_ok()
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: types.SimpleNamespace(available=8 * 1024**3))
    assert main._capture_preload_ram_ok()

    # Unmeasurable → warm anyway (the load path has its own error handling).
    def _boom():
        raise RuntimeError("no vm info")
    monkeypatch.setattr(psutil, "virtual_memory", _boom)
    assert main._capture_preload_ram_ok()


def test_pause_outlasts_silence_timeout_and_resume_keeps_audio(client, monkeypatch):
    from api.routers import capture_ws as cw
    monkeypatch.setattr(cw, 'SILENCE_TIMEOUT_S', 0.05)
    monkeypatch.setattr(cw, 'PARTIAL_INTERVAL_S', 0.01)
    sizes = []

    async def final(chunks, **kwargs):
        sizes.append(sum(map(len, chunks)))
        return {'text': 'kept both parts', 'segments': [], 'language': 'en', 'engine': 'stub'}

    monkeypatch.setattr(cw, '_transcribe_buffer_full', final)
    with client.websocket_connect('/ws/transcribe') as ws:
        ws.send_bytes(_audio_chunk())
        ws.send_text('PAUSE')
        time.sleep(0.15)
        ws.send_text('RESUME')
        ws.send_bytes(_audio_chunk())
        ws.send_text('EOF')
        while ws.receive_json().get('type') != 'final':
            pass
    assert sizes == [40_000]


@pytest.mark.parametrize("origin", ["app://voicestudio", "app://voicestudio.evil", "app://other-app"])
def test_electron_stream_origin_and_final_delivery(client, monkeypatch, origin):
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.delenv("OMNIVOICE_ALLOWED_ORIGINS", raising=False)
    if origin != "app://voicestudio":
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/ws/transcribe", headers={"origin": origin}):
                pass
        assert error.value.code == 1008
        return
    with client.websocket_connect("/ws/transcribe", headers={"origin": origin}) as ws:
        ws.send_bytes(_audio_chunk())
        ws.send_text("EOF")
        while True:
            message = ws.receive_json()
            if message["type"] == "final":
                assert message["text"] == "Hello world."
                break


def test_electron_http_preflight_uses_the_same_desktop_origin(client):
    response = client.options("/system/info", headers={
        "origin": "app://voicestudio", "access-control-request-method": "GET",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "app://voicestudio"
