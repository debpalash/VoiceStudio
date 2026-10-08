"""
Streaming-protocol test for the sherpa-onnx live-dictation WS path.

North-star: partials must arrive AS THE TEXT GROWS, before the final. This
drives ``/ws/transcribe?model=<streaming-id>`` with a mocked OnlineRecognizer
whose decoded text grows frame-by-frame, then fires an endpoint, and asserts:
  • ≥1 {"type":"partial"} arrives with growing text BEFORE the committed result,
  • an endpoint produces a {"type":"final"} mid-session,
  • a trailing {"type":"final"} is sent on EOF.

Everything is mocked (no sherpa wheel, no model download) — protocol only.
"""
import os
import sys
import types

import pytest
# These tests exercise ASR-consumer mechanics and assume ASR weights are
# installed - neutralize the no-ASR preflight (its own suite:
# tests/test_asr_model_missing.py).
pytestmark = pytest.mark.usefixtures("asr_model_installed")


os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


class _GrowingStream:
    def accept_waveform(self, sr, samples):
        pass

    def input_finished(self):
        pass


class _GrowingOnlineRecognizer:
    """Emits "a", "a b", "a b c" on successive frames, then an endpoint."""

    def __init__(self):
        self._texts = ["a", "a b", "a b c"]
        self._i = 0
        self._endpoint_at = 3  # endpoint after the 3rd frame

    def create_stream(self):
        return _GrowingStream()

    def is_ready(self, s):
        return False  # decode loop is a no-op; text advances per frame

    def decode_stream(self, s):
        pass

    def get_result(self, s):
        idx = min(self._i, len(self._texts) - 1)
        return self._texts[idx]

    def is_endpoint(self, s):
        return self._i >= self._endpoint_at

    def reset(self, s):
        self._i = 0
        self._texts = ["tail"]
        self._endpoint_at = 999


class _SilentOnlineRecognizer:
    """Accepts clear speech but never returns a token."""

    def create_stream(self):
        return _GrowingStream()

    def is_ready(self, s):
        return False

    def decode_stream(self, s):
        pass

    def get_result(self, s):
        return ""

    def is_endpoint(self, s):
        return False


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from api.routers import capture_ws as cw
    from services import sherpa_dictation as sd
    from services import asr_backend as ab

    spec = sd.get_spec("sherpa-zipformer-en-20m")  # streaming
    monkeypatch.setattr(cw, "_select_sherpa_spec", lambda ws: spec)
    monkeypatch.setattr(ab.SherpaDictationBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))

    rec = _GrowingOnlineRecognizer()

    def fake_ensure(self):
        self._rec = rec

    # Each accepted near-end frame advances the recognizer's text pointer.
    real_recv = cw._recv_pcm_frame

    async def counting_recv(ws, aec):
        kind, pcm = await real_recv(ws, aec)
        if kind == "near":
            rec._i += 1
        return kind, pcm

    monkeypatch.setattr(ab.SherpaDictationBackend, "ensure_loaded", fake_ensure)
    monkeypatch.setattr(cw, "_recv_pcm_frame", counting_recv)
    # Avoid LLM refinement network calls.
    monkeypatch.setitem(sys.modules, "services.refinement",
                        types.SimpleNamespace(maybe_refine=lambda t: None,
                                              collapse_repetitive_artifacts=lambda t: t))

    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


def _pcm(nbytes=2000):
    return b"\x00" * nbytes


def test_partials_before_final(client):
    with client.websocket_connect("/ws/transcribe?model=sherpa-zipformer-en-20m&sr=16000") as ws:
        # Three frames → growing partials, then endpoint → mid-session final.
        ws.send_bytes(_pcm())
        ws.send_bytes(_pcm())
        ws.send_bytes(_pcm())
        ws.send_text("EOF")

        msgs = []
        for _ in range(20):
            try:
                msgs.append(ws.receive_json())
            except Exception:
                break
            # Finals are polished (dictation v2) — "a b c" ships as "A b c."
            if msgs[-1].get("type") == "final" and msgs[-1].get("text") in ("A b c.", ""):
                # got the endpoint-final; keep draining for the EOF final too
                if len([m for m in msgs if m["type"] == "final"]) >= 1:
                    # try one more receive for trailing final, then stop
                    try:
                        msgs.append(ws.receive_json())
                    except Exception:
                        pass
                    break

    # Cold-start status frames don't count as results.
    msgs = [m for m in msgs if m.get("type") != "status"]
    types_seen = [m["type"] for m in msgs]
    partials = [m for m in msgs if m["type"] == "partial"]
    finals = [m for m in msgs if m["type"] == "final"]

    # At least one partial arrived, and the FIRST partial came before the
    # FIRST final (the whole point — live text as you speak).
    assert partials, f"no partials emitted; saw {types_seen}"
    assert finals, f"no final emitted; saw {types_seen}"
    assert types_seen.index("partial") < types_seen.index("final")
    assert [m["final_kind"] for m in finals] == ["utterance", "summary"]

    # Partials grow monotonically in length.
    lengths = [len(p["text"]) for p in partials]
    assert lengths == sorted(lengths)


def test_streaming_silent_model_falls_back_and_demotes(monkeypatch):
    """Speech into a token-silent streaming recognizer still returns text.

    The offline Sherpa handler already detects this runtime failure class. The
    streaming handler must expose the same recovery contract instead of
    returning a successful-looking empty summary.
    """
    import numpy as np
    from fastapi.testclient import TestClient
    from api.routers import capture_ws as cw
    from services import asr_backend as ab
    from services import sherpa_dictation as sd

    spec = sd.get_spec("sherpa-zipformer-en-20m")
    monkeypatch.setattr(cw, "_select_sherpa_spec", lambda ws: spec)
    monkeypatch.setattr(ab.SherpaDictationBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab.SherpaDictationBackend, "ensure_loaded",
                        lambda self: setattr(self, "_rec", _SilentOnlineRecognizer()))
    monkeypatch.setattr(sd, "is_installed", lambda _spec: True)

    demoted = []
    monkeypatch.setattr(sd, "demote_model", lambda model_id: demoted.append(model_id) or True)

    fallback_calls = []

    async def fallback(chunks, *, pcm_sr=None, skip_sherpa=False, dictation=False):
        fallback_calls.append((b"".join(chunks), pcm_sr, skip_sherpa))
        return {
            "text": "fallback heard me",
            "segments": [{"start": 0.0, "end": 0.25, "text": "fallback heard me"}],
            "language": "en",
            "engine": "stub-fallback",
        }

    monkeypatch.setattr(cw, "_transcribe_buffer_full", fallback)
    monkeypatch.setitem(sys.modules, "services.refinement",
                        types.SimpleNamespace(maybe_refine_async=lambda t: None,
                                              collapse_repetitive_artifacts=lambda t: t))

    speech = np.full(3000, 3000, dtype=np.int16).tobytes()
    from main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    with client.websocket_connect(
        "/ws/transcribe?model=sherpa-zipformer-en-20m&sr=16000"
    ) as ws:
        ws.send_bytes(speech)
        ws.send_text("EOF")
        final = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "final":
                final = msg
                break

    assert final is not None
    assert final["text"] == "Fallback heard me."
    assert final["final_kind"] == "summary"
    assert final["engine"] == "capture-asr-fallback"
    assert final["model_silent"] == spec.id
    assert final["warning"]
    assert demoted == [spec.id]
    assert fallback_calls == [(speech, 16000, True)]


def test_streaming_silent_model_does_not_download_a_fallback(monkeypatch):
    """An installed silent Sherpa model must not trigger another model pull.

    The session-start probe validates the selected Sherpa weights, but that
    says nothing about the fallback. Before demoting to it, recovery must
    separately prove the capture fallback is installed — otherwise it invokes
    an ASR backend that auto-downloads on a cache miss, turning a failed
    dictation into a surprise multi-gigabyte pull.
    """
    import numpy as np
    from fastapi.testclient import TestClient
    from api.routers import capture_ws as cw
    from services import asr_backend as ab
    from services import sherpa_dictation as sd

    spec = sd.get_spec("sherpa-zipformer-en-20m")
    monkeypatch.setattr(cw, "_select_sherpa_spec", lambda ws: spec)
    monkeypatch.setattr(ab.SherpaDictationBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab.SherpaDictationBackend, "ensure_loaded",
                        lambda self: setattr(self, "_rec", _SilentOnlineRecognizer()))
    monkeypatch.setattr(sd, "is_installed", lambda _spec: True)
    monkeypatch.setattr(sd, "demote_model", lambda _model_id: True)

    probes = []

    def probe(**kwargs):
        probes.append(kwargs)
        if kwargs.get("sherpa_model_id") == spec.id:
            return None  # selected Sherpa model is installed
        return {
            "error": "asr_model_missing",
            "missing_repo_id": "local/fallback-not-installed",
            "recommended": None,
        }

    monkeypatch.setattr(ab, "asr_model_missing_error", probe)
    fallback_calls = []

    async def fallback(_chunks, *, pcm_sr=None, skip_sherpa=False, dictation=False):
        fallback_calls.append(pcm_sr)
        return {"text": "this required a download", "segments": []}

    monkeypatch.setattr(cw, "_transcribe_buffer_full", fallback)

    speech = np.full(3000, 3000, dtype=np.int16).tobytes()
    from main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    with client.websocket_connect(
        "/ws/transcribe?model=sherpa-zipformer-en-20m&sr=16000"
    ) as ws:
        ws.send_bytes(speech)
        ws.send_text("EOF")
        final = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "final":
                final = msg
                break

    assert final is not None
    assert final["text"] == ""
    assert final["model_silent"] == spec.id
    assert fallback_calls == []
    assert probes == [
        {"purpose": "dictation", "sherpa_model_id": spec.id},
        {"purpose": "dictation", "skip_sherpa": True, "require_installed": True},
    ]


@pytest.mark.asyncio
async def test_silent_recovery_needs_fallback_speech_before_demotion(monkeypatch):
    """Noise alone must not persistently disable an otherwise healthy model."""
    from api.routers import capture_ws as cw
    from services import asr_backend as ab
    from services import sherpa_dictation as sd

    spec = sd.get_spec("sherpa-zipformer-en-20m")
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **_kwargs: None)
    demoted = []
    monkeypatch.setattr(sd, "demote_model", lambda model_id: demoted.append(model_id) or True)

    async def silent_fallback(_chunks, *, pcm_sr=None, skip_sherpa=False, dictation=False):
        assert pcm_sr == 16000
        assert skip_sherpa is True
        return {"text": "", "segments": []}

    monkeypatch.setattr(cw, "_transcribe_buffer_full", silent_fallback)

    recovered, segments = await cw._recover_silent_sherpa(
        spec, b"\x01\x00" * 3000, 16000,
    )

    assert recovered == ""
    assert segments == []
    assert demoted == []


def test_non_streaming_model_uses_offline_handler(monkeypatch):
    """An offline-kind sherpa model routes to the offline cadence handler and
    still finalizes (sanity that the kind branch wires up)."""
    from fastapi.testclient import TestClient
    from api.routers import capture_ws as cw
    from services import sherpa_dictation as sd
    from services import asr_backend as ab

    spec = sd.get_spec("sherpa-whisper-tiny")  # offline
    monkeypatch.setattr(cw, "_select_sherpa_spec", lambda ws: spec)
    monkeypatch.setattr(ab.SherpaDictationBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))

    def fake_ensure(self):
        self._rec = object()

    monkeypatch.setattr(ab.SherpaDictationBackend, "ensure_loaded", fake_ensure)
    monkeypatch.setattr(ab.SherpaDictationBackend, "_decode_offline",
                        lambda self, samples, sr: "offline text")
    monkeypatch.setitem(sys.modules, "services.refinement",
                        types.SimpleNamespace(maybe_refine=lambda t: None,
                                              collapse_repetitive_artifacts=lambda t: t))
    monkeypatch.setenv("OMNIVOICE_SHERPA_OFFLINE_PARTIAL", "0.05")
    # reload module-level cadence constant
    cw.SHERPA_OFFLINE_PARTIAL_S = 0.05

    from main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    with client.websocket_connect("/ws/transcribe?model=sherpa-whisper-tiny&sr=16000") as ws:
        ws.send_bytes(b"\x00" * 4000)
        ws.send_text("EOF")
        final = None
        for _ in range(20):
            try:
                m = ws.receive_json()
            except Exception:
                break
            if m.get("type") == "final":
                final = m
                break
    assert final is not None
    # Polished final (dictation v2): leading capital + terminal punctuation.
    assert final["text"] == "Offline text."
    assert final["engine"] == "sherpa-onnx-asr"
    assert final["final_kind"] == "summary"


def test_offline_silent_model_does_not_download_a_fallback(monkeypatch):
    """Offline silent-model recovery observes the same local-only gate."""
    import numpy as np
    from fastapi.testclient import TestClient
    from api.routers import capture_ws as cw
    from services import asr_backend as ab
    from services import sherpa_dictation as sd

    spec = sd.get_spec("sherpa-whisper-tiny")
    monkeypatch.setattr(cw, "_select_sherpa_spec", lambda ws: spec)
    monkeypatch.setattr(ab.SherpaDictationBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab.SherpaDictationBackend, "ensure_loaded",
                        lambda self: setattr(self, "_rec", object()))
    monkeypatch.setattr(ab.SherpaDictationBackend, "_decode_offline",
                        lambda self, samples, sr: "")
    monkeypatch.setattr(sd, "is_installed", lambda _spec: True)
    monkeypatch.setattr(sd, "demote_model", lambda _model_id: True)

    probes = []

    def probe(**kwargs):
        probes.append(kwargs)
        if kwargs.get("sherpa_model_id") == spec.id:
            return None
        return {
            "error": "asr_model_missing",
            "missing_repo_id": "local/fallback-not-installed",
            "recommended": None,
        }

    monkeypatch.setattr(ab, "asr_model_missing_error", probe)
    fallback_calls = []

    async def fallback(_chunks, *, pcm_sr=None, skip_sherpa=False, dictation=False):
        fallback_calls.append(pcm_sr)
        return {"text": "this required a download", "segments": []}

    monkeypatch.setattr(cw, "_transcribe_buffer_full", fallback)

    speech = np.full(3000, 3000, dtype=np.int16).tobytes()
    from main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    with client.websocket_connect(
        "/ws/transcribe?model=sherpa-whisper-tiny&sr=16000"
    ) as ws:
        ws.send_bytes(speech)
        ws.send_text("EOF")
        final = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "final":
                final = msg
                break

    assert final is not None
    assert final["text"] == ""
    assert final["model_silent"] == spec.id
    assert fallback_calls == []
    assert probes == [
        {"purpose": "dictation", "sherpa_model_id": spec.id},
        {"purpose": "dictation", "skip_sherpa": True, "require_installed": True},
    ]


# ── Utterance-windowed offline decoding (dictation v2) ───────────────────────


def test_offline_silence_gate_commits_mid_session(monkeypatch):
    """~0.6s of trailing silence must COMMIT the current utterance: a `final`
    flushes mid-session (not just at EOF) and the committed samples are
    dropped from the live buffer, so no decode ever spans more than one
    utterance (the O(n²) full-buffer re-decode fix)."""
    import time as _time

    import numpy as np
    from fastapi.testclient import TestClient
    from api.routers import capture_ws as cw
    from services import sherpa_dictation as sd
    from services import asr_backend as ab

    spec = sd.get_spec("sherpa-whisper-tiny")  # offline kind
    monkeypatch.setattr(cw, "_select_sherpa_spec", lambda ws: spec)
    monkeypatch.setattr(ab.SherpaDictationBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab.SherpaDictationBackend, "ensure_loaded",
                        lambda self: setattr(self, "_rec", object()))

    decoded_lens = []

    def fake_decode(self, samples, sr):
        decoded_lens.append(len(samples))
        return "utterance one"

    monkeypatch.setattr(ab.SherpaDictationBackend, "_decode_offline", fake_decode)
    monkeypatch.setitem(sys.modules, "services.refinement",
                        types.SimpleNamespace(maybe_refine=lambda t: None,
                                              collapse_repetitive_artifacts=lambda t: t))
    cw.SHERPA_OFFLINE_PARTIAL_S = 0.05  # fast ticks for the test

    speech = np.full(4000, 3000, dtype=np.int16).tobytes()   # 0.25s speech
    silence = b"\x00" * 22400                                # 0.7s silence
    utt1_samples = (len(speech) + len(silence)) // 2         # 15200

    from main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    with client.websocket_connect("/ws/transcribe?model=sherpa-whisper-tiny&sr=16000") as ws:
        ws.send_bytes(speech)
        ws.send_bytes(silence)
        # Give the gate a few ticks to commit utterance 1, then speak again.
        _time.sleep(0.4)
        ws.send_bytes(speech)
        ws.send_text("EOF")
        msgs = []
        for _ in range(40):
            try:
                m = ws.receive_json()
            except Exception:
                break
            msgs.append(m)

    finals = [m for m in msgs if m.get("type") == "final"]
    # Two finals: the gate-committed utterance mid-session + the EOF trailing
    # final. The old behavior produced exactly one (everything at EOF).
    assert len(finals) >= 2, f"silence gate never committed mid-session: {msgs}"
    assert finals[0]["text"] == "Utterance one."
    assert all(m["final_kind"] == "utterance" for m in finals[:-1])
    assert finals[-1]["final_kind"] == "summary"
    # EOF final = committed pieces + the drained live tail (utterance 2).
    assert finals[-1]["text"] == "Utterance one. Utterance one."
    # O(n²) fix: every decode was bounded by ONE utterance window — never a
    # re-decode of already-committed audio (which would be >utt1_samples).
    assert decoded_lens, "decoder never ran"
    assert max(decoded_lens) <= utt1_samples


def test_status_frames_precede_results(monkeypatch):
    """A WS session whose model isn't cached yet narrates the cold start:
    status 'downloading' (or 'loading' when cached) then 'ready', before any
    partial/final."""
    from fastapi.testclient import TestClient
    from api.routers import capture_ws as cw
    from services import sherpa_dictation as sd
    from services import asr_backend as ab

    spec = sd.get_spec("sherpa-whisper-tiny")
    monkeypatch.setattr(cw, "_select_sherpa_spec", lambda ws: spec)
    monkeypatch.setattr(ab.SherpaDictationBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab.SherpaDictationBackend, "ensure_loaded",
                        lambda self: setattr(self, "_rec", object()))
    monkeypatch.setattr(ab.SherpaDictationBackend, "_decode_offline",
                        lambda self, samples, sr: "hi")
    monkeypatch.setattr(sd, "is_installed", lambda spec: False)  # cold cache
    monkeypatch.setitem(sys.modules, "services.refinement",
                        types.SimpleNamespace(maybe_refine=lambda t: None,
                                              collapse_repetitive_artifacts=lambda t: t))

    from main import app
    client = TestClient(app, client=("127.0.0.1", 50000))
    with client.websocket_connect("/ws/transcribe?model=sherpa-whisper-tiny&sr=16000") as ws:
        msgs = [ws.receive_json(), ws.receive_json()]  # the two status frames
        ws.send_bytes(b"\x00" * 4000)
        ws.send_text("EOF")
        for _ in range(20):
            try:
                m = ws.receive_json()
            except Exception:
                break
            msgs.append(m)
            if m.get("type") == "final":
                break

    assert msgs[0] == {"type": "status", "stage": "downloading"}
    assert msgs[1] == {"type": "status", "stage": "ready"}
    types_seen = [m["type"] for m in msgs]
    assert types_seen.index("status") < types_seen.index("final")


# ── Endpoint-rule tuning (dictation v2) ──────────────────────────────────────


class _KwargsOnlineRecognizer:
    last_kwargs = None

    @classmethod
    def from_transducer(cls, **kw):
        cls.last_kwargs = kw
        return cls()

    @classmethod
    def from_paraformer(cls, **kw):
        cls.last_kwargs = kw
        return cls()


def test_endpoint_rules_fast_defaults_and_env_override(monkeypatch):
    """Streaming endpoint rules commit at 1.0s/0.6s by default (was 2.4/1.2 —
    laggy) and honor OMNIVOICE_DICTATION_ENDPOINT_R1/R2."""
    from services import sherpa_dictation as sd

    fake = types.ModuleType("sherpa_onnx")
    fake.OnlineRecognizer = _KwargsOnlineRecognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    monkeypatch.setattr(sd, "_resolve_model_dir",
                        lambda spec, download=True: "/fake/dir")
    monkeypatch.delenv("OMNIVOICE_DICTATION_ENDPOINT_R1", raising=False)
    monkeypatch.delenv("OMNIVOICE_DICTATION_ENDPOINT_R2", raising=False)

    sd.build_online_recognizer(sd.get_spec("sherpa-zipformer-en-20m"))
    kw = _KwargsOnlineRecognizer.last_kwargs
    assert kw["rule1_min_trailing_silence"] == 1.0
    assert kw["rule2_min_trailing_silence"] == 0.6
    assert kw["rule3_min_utterance_length"] == 20  # unchanged

    monkeypatch.setenv("OMNIVOICE_DICTATION_ENDPOINT_R1", "2.4")
    monkeypatch.setenv("OMNIVOICE_DICTATION_ENDPOINT_R2", "1.2")
    sd.build_online_recognizer(sd.get_spec("sherpa-paraformer-bilingual-zh-en"))
    kw = _KwargsOnlineRecognizer.last_kwargs
    assert kw["rule1_min_trailing_silence"] == 2.4
    assert kw["rule2_min_trailing_silence"] == 1.2

    # Garbage env falls back to the defaults rather than crashing dictation.
    monkeypatch.setenv("OMNIVOICE_DICTATION_ENDPOINT_R1", "fast")
    monkeypatch.setenv("OMNIVOICE_DICTATION_ENDPOINT_R2", "")
    assert sd._endpoint_rules() == (1.0, 0.6)
