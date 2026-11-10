"""Resident ``tts-server`` mode for the OmniVoice GGUF engine (#3654).

Spawning ``omnivoice-tts`` per request pays process start, backend init and
the LM + codec load every time (~3.5 s on a discrete GPU). omnivoice.cpp's
``tts-server`` (upstream ``tools/tts-server.cpp``; the voice registry and
per-request language arrived in 040c8b344d8c) loads the pair once and serves
an OpenAI-compatible API, so one long-lived process per loaded quant pair
keeps generation at generation time.

Resident mode is only ever an accelerator for the spawn path:

* it is opt-in (off by default) and used only when a verified
  ``tts-server-<platform>`` binary sits next to ``omnivoice-tts-<platform>``
  and the Settings toggle is on;
* requests that need a control the server does not take (duration, chunk
  settings, no-denoise, no-preprocess-prompt, a reference without a
  transcript) go to the spawn path;
* a server that cannot start, or that crashes again after one restart,
  hands the request to the spawn path and the reason is logged;
* the process is stopped on unload (idle release, engine switch, Unload
  all) and when the toggle is turned off, so its VRAM is returned.

The server binds 127.0.0.1 on an ephemeral port. Upstream has no API key and
answers any origin (CORS ``*``), so while it runs other programs on this
computer can reach it; that is why it is opt-in. The process is identified
by its PID being alive plus the model id it reports (the base GGUF file
name).
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import logging
import os
import socket
import subprocess  # nosec B404 -- executable and args are resolved locally
import threading
import time
import urllib.error
import urllib.request
import weakref
from pathlib import Path
from typing import Optional

from core.contained_subprocess import spawn_owned

logger = logging.getLogger("omnivoice.gguf")

#: prefs.json key and environment override for the Settings toggle.
RESIDENT_PREF = "omnivoice_gguf_resident"
RESIDENT_ENV = "OMNIVOICE_GGUF_RESIDENT"

#: Seconds the server may take to load the LM + codec before it is abandoned.
_READY_TIMEOUT_S = 120.0
_POLL_S = 0.25

#: Generation controls only the per-request CLI accepts. A request that sets
#: one of them keeps its exact behaviour by taking the spawn path.
_SPAWN_ONLY_CONTROLS = ("duration", "chunk_duration", "chunk_threshold")


class ResidentUnavailable(RuntimeError):
    """The resident server could not serve this request; use the spawn path."""


def server_binary_path() -> Path:
    """``tts-server-<platform>`` next to the verified ``omnivoice-tts`` binary."""
    from . import backend

    slug = backend._platform_slug()
    name = "tts-server-" + slug
    if slug.startswith("windows"):
        name += ".exe"
    return backend._binary_path().parent / name


_STATUS_CACHE: dict[tuple[str, tuple[int, int] | None], tuple[bool, str]] = {}


def server_status() -> tuple[bool, str]:
    """Whether a usable ``tts-server`` binary is present. Never raises.

    The same integrity rules as the CLI apply: an executable magic, the
    ``bin/checksums.sha256`` digest when a manifest exists (an installer
    manifest that does not list the server refuses it), no macOS quarantine.
    Results are cached per file identity so a request never re-hashes.
    """
    from . import backend

    try:
        path = server_binary_path()
        if not path.is_file():
            return False, "no tts-server binary is bundled"
        key = (str(path), backend._file_identity(path))
        cached = _STATUS_CACHE.get(key)
        if cached is not None:
            return cached
        result = _verify_server_binary(path)
        _STATUS_CACHE.clear()
        _STATUS_CACHE[key] = result
        return result
    except Exception as exc:  # noqa: BLE001 -- status must never raise
        return False, f"{type(exc).__name__}: {exc}"


def _verify_server_binary(path: Path) -> tuple[bool, str]:
    from services.binary_preflight import looks_like_executable

    from . import backend

    ok, why = looks_like_executable(path)
    if not ok:
        return False, f"{path.name} is not a usable executable: {why}"
    try:
        manifest = backend._load_checksum_manifest()
    except backend.ChecksumManifestError as exc:
        return False, f"{path.name} could not be verified: {exc}"
    if manifest:
        expected = manifest.get(path.name)
        if expected is None:
            return False, f"{path.name} is not listed in bin/checksums.sha256"
        if backend._sha256_of_file(path).lower() != expected.lower():
            return False, f"{path.name} does not match bin/checksums.sha256"
    if backend._is_macos_quarantined(path):
        return False, f"{path.name} is quarantined by macOS Gatekeeper"
    if os.name == "posix" and not os.access(path, os.X_OK):
        try:
            path.chmod(path.stat().st_mode | 0o111)
        except OSError:
            return False, f"{path.name} is not executable"
    return True, "ready"


def resident_enabled() -> bool:
    """Env ``OMNIVOICE_GGUF_RESIDENT`` > prefs.json > off. Read per call.

    Opt-in: upstream ``tts-server`` has no API key and sends
    ``Access-Control-Allow-Origin: *``, so it must not run unasked.
    """
    try:
        from core import prefs

        value = prefs.resolve(RESIDENT_PREF, env=RESIDENT_ENV, default=False)
    except Exception:  # noqa: BLE001 -- an unreadable setting keeps the default
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def spawn_only_reason(kw: dict, *, has_transcript: bool) -> Optional[str]:
    """Why this request must use the per-request CLI, or ``None``."""
    for name in _SPAWN_ONLY_CONTROLS:
        if kw.get(name) is not None:
            return f"{name} is not accepted by tts-server"
    if kw.get("denoise", True) is False:
        return "no-denoise is not accepted by tts-server"
    if kw.get("preprocess_prompt", True) is False:
        return "no-preprocess-prompt is not accepted by tts-server"
    if kw.get("ref_audio") and not has_transcript:
        return "tts-server needs a transcript to register a cloned voice"
    return None


_LIVE: "weakref.WeakSet[ResidentServer]" = weakref.WeakSet()


def stop_all() -> None:
    """Stop every resident server (toggle turned off, or app exit).

    Waits for an in-flight request on each server, so a request cannot
    restart a server this call has just stopped.
    """
    for server in list(_LIVE):
        with server.lock:
            server.stop()


atexit.register(stop_all)


class ResidentServer:
    """One loopback ``tts-server`` process for one base + tokenizer pair."""

    def __init__(self):
        self.process = None
        self.port: Optional[int] = None
        self.pair: Optional[tuple[str, str]] = None
        self.voices: dict[str, str] = {}
        self.log_handle = None
        #: (binary identity, pair) whose start failed: skip straight to the
        #: spawn path instead of paying a failing start on every request.
        self.failed_start: Optional[tuple] = None
        self.lock = threading.RLock()
        _LIVE.add(self)

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    # ── lifecycle ───────────────────────────────────────────────────────

    def ensure(self, base: Path, tokenizer: Path) -> None:
        """Start the server for this pair unless it is already serving it."""
        from . import backend

        pair = (str(base), str(tokenizer))
        if self.alive() and self.pair == pair:
            return
        self.stop()
        binary = server_binary_path()
        attempt = (backend._file_identity(binary), pair)
        if self.failed_start == attempt:
            raise ResidentUnavailable("tts-server failed to start earlier for this quant")
        self.port = _free_port()
        args = [
            str(binary),
            "--model", str(base),
            "--codec", str(tokenizer),
            "--host", "127.0.0.1",
            "--port", str(self.port),
        ]
        log_path = _log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = log_path.open("ab")
        try:
            self.process = spawn_owned(
                args,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=backend._spawn_env(),
                # Same rule as the CLI: ggml's backend scan stays inside bin/.
                cwd=str(binary.parent),
            )
            self.pair = pair
            self._wait_ready(Path(base).name)
        except Exception as exc:
            self.stop()
            self.failed_start = attempt
            raise ResidentUnavailable(f"tts-server did not start: {exc}") from exc
        self.failed_start = None
        logger.info("OmniVoice GGUF resident server ready on port %d (%s)", self.port, Path(base).name)

    def _wait_ready(self, model_id: str) -> None:
        deadline = time.monotonic() + _READY_TIMEOUT_S
        last = "no response"
        while time.monotonic() < deadline:
            if not self.alive():
                raise RuntimeError(f"it exited during start-up; see {_log_path().name}")
            try:
                rows = self._json("GET", "/v1/models", timeout=2).get("data", [])
            except (OSError, ValueError) as exc:  # still loading the pair
                last = str(exc)
                time.sleep(_POLL_S)
                continue
            if not any(isinstance(r, dict) and r.get("id") == model_id for r in rows):
                raise RuntimeError("the port is not served by this process")
            # The voice registry (040c8b344d8c+) is what cloning needs; an
            # older server answers 404 here.
            try:
                self._json("GET", "/v1/audio/voices", timeout=2)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(
                    f"it has no voice registry (HTTP {exc.code}); omnivoice.cpp 040c8b344d8c or newer is needed"
                ) from None
            return
        raise TimeoutError(f"not ready after {_READY_TIMEOUT_S:.0f}s ({last})")

    def stop(self) -> None:
        """Stop the process and forget its voices. Idempotent."""
        proc, self.process = self.process, None
        self.pair = None
        self.port = None
        self.voices.clear()
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (OSError, subprocess.SubprocessError) as exc:
                    logger.debug("Unable to reap the GGUF tts-server: %s", exc)
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None

    # ── protocol ────────────────────────────────────────────────────────

    def _send(self, method: str, path: str, payload: Optional[dict], timeout: float) -> bytes:
        if not self.alive():
            raise ConnectionError("the GGUF tts-server is not running")
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        if payload is not None:
            request.add_header("Content-Type", "application/json")
            request.data = json.dumps(payload).encode("utf-8")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:  # nosec B310 loopback only
            return response.read()

    def _json(self, method: str, path: str, payload: Optional[dict] = None, timeout: float = 30) -> dict:
        result = json.loads(self._send(method, path, payload, timeout).decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("the GGUF tts-server returned malformed JSON")
        return result

    def voice_for(self, data: bytes, transcript: str) -> str:
        """Register the clip + transcript once; return its server-side name."""
        digest = hashlib.sha256()
        digest.update(data)
        digest.update(b"\0")
        digest.update(transcript.encode("utf-8"))
        key = digest.hexdigest()
        name = self.voices.get(key)
        if name is not None:
            return name
        name = "vs-" + key[:32]
        self._json("POST", "/v1/audio/voices", {
            "name": name,
            "ref_text": transcript,
            "wav_b64": base64.b64encode(data).decode("ascii"),
        }, timeout=120)
        self.voices[key] = name
        return name

    def speak(self, payload: dict, timeout: float) -> bytes:
        body = self._send("POST", "/v1/audio/speech", payload, timeout)
        if not body.startswith(b"RIFF"):
            raise ValueError("the GGUF tts-server did not return a WAV")
        return body


def speech_payload(text: str, *, voice: Optional[str], language: Optional[str],
                   instruct: Optional[str], seed) -> dict:
    """The ``/v1/audio/speech`` body for one request (WAV, one shot)."""
    # The CLI trims trailing newlines from stdin; keep the same input.
    payload: dict = {"input": text.rstrip("\r\n"), "response_format": "wav"}
    if voice:
        payload["voice"] = voice
    if language:
        payload["language"] = language
    if isinstance(instruct, str) and instruct:
        payload["instructions"] = instruct
    if seed is not None:
        payload["seed"] = int(seed)
    return payload


def _log_path() -> Path:
    from core.config import DATA_DIR

    return Path(DATA_DIR) / "engines" / "omnivoice-gguf" / "tts-server.log"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
