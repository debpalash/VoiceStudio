"""Supertonic-3 sidecar entry point (Phase 3 Plan 03-01).

Runs in the OmniVoice parent venv (no dedicated venv ‑‑ the ``supertonic``
SDK's transitive deps ``onnxruntime``, ``numpy``, ``soundfile``,
``huggingface_hub`` are already present at the parent's pins). Spawned by
:class:`backend.engines.supertonic3.backend.Supertonic3Backend` through
the Phase 2 ``SubprocessBackend`` primitive.

Wire protocol ‑‑ length-prefixed JSON over stdin/stdout, byte-identical to
``backend/services/subprocess_backend.py``::

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

Op flow expected by the parent:

  1. Sidecar -> parent: {"op": "ready", "engine": "supertonic3",
                         "sample_rate": 44100, "version": "<sdk-version>"}
     Model NOT yet loaded ‑‑ that happens lazily on the first synthesize
     op so we comfortably make ``SubprocessBackend.SPAWN_READY_TIMEOUT_S``.

  2. Optional: parent -> sidecar: {"op": "ping"}
                  sidecar -> parent: {"op": "pong"}

  3. Parent -> sidecar: {"op": "synthesize", "text": "...",
                          "voice": "M1", "lang": "en",
                          "speed": 1.0, "total_steps": 8}
     One or more {"op": "progress", "stage": "loading_model",
                  "percent": N} frames may be emitted during the cold
     ``snapshot_download`` + SDK init on the *first* call only. Then:
                  sidecar -> parent: {"op": "audio",
                                      "audio_pcm_b64": "<base64 int16>",
                                      "sample_rate": 44100,
                                      "n_samples": N}

  4. Parent -> sidecar: {"op": "shutdown"} -> exit 0
  5. Unknown op -> {"op": "error", "stage": "dispatch",
                    "message": "unknown op: <op>"} and continue.

Hardware honesty (TTS-04): Supertonic-3 is ONNX/numpy on the CPU EP.
The SDK exposes no CUDA / MPS path. The sidecar never queries
``torch.cuda`` ‑‑ it has no torch import at all. Honest CPU-only
reporting is baked in: there is nothing to mis-claim.

Self-test mode (``--selftest``): import the SDK, resolve the pinned SHA
via ``snapshot_download``, then exit 0. Gated by ``OMNIVOICE_SMOKE=1``
upstream because the snapshot is ~400 MB. Useful for release-prep CI to
verify a wheel + the pinned SHA still resolve as expected.

Security:

  * NO logging of ``os.environ`` contents. Defense in depth against
    accidental token-bytes-on-stderr; the parent's stderr drainer
    additionally pipes everything through the Phase 1
    ``HFTokenRedactor`` filter.
  * NO eval / exec / subprocess in the dispatch loop. The wire frames
    are JSON-only and op dispatch is an explicit allowlist.
  * Single-frame DoS cap matches the parent's ``MAX_FRAME_BYTES`` so a
    malformed inbound frame surfaces as a clean IOError instead of an
    OOM.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import struct
import sys
import traceback
from pathlib import Path

# Stdlib-only at import time. The SDK + numpy + huggingface_hub are
# loaded lazily inside ``_load_tts`` on the first synthesize op so the
# sidecar emits its ``ready`` frame inside the 30 s spawn handshake even
# on a cold filesystem.


# Mirrors backend/services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES: int = 64 * 1024 * 1024

#: Native sample rate Supertonic-3 emits. Advertised in the ready frame
#: so the parent doesn't have to import the SDK just to learn the rate.
SUPERTONIC_SAMPLE_RATE: int = 44100

logger = logging.getLogger("supertonic3.sidecar")


# ── wire protocol ─────────────────────────────────────────────────────────


def _send(stream, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("!I", len(body)))
    stream.write(body)
    stream.flush()


def _recv(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None  # EOF
    (n,) = struct.unpack("!I", header)
    if n > MAX_FRAME_BYTES:
        raise IOError(f"frame too large: {n}")
    body = bytearray()
    while len(body) < n:
        chunk = stream.read(n - len(body))
        if not chunk:
            raise IOError("short read")
        body.extend(chunk)
    return json.loads(bytes(body).decode("utf-8"))


# ── revision pin ──────────────────────────────────────────────────────────


def _resolve_pinned_sha() -> str:
    """Read the pinned SHA from env (set by the parent) with a constants
    fallback for ``--selftest`` invocations that don't go through
    SubprocessBackend.

    The parent injects ``SUPERTONIC3_REVISION`` via ``Supertonic3Backend``'s
    ``extra_env`` (Pattern 1 in 03-RESEARCH.md / Plan 03-01 Task 3). When
    the env var is missing (selftest from the CLI, ad-hoc dev), we fall
    back to the in-tree constant so behaviour is identical.
    """
    sha = os.environ.get("SUPERTONIC3_REVISION")
    if sha:
        return sha
    # Defer the constants import so this file stays stdlib-importable for
    # the ``--selftest`` ImportError surfacing path.
    try:
        from backend.engines.supertonic3.constants import PINNED_REVISION_SHA
        return PINNED_REVISION_SHA
    except ImportError:
        # Final fallback ‑‑ relative import for when the file is invoked
        # via ``python backend/engines/supertonic3/sidecar.py`` rather
        # than via ``python -m backend.engines.supertonic3.sidecar``.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from engines.supertonic3.constants import PINNED_REVISION_SHA  # type: ignore[import-not-found]
        return PINNED_REVISION_SHA


# ── model loading (lazy, on first synthesize) ─────────────────────────────


# Module-level singleton ‑‑ populated on the first synthesize op and reused
# for every subsequent request in this sidecar's lifetime.
_tts = None


def _load_tts(stdout) -> object:
    """Cold-construct ``supertonic.TTS`` from the pinned snapshot.

    Emits ``progress`` frames at 0/50/100% so the parent can surface the
    400 MB download latency (Pitfall 7 in 03-RESEARCH.md). On failure
    raises ‑‑ the caller emits an ``error`` frame for the in-flight
    synthesize op and continues the dispatch loop.

    SDK behaviour (verified against ``supertonic==1.3.1`` wheel):
        ``TTS()`` accepts ``model_dir=`` (Path | str) pointing at a
        directory that already contains the ONNX weights. We pre-fetch
        via ``snapshot_download`` so the revision we resolve is exactly
        the SHA we pinned ‑‑ the SDK's own default is the same SHA but
        the explicit ``revision=`` argument makes this defence-in-depth.
    """
    global _tts
    if _tts is not None:
        return _tts

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})

    # Lazy imports ‑‑ keeps the ready frame fast.
    from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
    from supertonic import TTS  # type: ignore[import-not-found]

    revision = _resolve_pinned_sha()
    # Pin by SHA (TTS-03). ``snapshot_download`` is idempotent + uses
    # ``HF_HUB_CACHE`` / ``HF_HOME`` / ``HF_ENDPOINT`` already forwarded
    # by the SubprocessBackend env contract.
    model_path = snapshot_download(
        repo_id="Supertone/supertonic-3",
        revision=revision,
    )

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 50})

    _tts = TTS(model="supertonic-3", model_dir=model_path, auto_download=False)

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _tts


def _wav_float_to_pcm_b64(wav, sample_rate: int) -> tuple[str, int, int]:
    """Convert a mono float32 numpy array to base64 int16 PCM.

    Returns ``(b64_pcm, sample_rate, n_samples)``. The SDK emits a
    float32 mono array in [-1, 1]; we clip + scale to int16 and base64
    so the wire frame stays JSON-friendly.
    """
    import numpy as np

    arr = np.asarray(wav, dtype=np.float32).squeeze()
    while arr.ndim > 1:
        # Downmix along whichever axis is the channel axis. Hardcoded to axis 0
        # this averaged across TIME for a channels-last (N, 2) array -- every
        # output sample became the mean of two neighbouring samples, which is
        # not a downmix but a destroyed waveform. (#1328)
        arr = arr.mean(axis=int(np.argmin(arr.shape)))
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(sample_rate), int(arr.shape[0])


def _normalize_lang(raw) -> str | None:
    """Map OmniVoice's language sentinel to the SDK's language codes.

    The SDK accepts ISO-639-1 codes plus ``"na"`` (language-agnostic for
    Supertonic-3). The parent sends either a raw 2-letter code, the
    string ``"auto"`` (OmniVoice's sentinel), or ``None``. All three
    of ``"auto"``, ``""``, ``None`` map to ``"na"`` so the SDK's
    multilingual fallback engages cleanly.
    """
    if raw is None:
        return "na"
    if not isinstance(raw, str):
        return "na"
    s = raw.strip().lower()
    if not s or s == "auto":
        return "na"
    return s[:2]


def _handle_synthesize(msg: dict, stdout) -> None:
    """Dispatch one synthesize request. Emits the audio frame or raises."""
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")

    voice = msg.get("voice") or "M1"
    lang = _normalize_lang(msg.get("lang"))
    speed = float(msg.get("speed", 1.0))
    total_steps = int(msg.get("total_steps", 8))

    tts = _load_tts(stdout)
    # ``get_voice_style`` raises ValueError on an unknown voice ‑‑ the
    # parent already validates against ``VOICE_PRESETS`` and falls back
    # to ``DEFAULT_VOICE``, so this is defence in depth.
    style = tts.get_voice_style(voice_name=voice)

    # The SDK returns ``(wav_np, duration_np)``; we only need the audio.
    wav, _duration = tts.synthesize(
        text=text,
        voice_style=style,
        total_steps=total_steps,
        speed=speed,
        lang=lang,
    )

    pcm_b64, sr, n_samples = _wav_float_to_pcm_b64(wav, getattr(tts, "sample_rate", SUPERTONIC_SAMPLE_RATE))
    _send(stdout, {
        "op": "audio",
        "audio_pcm_b64": pcm_b64,
        "sample_rate": sr,
        "n_samples": n_samples,
    })


# ── selftest ──────────────────────────────────────────────────────────────


def _run_selftest() -> int:
    """Import the SDK + resolve the pinned snapshot. Exit 0 on success.

    Used by release-prep CI to verify the wheel + pinned SHA still
    resolve. The 400 MB download means this is gated by
    ``OMNIVOICE_SMOKE=1`` upstream ‑‑ this function itself just runs the
    full path and returns its exit code.
    """
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
        from supertonic import TTS  # type: ignore[import-not-found]
    except ImportError as exc:
        print(f"selftest: import failed: {exc}", file=sys.stderr)
        return 1

    revision = _resolve_pinned_sha()
    if len(revision) != 40 or not all(c in "0123456789abcdef" for c in revision):
        print(
            f"selftest: PINNED_REVISION_SHA must be 40 hex chars, got {revision!r}",
            file=sys.stderr,
        )
        return 1

    try:
        path = snapshot_download(
            repo_id="Supertone/supertonic-3",
            revision=revision,
        )
    except Exception as exc:  # network / auth / SHA-not-found
        print(f"selftest: snapshot_download failed: {exc}", file=sys.stderr)
        return 1

    try:
        _ = TTS(model="supertonic-3", model_dir=path, auto_download=False)
    except Exception as exc:
        print(f"selftest: TTS init failed: {exc}", file=sys.stderr)
        return 1

    print(f"selftest: ok (revision={revision} path={path})")
    return 0


# ── main loop ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supertonic-3 sidecar")
    parser.add_argument(
        "--selftest", action="store_true",
        help="Import the SDK, resolve the pinned snapshot, exit 0 on success",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return _run_selftest()

    stdin = sys.stdin.buffer
    # Frames go down a PRIVATE fd, and fd 1 is pointed at stderr (#1428).
    #
    # This sidecar's protocol is length-prefixed binary on stdout, but it is
    # not the only thing writing there: the libraries it loads print freely to
    # fd 1 — wetextprocessing's FST logs, tqdm bars, native prints from torch
    # and ONNX runtime. Those bytes interleave with frames, and the parent
    # then reads four bytes of log text as a length prefix, which is how a
    # generation dies with `OSError: frame too large: 1044258881` (that number
    # is ASCII). Worse, it desyncs the stream, so every later request on the
    # same sidecar reads stale bytes and no retry can recover.
    #
    # Duplicating fd 1 first keeps a clean channel only this module can write
    # to; redirecting fd 1 to fd 2 sends the library noise to stderr, which
    # the parent already drains into its own log (through the HF-token
    # redactor). Nothing is lost and the frame stream cannot be corrupted.
    _frame_fd = os.dup(1)
    os.dup2(2, 1)
    stdout = os.fdopen(_frame_fd, "wb")

    # Detect SDK version for the ready frame so the parent's compat table
    # can surface it without re-importing the SDK in-process.
    sdk_version: str | None = None
    try:
        import supertonic  # type: ignore[import-not-found]
        sdk_version = getattr(supertonic, "__version__", None)
    except ImportError:
        # We still emit the ready frame ‑‑ the first synthesize op will
        # raise an explicit ``ImportError`` frame back to the parent.
        sdk_version = None

    _send(stdout, {
        "op": "ready",
        "engine": "supertonic3",
        "sample_rate": SUPERTONIC_SAMPLE_RATE,
        "version": sdk_version,
    })

    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:
            _send(stdout, {
                "op": "error",
                "stage": "recv",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            return 1
        if msg is None:
            return 0

        op = msg.get("op") if isinstance(msg, dict) else None
        try:
            if op == "ping":
                _send(stdout, {"op": "pong"})
            elif op == "synthesize":
                _handle_synthesize(msg, stdout)
            elif op == "shutdown":
                return 0
            else:
                _send(stdout, {
                    "op": "error",
                    "stage": "dispatch",
                    "message": f"unknown op: {op!r}",
                })
        except Exception as exc:
            # Per-op failure is recoverable ‑‑ emit the error frame and
            # stay alive so the parent can retry without paying the
            # respawn + model-load cost.
            _send(stdout, {
                "op": "error",
                "stage": op or "unknown",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })


if __name__ == "__main__":
    sys.exit(main())
