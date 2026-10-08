"""
Streaming ASR via WebSocket — live partial transcription results.

Client streams audio chunks (PCM/WebM) and receives partial + final
transcription JSON messages in real-time. Used by CaptureButton for
live dictation feedback.

Protocol:
    → Client sends binary audio frames (16-bit PCM or WebM/Opus blobs)
    ← Server sends JSON messages:

    Raw PCM mode (``?pcm=1&sr=16000``) is the container-free fallback for
    WebViews without MediaRecorder. Opt-in AEC mode
    (``?aec=1[&sr=16000]``, parity Action 8b): for dictating
    while the app plays audio. Frames must be raw int16 mono PCM, each tagged
    with a 1-byte prefix — 0x00 = microphone, 0x01 = playback reference. The
    server runs an NLMS echo canceller, cleaning the mic against the reference
    before transcription. Without the param the protocol is unchanged.
        {"type": "partial", "text": "Hello wor..."}      — interim result
        {"type": "final",   "text": "Hello world.",       — committed result
         "segments": [...], "language": "en",
         "duration_s": 4.2, "transcription_time_s": 0.8,
         "engine": "mlx-whisper"}
        {"type": "status",  "stage": "downloading"|"loading"|"ready"}
                                                          — model cold-start
        {"type": "error",   "message": "...", "kind": "...",
         "detail": "..."}                                  — error ("detail"
                                                          kept for legacy)

    Sherpa ``final`` frames additionally carry
    ``"final_kind": "utterance"|"summary"``. Utterances are mid-session
    commits; the summary is the authoritative whole-session result at EOF.

    Every ``final`` text is normalised by services.text_polish (leading
    capital for Latin scripts, terminal punctuation, single-spaced) so the
    pasted result reads like typed text. Partials are raw.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import tempfile
import time
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.dependencies import is_local_host, ws_remote_authorized
from services.text_polish import polish_text

router = APIRouter()
logger = logging.getLogger("omnivoice.capture_ws")


def _timing(value):
    """A segment timing, or ``None`` when the engine could not determine one.

    ``dict.get(key, 0)`` returns a stored ``None`` rather than the default, so
    rounding it raised (#1904). The null is the honest answer here — this module
    emits it deliberately for un-endpointed utterances — and the segment list
    renders whichever half of the range is known.
    """
    return round(value, 2) if isinstance(value, (int, float)) else None

SPEECH_PROTOCOL = "voicestudio.speech.v1"
PLATFORM_STREAM_PATH = "/v1/audio/transcriptions/stream"

# How often (seconds) to run transcription on the accumulated buffer.
# Shorter = more responsive but more GPU load.
PARTIAL_INTERVAL_S = float(os.environ.get("OMNIVOICE_STREAM_INTERVAL", "2.0"))

# Maximum silence before we auto-finalize (seconds of no new audio).
SILENCE_TIMEOUT_S = float(os.environ.get("OMNIVOICE_STREAM_SILENCE", "3.0"))

# Minimum buffer size before first partial (bytes of raw audio).
MIN_BUFFER_BYTES = 64000  # ~2s of 16-bit mono 16kHz — needs enough WebM frames for ffmpeg

# Minimum buffer for final transcription — much lower since we always want
# to transcribe whatever the user recorded, even short utterances.
MIN_FINAL_BUFFER_BYTES = 4000  # ~125ms of 16-bit mono 16kHz

# ── Dictate-over-playback AEC (parity Action 8b, opt-in) ──────────────────
# Activated by the ``?aec=1`` query param. When OFF (the default), the
# protocol and behaviour are byte-for-byte unchanged. When ON, the client
# streams raw int16 mono PCM frames tagged with a 1-byte type prefix so the
# server can tell mic audio from the playback reference it must cancel:
_AEC_NEAR = 0x00  # microphone frame (clean it, then buffer for ASR)
_AEC_FAR = 0x01   # playback reference frame (feed the echo model only)


# Client-supplied ``?sr=`` values outside the range real capture devices use
# are replaced with 16 kHz. The rate sizes server-side state — RecoveryTail
# multiplies it by RECOVERY_TAIL_SECONDS to compute its byte ceiling — so an
# absurd rate must never be believed: it would re-open the unbounded-memory
# path the recovery-tail cap closed.
SR_MIN, SR_MAX = 8000, 96000


def _is_end_control(text: str | None) -> bool:
    """Accept the versioned JSON control frame and the legacy ``EOF`` frame."""
    if text == "EOF":
        return True
    if not text:
        return False
    try:
        message = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(message, dict) and message.get("type") == "input_audio.end"


class _PlatformWebSocket:
    """Add v1 session metadata without changing the legacy WebSocket contract."""

    def __init__(self, websocket: WebSocket):
        self._websocket = websocket
        self.session_id = uuid.uuid4().hex

    def __getattr__(self, name: str) -> Any:
        return getattr(self._websocket, name)

    async def send_json(self, data: Any, mode: str = "text") -> None:
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("protocol", SPEECH_PROTOCOL)
            data.setdefault("session_id", self.session_id)
            if data.get("type") == "final":
                data.setdefault("final_kind", "summary")
        await self._websocket.send_json(data, mode=mode)


def _bounded_sample_rate(query_params) -> int:
    try:
        sample_rate = int(query_params.get("sr", "16000"))
    except (TypeError, ValueError):
        return 16000
    return sample_rate if SR_MIN <= sample_rate <= SR_MAX else 16000


def _requested_pcm_sample_rate(query_params) -> int | None:
    """Return the bounded rate when the client transport is raw PCM.

    Sherpa clients omit ``pcm=1`` because the selected model already defines
    that transport. If the model is demoted or its runtime is unavailable, the
    legacy recognizer fallback must still decode those same bytes as PCM.
    """
    raw_pcm = query_params.get("pcm") in ("1", "true", "on")
    aec = query_params.get("aec") in ("1", "true", "on")
    sherpa_pcm = False
    requested_model = query_params.get("model")
    if requested_model:
        try:
            from services.sherpa_dictation import is_sherpa_model
            sherpa_pcm = is_sherpa_model(requested_model)
        except Exception:  # noqa: BLE001
            # A broken sherpa install must not decide the framing question —
            # sherpa_pcm stays False and the session negotiates the
            # MediaRecorder path; availability is re-probed (and reported)
            # when the model is actually selected.
            sherpa_pcm = False
    if not raw_pcm and not aec and not sherpa_pcm:
        return None
    return _bounded_sample_rate(query_params)


def _demux_aec_frame(data: bytes) -> tuple[str, bytes]:
    """Split a prefixed AEC binary frame into ``(kind, pcm)``.

    ``kind`` is ``"near"`` (mic) or ``"far"`` (playback reference). An empty
    or prefix-only frame yields an empty payload. Unknown prefixes are treated
    as ``"near"`` so a malformed tag degrades to plain dictation rather than
    dropping audio.
    """
    if not data:
        return "near", b""
    kind = "far" if data[0] == _AEC_FAR else "near"
    return kind, data[1:]


def _pcm16_to_wav(pcm: bytes, sample_rate: int) -> str | None:
    """Write raw int16 mono PCM to a temp WAV via stdlib ``wave`` (no ffmpeg).

    Used on the AEC path, where frames are already decoded PCM — the cleaned
    samples have no container, so the ffmpeg-sniffing ``_chunks_to_wav`` would
    misdetect them. Returns the temp path, or ``None`` for a too-short buffer.
    """
    if not pcm or len(pcm) < 100:
        return None
    import wave
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp.close()
    try:
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # int16
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return tmp.name
    except Exception as e:
        logger.debug("PCM->WAV failed: %s", e)
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return None


def _select_sherpa_spec(websocket: WebSocket):
    """Resolve the sherpa dictation model for this WS session, or None.

    A ``?model=<id>`` query param wins (the frontend can pin a model per
    session); otherwise the persisted ``dictation.model_id`` pref is used (only
    when dictation is enabled). Returns the :class:`SherpaModelSpec` or None
    (None → the legacy Whisper/WebM path runs unchanged).
    """
    try:
        from services import sherpa_dictation as sd
    except Exception:
        return None

    def _usable_spec(model_id):
        spec = sd.get_spec(model_id)
        if spec is not None and sd.is_demoted(spec.id):
            logger.warning(
                "dictation model %s is demoted — using the capture ASR fallback",
                spec.id,
            )
            return None
        return spec

    requested = websocket.query_params.get("model")
    if requested:
        return _usable_spec(requested)  # explicit selection (may be unavailable)
    # Fall back to the persisted dictation pref.
    try:
        from services.asr_backend import dictation_model_id
        mid = dictation_model_id()
    except Exception:
        mid = None
    return _usable_spec(mid) if mid else None


@router.websocket(PLATFORM_STREAM_PATH)
@router.websocket("/ws/transcribe")
async def ws_transcribe(websocket: WebSocket):
    """Stream audio in, get partial + final transcription out."""
    is_platform_stream = websocket.url.path == PLATFORM_STREAM_PATH
    if is_platform_stream:
        websocket = _PlatformWebSocket(websocket)
    # A browser can reach localhost regardless of the page's own origin.
    # Reject ambient cross-site WebSocket handshakes before the loopback-host
    # shortcut or accept(), while keeping native clients (no Origin header)
    # and configured/same-origin browser UIs working (#1646 review).
    origin = websocket.headers.get("origin")
    if origin:
        from core.csrf import origin_allowed

        if not origin_allowed(websocket):
            await websocket.close(code=1008, reason="browser origin not allowed")
            return
    # Loopback origin guard — refuse anything not from 127.0.0.1, ::1, or
    # localhost. Privileged HTTP routers use Depends(require_admin) at router
    # level; WebSocket dependency injection differs across FastAPI versions, so we
    # inline the check before accept(). Without it, any local process could
    # stream the user's microphone over this endpoint.
    # Wave 2.3 (remote backend): a non-loopback client that presents the
    # OMNIVOICE_API_KEY bearer is the thin-client dictation case — the mic
    # lives on the user's machine, the GPU here — and is allowed through.
    host = websocket.client.host if websocket.client else None
    if not is_local_host(host) and not ws_remote_authorized(websocket):
        await websocket.close(code=1008, reason="loopback origin required")
        return

    await websocket.accept()
    if is_platform_stream:
        await websocket.send_json({
            "type": "session.started",
            "input_format": (
                "audio/pcm;encoding=s16le;channels=1"
                if _requested_pcm_sample_rate(websocket.query_params) is not None
                else "audio/webm;codecs=opus"
            ),
            "sample_rate": _bounded_sample_rate(websocket.query_params),
        })

    # Live-dictation engine selection. When a sherpa-onnx model is selected
    # (via ?model= or the dictation.model_id pref) AND sherpa is installed,
    # run the dedicated low-latency handler. Otherwise fall through to the
    # legacy Whisper/WebM path, byte-for-byte unchanged.
    spec = _select_sherpa_spec(websocket)

    # TTS-only install: no ASR model on disk for this session's selection →
    # typed error frame + close, BEFORE any recognizer is built (both the
    # sherpa loader and the whisper backends auto-download weights on first
    # load). The client renders a one-click download CTA from the payload.
    # Pass the RAW ?model= override, not just the resolved spec: an invalid
    # override resolves spec to None, and a bare None would make the preflight
    # consult the persisted sherpa pref (possibly installed → preflight
    # passes) while execution falls through to the Whisper path (weights
    # possibly missing → silent auto-download). The raw string keeps the
    # preflight on the same selection execution will use.
    from services.asr_backend import asr_model_missing_detail, asr_model_missing_error
    _requested_model = websocket.query_params.get("model")
    missing = await asyncio.to_thread(
        asr_model_missing_error, purpose="dictation",
        sherpa_model_id=(
            spec.id if spec is not None else _requested_model
        ),
    )
    if missing is not None:
        try:
            await websocket.send_json({
                "type": "error", "kind": "asr_model_missing",
                "message": asr_model_missing_detail(missing), **missing,
            })
            await websocket.close()
        except Exception:  # noqa: BLE001 — client may already be gone
            pass
        return
    if spec is not None:
        from services.asr_backend import SherpaDictationBackend, capture_lease
        ok, _reason = SherpaDictationBackend.is_available()
        if ok:
            # A live session holds the shared capture backend for its whole
            # lifetime without ever re-resolving it, so the idle reaper
            # (#1101 class) must not unload the model out from under it — even
            # if the user leaves the mic open, silent, past the idle timeout.
            # The lease pins it for exactly this window and restarts the idle
            # clock on the way out.
            with capture_lease():
                if spec.streaming:
                    await _run_sherpa_streaming(websocket, spec)
                else:
                    await _run_sherpa_offline(websocket, spec)
            return
        # sherpa not installed → fall through to the legacy path so the user
        # still gets dictation (just not live partials).
        logger.info("sherpa dictation selected but unavailable — legacy path")

    # Opt-in dictate-over-playback AEC (parity Action 8b). Default OFF →
    # identical legacy behaviour. When on, frames are 1-byte-tagged raw PCM
    # and the cleaned mic stream is muxed via stdlib wave (not ffmpeg).
    aec = None
    pcm_sr = _requested_pcm_sample_rate(websocket.query_params)
    if websocket.query_params.get("aec") in ("1", "true", "on"):
        try:
            from services.aec import NlmsEchoCanceller
            aec = NlmsEchoCanceller(sample_rate=pcm_sr or 16000)
            logger.info("AEC enabled for dictation session (sr=%d)", pcm_sr)
        except Exception as e:
            # Bad sr or import failure → fall back to plain dictation.
            logger.warning("AEC requested but disabled: %s", e)
            aec = None
            pcm_sr = None

    audio_chunks: list[bytes] = []
    total_bytes = 0
    last_audio_time = time.monotonic()
    paused = False
    running = True
    partial_text = ""
    # Track whether the client initiated the disconnect. When True the
    # WebSocket is already in a closed/closing state and any attempt to
    # call `send_json()` will raise "Unexpected ASGI message".
    client_disconnected = False

    async def receive_audio():
        """Receive audio frames from the client.

        Two end-of-stream signals: (a) text frame ``"EOF"`` (preferred —
        keeps the socket open so the ``final`` message can still be sent
        before the client closes), or (b) socket disconnect (legacy path).
        The EOF protocol exists so the client can use the WS ``final``
        message as the authoritative result and skip the duplicate HTTP
        POST that used to run on every dictation.
        """
        nonlocal total_bytes, last_audio_time, running, client_disconnected, paused
        try:
            while running:
                msg = await websocket.receive()
                msg_type = msg.get("type")
                if msg_type == "websocket.disconnect":
                    client_disconnected = True
                    running = False
                    break
                if msg_type != "websocket.receive":
                    continue
                data = msg.get("bytes")
                if data is not None:
                    if len(data) == 0:
                        # Empty binary frame also acts as EOF — connection stays open.
                        running = False
                        break
                    if aec is not None:
                        # Tagged PCM: route the playback reference into the echo
                        # model and clean the mic before it reaches the buffer.
                        kind, payload = _demux_aec_frame(data)
                        if kind == "far":
                            aec.push_far_end(payload)
                            continue
                        if not payload:
                            continue
                        data = aec.process_near_end(payload)
                    audio_chunks.append(data)
                    total_bytes += len(data)
                    last_audio_time = time.monotonic()
                    continue
                if msg.get("text") in ("PAUSE", "RESUME"):
                    paused = msg["text"] == "PAUSE"
                    last_audio_time = time.monotonic()
                    continue
                if _is_end_control(msg.get("text")):
                    # Client signals end-of-audio but stays connected for `final`.
                    running = False
                    break
        except WebSocketDisconnect:
            client_disconnected = True
            running = False
        except Exception as e:
            logger.debug("WS receive ended: %s", e)
            client_disconnected = True
            running = False

    async def _safe_send(payload: dict) -> bool:
        """Send JSON to the client, returning False if the connection is gone."""
        if client_disconnected:
            return False
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            return False

    async def process_partials():
        """Periodically transcribe the accumulated buffer for partial results."""
        nonlocal partial_text, running

        while running:
            await asyncio.sleep(PARTIAL_INTERVAL_S)

            if not running:
                break

            if paused:
                continue

            # Check silence timeout
            if time.monotonic() - last_audio_time > SILENCE_TIMEOUT_S and total_bytes > MIN_BUFFER_BYTES:
                running = False
                break

            if total_bytes < MIN_BUFFER_BYTES:
                continue

            # Transcribe current buffer
            try:
                text = await _transcribe_buffer(
                    audio_chunks[:], pcm_sr=pcm_sr, dictation=not is_platform_stream,
                )
                if text and text != partial_text:
                    partial_text = text
                    await _safe_send({
                        "type": "partial",
                        "text": text,
                    })
            except Exception as e:
                logger.warning("Partial transcription failed: %s", e)

    # Run receiver and processor concurrently
    receiver_task = asyncio.create_task(receive_audio())
    processor_task = asyncio.create_task(process_partials())

    # Wait for either to finish (receiver ends on disconnect, processor on silence)
    done, pending = await asyncio.wait(
        [receiver_task, processor_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    running = False
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # Final transcription on complete buffer — skip if client already gone.
    if total_bytes > MIN_FINAL_BUFFER_BYTES:
        try:
            # The vocabulary hint is a dictation setting; API stream clients
            # on PLATFORM_STREAM_PATH never get it.
            result = await _transcribe_buffer_full(
                audio_chunks, pcm_sr=pcm_sr, dictation=not is_platform_stream,
            )
            # Dictation v2: deterministic polish so the pasted final reads
            # like typed text (leading capital, terminal punctuation).
            result["text"] = polish_text(result.get("text", ""))
            # Wave 2.1: optional local-LLM refinement of the final text.
            # HARD-BOUNDED (maybe_refine_async, ~4s OMNIVOICE_REFINE_TIMEOUT_S):
            # a slow/dead LLM can never delay this `final` beyond the budget —
            # it falls back to the unrefined (but polished) text. Best-effort:
            # never let refinement turn a good final into an error. The raw
            # text always ships too — clients paste refined_text ?? text.
            if result.get("text"):
                try:
                    from services.refinement import maybe_refine_async
                    refined = await maybe_refine_async(result["text"])
                    if refined and refined != result["text"]:
                        result["refined_text"] = refined
                except Exception as e:  # noqa: BLE001
                    logger.debug("Dictation refinement skipped: %s", e)
            if not await _safe_send({"type": "final", **result}):
                logger.debug("Skipped final send — client already disconnected")
        except Exception as e:
            logger.exception("Final transcription failed")
            await _safe_send({"type": "error", "message": str(e),
                              "kind": "transcribe", "detail": str(e)})
    else:
        await _safe_send({
            "type": "final",
            "text": "",
            "segments": [],
            "language": "unknown",
            "duration_s": 0,
            "transcription_time_s": 0,
            "engine": "none",
        })

    if not client_disconnected:
        try:
            await websocket.close()
        except Exception:
            pass


# ── sherpa-onnx live dictation handlers ─────────────────────────────────────
#
# Both handlers read raw int16 mono PCM frames (reusing the AEC framing: an
# opt-in 1-byte type prefix when ?aec=1, else bare PCM) at ?sr= (default 16000).
# This is the low-latency transport — no WebM/ffmpeg in the hot path.

# How often the offline-kind handler re-decodes the live window for a partial
# (streaming-kind decodes every frame, no cadence needed).
SHERPA_OFFLINE_PARTIAL_S = float(os.environ.get("OMNIVOICE_SHERPA_OFFLINE_PARTIAL", "0.8"))

# Utterance gate for the offline-kind handler: once the trailing this-many
# seconds of the live buffer fall below the RMS floor, the utterance is
# COMMITTED — decoded, flushed as a `final`, and dropped from the buffer. Each
# decode is thereby bounded by one utterance instead of the whole session
# (the old full-buffer re-decode was O(n²)), and a sentence commits ~0.6s
# after the user stops speaking instead of only at EOF.
SHERPA_OFFLINE_SILENCE_S = float(os.environ.get("OMNIVOICE_SHERPA_OFFLINE_SILENCE", "0.6"))
SHERPA_OFFLINE_RMS_FLOOR = float(os.environ.get("OMNIVOICE_SHERPA_OFFLINE_RMS", "0.01"))


#: Seconds of audio retained for silent-model recovery. Recovery only needs
#: enough speech to prove the model is broken and to re-transcribe what was
#: said; retaining the whole session grew ~115 MB/hour at 16 kHz on an open
#: mic, unbounded, and only ever got read when the fallback fired.
RECOVERY_TAIL_DEFAULT_SECONDS = 120.0
RECOVERY_TAIL_MAX_SECONDS = 300.0


def _bounded_recovery_tail_seconds(value: str | None) -> float:
    """Parse the recovery tail override without allowing unbounded buffers."""
    try:
        seconds = float(value) if value is not None else RECOVERY_TAIL_DEFAULT_SECONDS
    except (TypeError, ValueError):
        return RECOVERY_TAIL_DEFAULT_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        return RECOVERY_TAIL_DEFAULT_SECONDS
    return min(seconds, RECOVERY_TAIL_MAX_SECONDS)


RECOVERY_TAIL_SECONDS = _bounded_recovery_tail_seconds(
    os.environ.get("OMNIVOICE_DICTATION_RECOVERY_TAIL_S")
)


class RecoveryTail:
    """The most recent ``RECOVERY_TAIL_SECONDS`` of session audio.

    Keeps the *tail* rather than the head: a long dictation's useful speech is
    what the user just said, and the silent-model check cares about how much
    audio the session carried overall — which ``total_bytes`` still reports
    truthfully after trimming.
    """

    __slots__ = ("_buf", "_max", "total_bytes")

    def __init__(self, sample_rate: int, seconds: float = RECOVERY_TAIL_SECONDS):
        # int16 mono → 2 bytes/sample. Floor of one frame so a nonsense rate
        # or seconds value can't produce a zero-length buffer.
        self._max = max(2, int(seconds * max(1, sample_rate)) * 2)
        self._buf = bytearray()
        self.total_bytes = 0

    def extend(self, pcm: bytes) -> None:
        self._buf.extend(pcm)
        self.total_bytes += len(pcm)
        excess = len(self._buf) - self._max
        if excess > 0:
            # int16 mono: trim whole samples only. A split frame can carry an
            # odd byte count, and an odd trim would leave the tail starting
            # mid-sample — every later sample byte-shifted, and the recovery
            # transcription fed noise.
            excess += excess % 2
            del self._buf[:excess]

    def tail(self) -> bytes:
        return bytes(self._buf)


def is_model_silent(text: str, heard_speech: bool, pcm_bytes: int) -> bool:
    """True when the dictation model produced NO text despite real speech.

    Distinguishes "the user said nothing" (fine — stay quiet) from "the model
    is broken" (fall back + warn). A sherpa model can load cleanly and still
    decode nothing: the NeMo-TDT path does exactly this on some builds, where
    parakeet-tdt v2/v3 return an empty token list for clear speech while
    whisper/zipformer transcribe the same bytes. Without this, dictation just
    silently produces nothing and looks dead.
    """
    return bool(not (text or "").strip()
                and heard_speech
                and pcm_bytes > MIN_FINAL_BUFFER_BYTES)


def _pcm16_to_f32(pcm: bytes):
    """int16 little-endian mono PCM bytes → float32 numpy in [-1, 1]."""
    import numpy as np
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    # Guard against an odd trailing byte from a split frame.
    if len(pcm) % 2:
        pcm = pcm[:-1]
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _pcm16_rms(pcm: bytes) -> float:
    samples = _pcm16_to_f32(pcm)
    if not len(samples):
        return 0.0
    return float((samples * samples).mean() ** 0.5)


async def _recover_silent_sherpa(
    spec, pcm: bytes, pcm_sr: int,
) -> tuple[str, list[dict]]:
    """Retry a token-silent Sherpa session through an installed local ASR."""
    logger.warning(
        "dictation model %s decoded NOTHING from %.1fs of speech-level audio "
        "— falling back to the capture ASR engine for this session",
        spec.id, len(pcm) / float(max(1, pcm_sr) * 2),
    )
    try:
        from services.asr_backend import asr_model_missing_error
        fallback_missing = await asyncio.to_thread(
            asr_model_missing_error,
            purpose="dictation",
            skip_sherpa=True,
            require_installed=True,
        )
        if fallback_missing is not None:
            logger.warning(
                "dictation silent-model fallback is not installed (%s); "
                "skipping recovery to avoid an automatic download",
                fallback_missing.get("missing_repo_id", "unknown"),
            )
            return "", []

        result = await _transcribe_buffer_full(
            [pcm], pcm_sr=pcm_sr, skip_sherpa=True,
        )
        text = polish_text(_result_text(result))
        if not text:
            return "", []
        # The RMS gate can fire on fan/keyboard noise. Only another recognizer
        # producing words proves the audio held speech and makes persistent
        # demotion safe.
        try:
            from services.sherpa_dictation import demote_model
            if await asyncio.to_thread(demote_model, spec.id):
                logger.error(
                    "dictation model %s demoted on this machine — it will no longer be "
                    "auto-selected. Pick it again in Settings to give it another chance.",
                    spec.id,
                )
        except Exception:
            logger.exception("silent-model demotion failed")
        segments = (result or {}).get("segments") or [
            {"start": 0.0, "end": None, "text": text}
        ]
        return text, segments
    except Exception:
        logger.exception("dictation silent-model fallback failed")
        return "", []


async def _sherpa_session(websocket: WebSocket):
    """Shared WS setup for the sherpa handlers.

    Returns ``(pcm_sr, aec)``: the bounded PCM sample rate for the session
    and the echo canceller when ``?aec=1`` requested one (``None`` otherwise
    or when AEC setup fails).
    """
    pcm_sr = _bounded_sample_rate(websocket.query_params)
    aec = None
    if websocket.query_params.get("aec") in ("1", "true", "on"):
        try:
            from services.aec import NlmsEchoCanceller
            aec = NlmsEchoCanceller(sample_rate=pcm_sr)
        except Exception as e:
            logger.warning("AEC requested but disabled (sherpa): %s", e)
            aec = None
    return pcm_sr, aec


async def _recv_pcm_frame(websocket: WebSocket, aec):
    """Receive one frame; return (kind, pcm_bytes).

    kind ∈ {"near","eof","skip"}. Demuxes AEC-tagged frames when ``aec`` is on
    and feeds the playback reference into the canceller. A text "EOF" or an
    empty/closed socket yields kind "eof".
    """
    msg = await websocket.receive()
    mtype = msg.get("type")
    if mtype == "websocket.disconnect":
        return "eof", b""
    if mtype != "websocket.receive":
        return "skip", b""
    data = msg.get("bytes")
    if data is not None:
        if len(data) == 0:
            return "eof", b""
        if aec is not None:
            kind, payload = _demux_aec_frame(data)
            if kind == "far":
                aec.push_far_end(payload)
                return "skip", b""
            if not payload:
                return "skip", b""
            return "near", aec.process_near_end(payload)
        return "near", data
    if _is_end_control(msg.get("text")):
        return "eof", b""
    return "skip", b""


async def _sherpa_load_with_status(websocket: WebSocket, backend, spec) -> bool:
    """Build the recognizer off the event loop, narrating cold-start progress.

    Sends ``{"type":"status","stage":"downloading"|"loading"}`` before the
    load ("downloading" when the pinned assets aren't in the HF cache yet;
    stage-only — HF's per-file progress isn't worth a callback plumb-through)
    and ``{"type":"status","stage":"ready"}`` after, so the widget can show
    *why* the first dictation takes a moment. Returns False when the load
    failed (the error frame is sent and the socket closed here).
    """
    try:
        from services import sherpa_dictation as _sd
        stage = "loading" if _sd.is_installed(spec) else "downloading"
    except Exception:
        stage = "loading"
    try:
        await websocket.send_json({"type": "status", "stage": stage})
    except Exception:
        logger.warning("Sherpa load status could not be delivered; stopping stream setup")
        return False
    try:
        await asyncio.to_thread(backend.ensure_loaded)
    except Exception as e:
        logger.exception("sherpa dictation load failed (%s)", spec.id)
        try:
            await websocket.send_json({"type": "error", "message": str(e),
                                       "kind": "load", "detail": str(e)})
            await websocket.close()
        except Exception:
            pass
        return False
    try:
        await websocket.send_json({"type": "status", "stage": "ready"})
    except Exception:
        logger.warning("Sherpa ready status could not be delivered; stopping stream setup")
        return False
    return True


async def _run_sherpa_streaming(websocket: WebSocket, spec):
    """True streaming: feed the OnlineRecognizer frame-by-frame, emit `partial`
    every time the decoded text grows, and `final` on sherpa's endpoint (silence)
    detection and on EOF. <300ms perceived latency on CPU for the tiny models.
    """
    import numpy as np
    from services.asr_backend import get_sherpa_dictation_backend

    pcm_sr, aec = await _sherpa_session(websocket)
    logger.info("sherpa streaming dictation: model=%s sr=%d aec=%s",
                spec.id, pcm_sr, bool(aec))

    # Reuse the shared, per-model warm backend (#888): the recognizer is built
    # once and shared across sessions instead of rebuilt (1.3–2.5s) per connect,
    # so the first dictation is instant when the preload warmed it. Each session
    # still gets its own decode stream below.
    backend = get_sherpa_dictation_backend(spec.id)
    # Build the recognizer off the event loop if it isn't warm yet
    # (download-on-first-use + ONNX session init can take a moment); status
    # frames keep the widget honest.
    if not await _sherpa_load_with_status(websocket, backend, spec):
        return
    rec = backend._rec
    stream = rec.create_stream()

    last_partial = ""
    committed: list[str] = []     # finalized utterances this session
    session_pcm = RecoveryTail(pcm_sr)   # bounded audio for silent-model recovery
    heard_speech = False
    client_disconnected = False

    async def _send(payload) -> bool:
        nonlocal client_disconnected
        if client_disconnected:
            return False
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            client_disconnected = True
            return False

    def _decode_after_feed(pcm: bytes):
        """Blocking: feed one PCM frame, decode, return (text, is_endpoint).
        Runs in a thread so the ONNX work never blocks the event loop."""
        samples = _pcm16_to_f32(pcm)
        if len(samples):
            stream.accept_waveform(pcm_sr, samples)
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        endpoint = rec.is_endpoint(stream)
        text = (rec.get_result(stream) or "").strip()
        return text, endpoint

    def _flush_final():
        """Blocking: pad + drain the stream for the trailing utterance."""
        tail = np.zeros(int(0.5 * pcm_sr), dtype=np.float32)
        stream.accept_waveform(pcm_sr, tail)
        stream.input_finished()
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        return (rec.get_result(stream) or "").strip()

    try:
        while True:
            kind, pcm = await _recv_pcm_frame(websocket, aec)
            if kind == "eof":
                break
            if kind == "skip":
                continue
            session_pcm.extend(pcm)
            if not heard_speech and _pcm16_rms(pcm) >= SHERPA_OFFLINE_RMS_FLOOR:
                heard_speech = True
            text, endpoint = await asyncio.to_thread(_decode_after_feed, pcm)
            if endpoint:
                # Commit this utterance (polished — it gets pasted); reset
                # for the next one.
                text = polish_text(text)
                if text:
                    committed.append(text)
                    await _send({"type": "final", "text": text,
                                 "final_kind": "utterance",
                                 "segments": [{"start": 0.0, "end": None, "text": text}],
                                 "language": "auto", "engine": backend.id})
                rec.reset(stream)
                last_partial = ""
            elif text and text != last_partial:
                last_partial = text
                await _send({"type": "partial", "text": text})
    except WebSocketDisconnect:
        client_disconnected = True
    except Exception as e:
        logger.warning("sherpa streaming loop ended: %s", e)
        client_disconnected = True

    # Drain the trailing (un-endpointed) utterance on EOF.
    try:
        tail_text = await asyncio.to_thread(_flush_final)
    except Exception as e:
        logger.debug("sherpa streaming flush failed: %s", e)
        tail_text = ""
    tail_text = polish_text(tail_text)
    if tail_text and tail_text != (committed[-1] if committed else None):
        committed.append(tail_text)

    # Pieces are already polished; the join is too (polish is idempotent).
    full = " ".join(t for t in committed if t).strip()
    segments = [{"start": 0.0, "end": None, "text": t} for t in committed if t]

    model_silent = is_model_silent(full, heard_speech, session_pcm.total_bytes)
    if model_silent:
        recovered, recovered_segments = await _recover_silent_sherpa(
            spec, session_pcm.tail(), pcm_sr,
        )
        if recovered:
            full = recovered
            segments = recovered_segments

    if not client_disconnected:
        payload = {"type": "final", "text": full, "final_kind": "summary",
                   "segments": segments,
                   "language": "auto", "engine": backend.id}
        if model_silent:
            payload["engine"] = "capture-asr-fallback" if full else backend.id
            payload["model_silent"] = spec.id
            payload["warning"] = (
                f"The selected dictation model ({spec.id}) produced no text from your "
                "speech. Switched to the fallback engine for this session — pick a "
                "different model in Settings → Dictation."
            )
        if full:
            # Hard-bounded refinement (~4s): never delays this summary `final`
            # beyond OMNIVOICE_REFINE_TIMEOUT_S even with a dead LLM endpoint.
            try:
                from services.refinement import maybe_refine_async
                refined = await maybe_refine_async(full)
            except Exception:
                refined = None
            if refined and refined != full:
                payload["refined_text"] = refined
        await _send(payload)
        try:
            await websocket.close()
        except Exception:
            pass


async def _run_sherpa_offline(websocket: WebSocket, spec):
    """Offline-kind sherpa model with live partials, utterance-windowed.

    Raw PCM accumulates in a *live* buffer holding only the current
    (uncommitted) utterance. Every ~800ms the live window is re-decoded for a
    ``partial``; when the trailing ~0.6s of it fall below the RMS floor the
    utterance is committed — decoded once more, flushed as a ``final``, and
    its samples dropped — so per-partial cost is bounded by one utterance
    (not the whole session) and sentences commit as the user pauses instead
    of only at EOF."""
    from services.asr_backend import get_sherpa_dictation_backend

    pcm_sr, aec = await _sherpa_session(websocket)
    logger.info("sherpa offline dictation: model=%s sr=%d aec=%s",
                spec.id, pcm_sr, bool(aec))

    # Shared, per-model warm backend (#888) — built once, reused per session.
    backend = get_sherpa_dictation_backend(spec.id)
    if not await _sherpa_load_with_status(websocket, backend, spec):
        return

    buf = bytearray()             # live (uncommitted) PCM only
    committed: list[str] = []     # polished utterances already flushed
    last_partial = ""
    # Silent-model guard (#1175 follow-up): a sherpa model can load cleanly and
    # still decode NOTHING — the NeMo-TDT path does exactly this on some builds
    # (parakeet-tdt v2/v3 return an empty token list for clear speech, while
    # whisper/zipformer transcribe the same bytes). Keep the whole session's
    # audio and whether any of it was speech-level, so the finaliser can tell
    # "user said nothing" (fine) from "model produced nothing" (broken).
    session_pcm = RecoveryTail(pcm_sr)
    heard_speech = False
    running = True
    client_disconnected = False
    last_audio = time.monotonic()
    # Trailing-silence gate window, in bytes of int16 mono PCM.
    sil_bytes = max(2, int(SHERPA_OFFLINE_SILENCE_S * pcm_sr) * 2)

    async def _send(payload) -> bool:
        nonlocal client_disconnected
        if client_disconnected:
            return False
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            client_disconnected = True
            return False

    def _decode_window(pcm: bytes) -> str:
        samples = _pcm16_to_f32(pcm)
        if not len(samples):
            return ""
        return backend._decode_offline(samples, pcm_sr)

    async def receive():
        nonlocal running, client_disconnected, last_audio, heard_speech
        try:
            while running:
                kind, pcm = await _recv_pcm_frame(websocket, aec)
                if kind == "eof":
                    running = False
                    break
                if kind == "skip":
                    continue
                buf.extend(pcm)
                session_pcm.extend(pcm)
                if not heard_speech and _pcm16_rms(pcm) >= SHERPA_OFFLINE_RMS_FLOOR:
                    heard_speech = True
                last_audio = time.monotonic()
        except WebSocketDisconnect:
            client_disconnected = True
            running = False
        except Exception as e:
            logger.debug("sherpa offline receive ended: %s", e)
            running = False

    async def _commit(snapshot: bytes):
        """Finalize one utterance: decode it off-thread, flush a polished
        `final`, drop its samples from the live buffer. `receive()` may
        append while we decode — only the snapshot's prefix is dropped."""
        nonlocal last_partial
        try:
            text = await asyncio.to_thread(_decode_window, snapshot)
        except Exception as e:
            logger.debug("sherpa offline commit decode failed: %s", e)
            return
        del buf[:len(snapshot)]
        last_partial = ""
        text = polish_text(text)
        if text:
            committed.append(text)
            await _send({"type": "final", "text": text,
                         "final_kind": "utterance",
                         "segments": [{"start": 0.0, "end": None, "text": text}],
                         "language": "auto", "engine": backend.id})

    async def partials():
        nonlocal last_partial, running
        while running:
            await asyncio.sleep(SHERPA_OFFLINE_PARTIAL_S)
            if not running or len(buf) < 2000:
                continue
            snapshot = bytes(buf)
            if len(snapshot) > sil_bytes and \
                    _pcm16_rms(snapshot[-sil_bytes:]) < SHERPA_OFFLINE_RMS_FLOOR:
                if _pcm16_rms(snapshot[:-sil_bytes]) >= SHERPA_OFFLINE_RMS_FLOOR:
                    await _commit(snapshot)
                else:
                    # Pure silence — drop it (keep the gate window for
                    # continuity) so a long pause can't grow the buffer.
                    del buf[:len(snapshot) - sil_bytes]
                continue
            try:
                text = await asyncio.to_thread(_decode_window, snapshot)
            except Exception as e:
                logger.debug("sherpa offline partial failed: %s", e)
                continue
            if text and text != last_partial:
                last_partial = text
                await _send({"type": "partial", "text": text})

    recv_task = asyncio.create_task(receive())
    part_task = asyncio.create_task(partials())
    await asyncio.wait([recv_task, part_task], return_when=asyncio.FIRST_COMPLETED)
    running = False
    for t in (recv_task, part_task):
        if not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    # Drain the trailing (un-committed) utterance on EOF.
    try:
        tail = await asyncio.to_thread(_decode_window, bytes(buf))
    except Exception:
        logger.exception("sherpa offline final failed")
        tail = ""
    tail = polish_text(tail)
    if tail:
        committed.append(tail)
    # Pieces are already polished; the join is too (polish is idempotent).
    full = " ".join(committed).strip()
    segments = [{"start": 0.0, "end": None, "text": t} for t in committed]

    # Silent-model fallback: we heard speech-level audio but the selected
    # sherpa model returned nothing at all. That is a broken engine, not a
    # quiet user — hand the session to the capture ASR backend so the user
    # still gets their words, and say which model let them down. Bounded to
    # this session; the pref is left alone so the user stays in control.
    model_silent = is_model_silent(full, heard_speech, session_pcm.total_bytes)
    if model_silent:
        recovered, recovered_segments = await _recover_silent_sherpa(
            spec, session_pcm.tail(), pcm_sr,
        )
        if recovered:
            full = recovered
            segments = recovered_segments

    if not client_disconnected:
        payload = {"type": "final", "text": full, "final_kind": "summary",
                   "segments": segments,
                   "language": "auto", "engine": backend.id}
        if model_silent:
            # The client surfaces this so a silently-broken model can't look
            # like "dictation is just broken" ever again.
            payload["engine"] = "capture-asr-fallback" if full else backend.id
            payload["model_silent"] = spec.id
            payload["warning"] = (
                f"The selected dictation model ({spec.id}) produced no text from your "
                "speech. Switched to the fallback engine for this session — pick a "
                "different model in Settings → Dictation."
            )
        if full:
            # Hard-bounded refinement (~4s) — never delays the `final`.
            try:
                from services.refinement import maybe_refine_async
                refined = await maybe_refine_async(full)
                if refined and refined != full:
                    payload["refined_text"] = refined
            except Exception:
                pass
        await _send(payload)
        try:
            await websocket.close()
        except Exception:
            pass


def _result_text(result: dict | None) -> str:
    """Normalize text from every ASR backend result shape.

    Some backends return a top-level ``text`` value, while WhisperX, Faster
    Whisper, Moonshine, and OpenAI-compatible ASR expose only ``segments`` and
    ``chunks``. Dictation partials and finals must interpret both contracts the
    same way.
    """
    if not isinstance(result, dict):
        return ""

    text = result.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    for key in ("segments", "chunks"):
        items = result.get(key)
        if not isinstance(items, (list, tuple)):
            continue
        text = " ".join(
            str(item.get("text", "")).strip()
            for item in items
            if isinstance(item, dict) and item.get("text")
        ).strip()
        if text:
            return text
    return ""


async def _transcribe_buffer(
    chunks: list[bytes], *, pcm_sr: int | None = None, dictation: bool = False,
) -> str:
    """Quick partial transcription of the current audio buffer.

    ``dictation=True`` applies the saved dictation vocabulary hint; other
    callers of this helper (e.g. the phone-call agent) stay unbiased by it.
    """

    tmp = _pcm16_to_wav(b"".join(chunks), pcm_sr) if pcm_sr else _chunks_to_wav(chunks)
    if tmp is None:
        return ""

    try:
        from services.model_manager import _gpu_pool
        from services.asr_backend import get_capture_asr_backend, run_transcribe_guarded
        from api.routers.dictation import dictation_transcribe_kwargs

        def _run():
            backend = get_capture_asr_backend()
            prompt_kwargs = dictation_transcribe_kwargs(backend) if dictation else {}
            result = backend.transcribe(tmp, word_timestamps=False, **prompt_kwargs)
            return _result_text(result)

        # Bound dictation transcribes (#730): a wedged whisperx/CTranslate2 call
        # must not hold its GPU-pool worker forever and starve TTS / other ASR
        # into a "can't reach backend"; on timeout the pool is reset to recover.
        text = await run_transcribe_guarded(_gpu_pool, _run, what="Dictation")
        return text.strip()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


async def _transcribe_buffer_full(
    chunks: list[bytes], *, pcm_sr: int | None = None, skip_sherpa: bool = False,
    dictation: bool = False,
) -> dict:
    """Full transcription with timing info for the final result.

    ``dictation=True`` applies the saved dictation vocabulary hint. The
    silent-model rescue (``skip_sherpa``) leaves it off: its text decides
    whether a sherpa model is demoted, and Whisper can echo a prompt on noise,
    so that text must come from the audio alone.
    """
    tmp = _pcm16_to_wav(b"".join(chunks), pcm_sr) if pcm_sr else _chunks_to_wav(chunks)
    if tmp is None:
        return {"text": "", "segments": [], "language": "unknown",
                "duration_s": 0, "transcription_time_s": 0, "engine": "none"}

    try:
        from services.model_manager import _gpu_pool
        from services.asr_backend import get_capture_asr_backend, run_transcribe_guarded
        from api.routers.dictation import dictation_transcribe_kwargs

        def _run():
            backend = get_capture_asr_backend(skip_sherpa=skip_sherpa)
            prompt_kwargs = dictation_transcribe_kwargs(backend) if dictation else {}
            t0 = time.perf_counter()
            result = backend.transcribe(tmp, word_timestamps=False, **prompt_kwargs)
            elapsed = round(time.perf_counter() - t0, 2)

            segments = result.get("segments", [])
            full_text = _result_text(result)

            # Wave 1.1: strip Whisper hallucination loops from the final
            # text (the string that gets auto-pasted). Segments keep the
            # raw recognition so their timings stay truthful.
            from services.refinement import collapse_repetitive_artifacts
            full_text = collapse_repetitive_artifacts(full_text)

            # end=None means the engine could not determine the timing — this
            # module writes exactly that in its own streaming payloads, and
            # sherpa's _sherpa_result does too when the sample rate yields no
            # duration. Measure only real numbers, and pass the nulls through
            # rather than rounding them (#1904).
            ends = [e for e in (s.get("end") for s in segments) if isinstance(e, (int, float))]
            duration = max(ends) if ends else 0.0

            return {
                "text": full_text,
                "segments": [
                    {"start": _timing(s.get("start", 0)),
                     "end": _timing(s.get("end", 0)),
                     "text": s.get("text", "").strip()}
                    for s in segments
                ],
                "language": result.get("language", "unknown"),
                "duration_s": round(duration, 2),
                "transcription_time_s": elapsed,
                "engine": backend.id,
            }

        # Bounded + pool-resetting on timeout (#730), same rationale as the
        # partial path above.
        return await run_transcribe_guarded(_gpu_pool, _run, what="Dictation")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _chunks_to_wav(chunks: list[bytes]) -> str | None:
    """Concatenate audio chunks and write to a temp WAV file.

    Handles both raw PCM (from AudioWorklet) and WebM/Opus blobs
    (from MediaRecorder) by converting through ffmpeg.

    Falls back to saving raw WebM if ffmpeg conversion fails — the ASR
    backends (MLX Whisper, WhisperX) can decode WebM/Opus natively.
    """
    if not chunks:
        return None

    blob = b"".join(chunks)
    if len(blob) < 100:
        return None

    # Write blob to temp file
    tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".webm")
    tmp_in.write(blob)
    tmp_in.close()

    tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp_out.close()

    try:
        from services.ffmpeg_utils import find_ffmpeg, local_inputs_only
        import subprocess
        subprocess.run(
            local_inputs_only([find_ffmpeg(), "-y", "-i", tmp_in.name,
             "-ar", "16000", "-ac", "1", "-f", "wav", tmp_out.name]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=True,
        )
        # ffmpeg succeeded — clean up input, return WAV
        try:
            os.unlink(tmp_in.name)
        except OSError:
            pass
        return tmp_out.name
    except Exception as e:
        logger.debug("ffmpeg conversion failed: %s", e)
        try:
            os.unlink(tmp_out.name)
        except OSError:
            pass
        # Fallback: return the raw WebM — ASR backends (MLX Whisper,
        # WhisperX) can decode WebM/Opus containers natively.
        logger.debug("Falling back to raw WebM input for ASR")
        return tmp_in.name
