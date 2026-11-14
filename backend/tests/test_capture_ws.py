"""Tests for the streaming ASR WebSocket helpers.

Only tests the pure-Python helper functions (no GPU needed).
The WebSocket endpoint itself requires the full app, which we
skip in CI — it's integration-tested via the browser.
"""
# conftest.py puts `backend/` on sys.path. capture_ws imports its heavy deps
# (model_manager / asr_backend / ffmpeg_utils) lazily inside handlers, so no
# stubbing is needed — the old empty-ModuleType stubs leaked process-wide at
# collection time and broke every later `from services.ffmpeg_utils import
# find_ffmpeg` in mixed runs (see conftest.py).
from api.routers.capture_ws import _chunks_to_wav, MIN_BUFFER_BYTES


class TestChunksToWav:
    def test_empty_returns_none(self):
        assert _chunks_to_wav([]) is None

    def test_tiny_returns_none(self):
        assert _chunks_to_wav([b"\x00" * 10]) is None

    def test_below_100_bytes_returns_none(self):
        assert _chunks_to_wav([b"\x00" * 99]) is None


class TestConstants:
    def test_min_buffer_bytes_reasonable(self):
        """MIN_BUFFER_BYTES should be at least 0.25s of 16-bit mono 16kHz."""
        # 16kHz * 2 bytes * 0.25s = 8000
        assert MIN_BUFFER_BYTES >= 8000

    def test_partial_interval_positive(self):
        from api.routers.capture_ws import PARTIAL_INTERVAL_S
        assert PARTIAL_INTERVAL_S > 0

    def test_silence_timeout_positive(self):
        from api.routers.capture_ws import SILENCE_TIMEOUT_S
        assert SILENCE_TIMEOUT_S > 0


class TestLoopbackGuard:
    """Source-level guard against regressing the WS loopback contract.

    Same shape as tests/test_bind_host.py: these don't run the endpoint
    (which needs the full app + a WebSocket client). They read the source
    and assert the guard is present. If a future refactor removes the check,
    this test fails with a pointer to the security rationale.

    Why a source-level guard: the /ws/transcribe socket streams the user's
    live microphone audio. Any local process opening this WS without an
    origin check could exfiltrate dictation in real time. HTTP routers use
    Depends(require_loopback) at router level; WebSocket dependency
    injection is brittle across FastAPI versions, so the guard is a plain
    coroutine. It now lives in ``api/dependencies.require_ws_access`` and
    every WebSocket endpoint awaits it, so the assertions cover both the
    shared control and the call site's ordering.
    """

    def _rerouter_src(self):
        from pathlib import Path
        return (
            Path(__file__).resolve().parent.parent
            / "api" / "routers" / "capture_ws.py"
        ).read_text(encoding="utf-8")

    def _guard_src(self):
        from pathlib import Path
        return (
            Path(__file__).resolve().parent.parent
            / "api" / "dependencies.py"
        ).read_text(encoding="utf-8")

    def test_ws_transcribe_awaits_the_shared_guard(self):
        assert "require_ws_access" in self._rerouter_src(), (
            "capture_ws.py no longer awaits require_ws_access — the WS "
            "loopback guard has been removed. Reinstate it before accept()."
        )

    def test_shared_guard_checks_loopback_and_closes_with_1008(self):
        src = self._guard_src()
        assert "is_local_host" in src, (
            "require_ws_access no longer references is_local_host — the WS "
            "loopback guard has been weakened."
        )
        assert "websocket.close(code=1008" in src, (
            "require_ws_access must close refused connections with code 1008 "
            "(Policy Violation). Otherwise any local process can stream the "
            "user's microphone."
        )

    def test_guard_runs_before_accept(self):
        src = self._rerouter_src()
        # The guard call must appear before the first accept() in the
        # ws_transcribe handler, otherwise an attacker gets a window where
        # the WS is open and can send audio frames.
        guard_idx = src.find("require_ws_access")
        accept_idx = src.find("await websocket.accept()")
        assert 0 <= guard_idx < accept_idx, (
            "require_ws_access must be awaited before websocket.accept() — the "
            "guard runs *before* the handshake completes so non-loopback "
            "origins never see an open socket."
        )
