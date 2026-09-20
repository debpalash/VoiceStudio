"""audio.cpp TTS backend — Breeze-TTS-2 via a managed native server.

audio.cpp (0xShug0/audio.cpp) is a pure-C++ ggml runtime: prebuilt
``audiocpp_server`` binaries for Windows/macOS/Linux, no Python venv, no
``transformers`` pin — so this engine needs neither the venv-isolation
(``engines.dots_tts``) nor the per-generate CLI-spawn (``engines
.omnivoice_gguf``) patterns. The parent instead:

1. resolves the binary + GGUF model (``bootstrap.py``),
2. spawns ONE long-lived ``audiocpp_server`` on 127.0.0.1 (lazy model load,
   so model memory is only held after the first generate), and
3. speaks its OpenAI-style ``POST /v1/audio/speech`` per generate.

v1 serves the ``breeze_tts`` family only (Breeze-TTS-2, en+zh, voice clone
+ voice design + voice direction). The server is task-agnostic on the
speech route — reference-audio presence selects clone/direction vs design —
so a single ``task: tts`` model entry covers all three modes.

License honesty: Breeze-TTS-2 weights (``BreezeBlue/Breeze-TTS-2`` and the
audio.cpp GGUF repack) are RESEARCH AND NON-COMMERCIAL ONLY
(``BreezeBlue Research and Non-Commercial License``); only the audio.cpp
code is Apache-2.0. There is no in-tree acceptance dialog for this engine
yet (settings ``/license`` allow-list), so the restriction is surfaced in
the display name, the install hint, and ``docs/engines/audio-cpp.md`` —
not silently.
"""
from __future__ import annotations

import atexit
import base64
import io
import json
import logging
import os
import secrets

# Used only for stream constants; spawn_owned performs the process launch.
import subprocess  # nosec B404
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.contained_subprocess import spawn_owned
from services.tts_backend import TTSBackend, TTSInputError

if TYPE_CHECKING:
    import torch

logger = logging.getLogger("omnivoice.audiocpp")

#: Engine id in the TTS registry.
ENGINE_ID = "audiocpp"

#: How long to wait for ``/health`` after spawning the server (first spawn
#: extracts nothing heavy — the model loads lazily on first generate).
_HEALTH_TIMEOUT_S = 120.0

#: Finish the inner HTTP request before the canonical generation guard can
#: abandon its worker thread. This leaves enough time to terminate the owned
#: native process and release its model memory synchronously.
_TERMINATE_GRACE_S = 5.0
_TERMINATE_KILL_S = 5.0
_GENERATE_TIMEOUT_MARGIN_S = (
    _TERMINATE_GRACE_S + _TERMINATE_KILL_S + 5.0
)


# ── pure request/config builders (unit-tested, no I/O) ──────────────────────


def _cpu_thread_count() -> int:
    """Use up to 16 physical cores, with a stdlib fallback."""
    try:
        import psutil

        cores = psutil.cpu_count(logical=False)
    except (ImportError, OSError):
        cores = None
    return min(16, max(1, cores or os.cpu_count() or 1))


def _device_min_vram_gb(device) -> float:
    """Dedicated-memory comfort floor for one discovered native device."""
    return 6.0 if (
        device
        and device.kind == "GPU"
        and (
            device.backend == "vulkan"
            or device.hardware_family in {"cuda", "rocm"}
        )
    ) else 0.0


def build_server_config(
    *, model_id: str, family: str, model_path: str, port: int,
    backend: str = "cpu", device: int = 0,
    execution_target: str | None = None,
) -> dict:
    """``server.json`` dict for the managed ``audiocpp_server``.

    ``lazy_load`` defers the ~4.73 GiB GGUF load to the first generate;
    ``max_loaded_models: 1`` bounds residency to the one model we serve.
    """
    return {
        "host": "127.0.0.1",
        "port": port,
        "backend": backend,
        "device": device,
        # The pinned CPU runtime scales strongly through 16 workers while
        # producing byte-identical audio.
        "threads": _cpu_thread_count()
        if (execution_target or backend) == "cpu" else 1,
        "lazy_load": True,
        "max_loaded_models": 1,
        "models": [
            {
                "id": model_id,
                "family": family,
                "path": model_path,
                "task": "tts",
                "mode": "offline",
            }
        ],
    }


def build_speech_payload(
    *, model_id: str, text: str, ref_audio: str | None = None,
    ref_text: str | None = None, instructions: str | None = None,
    guidance_scale: float | None = None, seed: int | None = None,
) -> dict:
    """``POST /v1/audio/speech`` JSON body.

    Field spellings verified against ``app/server/runtime.cpp``
    (``build_speech_request``): ``instructions`` (plural, OpenAI spelling)
    feeds the ``instruction`` request option; ``reference_text`` and
    ``guidance_scale``/``seed`` pass through top-level; ``voice_ref`` takes
    a ``{"type": "path", ...}`` object so the reference stays on disk
    (the 5 MiB base64 cap never bites). ``response_format: json`` returns
    the WAV base64-in-JSON — one round trip, no binary framing.
    """
    payload: dict[str, Any] = {
        "model": model_id,
        "input": text,
        "response_format": "json",
    }
    if instructions:
        payload["instructions"] = instructions
    if ref_audio:
        payload["voice_ref"] = {"type": "path", "path": str(ref_audio)}
        if ref_text:
            payload["reference_text"] = ref_text
    if guidance_scale is not None:
        payload["guidance_scale"] = float(guidance_scale)
    if seed is not None:
        payload["seed"] = int(seed)
    return payload


def decode_speech_json(obj: dict) -> tuple[int, object]:
    """``(sample_rate, mono float32 numpy)`` from a ``response_format=json``
    speech body. Raises ``ValueError`` on a server error payload."""
    if not isinstance(obj, dict):
        raise TypeError(f"audio.cpp speech reply is not JSON: {obj!r:.120}")
    if "audio" not in obj:
        raise ValueError(f"audio.cpp speech failed: {obj.get('error', obj)!r:.300}")
    import numpy as np
    import soundfile as sf

    wav_bytes = base64.b64decode(obj["audio"])
    wav, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
    wav = np.asarray(wav, dtype=np.float32)
    if wav.ndim > 1:
        wav = wav.mean(axis=-1)
    return int(sr), wav


# ── backend ─────────────────────────────────────────────────────────────────


class AudioCPPBackend(TTSBackend):
    """Breeze-TTS-2 through a parent-managed ``audiocpp_server``."""

    id = ENGINE_ID
    display_name = (
        "audio.cpp · Breeze-TTS-2 (native GGUF, en+zh, clone+design; "
        "weights research/non-commercial)"
    )
    supports_voice_design = True
    applies_own_mastering = True  # model-decoded 24 kHz studio output
    gpu_compat = ("cpu",)
    runs_out_of_process = True
    # Same marker SubprocessBackend sets: this engine lives in another OS
    # process. Consumers only branch the matrix label and the self-test
    # route (spawn-and-ping instead of in-process synth) — both correct
    # here; nothing assumes the stdio protocol from it.
    _is_subprocess_isolated = True
    _DEFAULT_SAMPLE_RATE = 24000  # Breeze-TTS-2 native rate

    def __init__(self) -> None:
        self._proc: Any | None = None
        self._port: int | None = None
        self._server_model_id: str | None = None
        self._sr = self._DEFAULT_SAMPLE_RATE
        self._lock = threading.RLock()
        self._server_json: Path | None = None
        self._selection = None
        self._device = None
        self._provider = None

    # ── availability ────────────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        from engines.audiocpp import bootstrap

        try:
            bootstrap.resolve_server_binary()
            bootstrap.resolve_model_file()
        except RuntimeError as exc:
            return False, str(exc)
        return True, "ready"

    @classmethod
    def runtime_compute_profile(cls, caps) -> dict:
        from dataclasses import replace

        from engines.audiocpp import bootstrap
        from services.engine_routing import low_vram_caveat

        try:
            selection = bootstrap.resolve_compute_selection(caps)
            targets = bootstrap.runtime_targets()
        except RuntimeError as exc:
            return {
                "gpu_compat": cls.gpu_compat,
                "min_vram_gb": 0.0,
                "effective_device": "cpu",
                "routing_status": "unavailable",
                "routing_reason": str(exc),
                "runtime_backend": None,
                "runtime_device_index": None,
                "runtime_device_name": None,
                "runtime_hardware_family": None,
                "runtime_vram_gb": None,
                "runtime_device_verified": False,
            }
        selected = selection.device
        accelerated = selected.target != "cpu"
        min_vram_gb = _device_min_vram_gb(selected)
        dedicated = min_vram_gb > 0
        reason = selection.fallback_reason
        if accelerated and dedicated and reason is None:
            selected_caps = replace(
                caps,
                device_name=selected.name,
                vram_gb=selection.verified_vram_gb,
            )
            reason = low_vram_caveat(
                selected_caps,
                min_vram_gb,
                family=selected.hardware_family,
                vram_gb=selection.verified_vram_gb,
            )
        status = "accelerated" if accelerated else (
            "cpu_fallback" if selection.fallback_reason else "cpu_only"
        )
        return {
            "gpu_compat": targets,
            "min_vram_gb": min_vram_gb,
            "effective_device": selected.target,
            "routing_status": status,
            "routing_reason": reason,
            "runtime_backend": selected.backend,
            "runtime_device_index": selected.index,
            "runtime_device_name": selected.name,
            "runtime_hardware_family": selected.hardware_family,
            "runtime_vram_gb": selection.verified_vram_gb,
            "runtime_device_verified": selection.verified_vram_gb > 0,
        }

    # ── TTSBackend protocol ─────────────────────────────────────────────

    @property
    def sample_rate(self) -> int:
        return self._sr

    @property
    def supported_languages(self) -> list[str]:
        return ["en", "zh"]

    def model_identity(self) -> str | None:
        from engines.audiocpp import bootstrap

        return f"{bootstrap.FAMILY}/{bootstrap.package_filename()}"

    # ── server lifecycle ────────────────────────────────────────────────

    def _base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def _ensure_loaded(self) -> None:
        """Spawn the server (once) and wait for ``/health``. Idempotent."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self._proc = None  # stale handle — respawn below
            from engines.audiocpp import bootstrap

            binary = bootstrap.resolve_server_binary()
            selection = bootstrap.resolve_compute_selection()
            model_file = bootstrap.resolve_model_file()
            self._port = bootstrap.server_port()
            # The random model id is a per-launch challenge. Before sending
            # speech text or a reference path, _verify_server_identity asks
            # /v1/models to prove this is the child configured by this process,
            # not an unrelated listener that pre-bound the loopback port.
            self._server_model_id = f"{bootstrap.MODEL_ID}-{secrets.token_hex(16)}"
            config = build_server_config(
                model_id=self._server_model_id,
                family=bootstrap.FAMILY,
                model_path=str(model_file),
                port=self._port,
                backend=selection.device.backend,
                device=selection.device.index,
                execution_target=selection.device.target,
            )
            self._selection = selection
            self._device = selection.device.target
            self._provider = selection.device.backend
            from core.config import DATA_DIR

            workdir = Path(str(DATA_DIR)) / "audiocpp"
            workdir.mkdir(parents=True, exist_ok=True)
            self._server_json = workdir / "server.json"
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            config_fd = os.open(self._server_json, flags, 0o600)
            try:
                if os.name != "nt":
                    os.fchmod(config_fd, 0o600)
                with os.fdopen(config_fd, "w", encoding="utf-8") as config_fh:
                    config_fd = -1
                    json.dump(config, config_fh, indent=2)
            finally:
                if config_fd >= 0:
                    os.close(config_fd)
            log_path = workdir / "server.log"
            logger.info(
                "audio.cpp: starting %s (backend=%s, device=%d, port=%d, model=%s)",
                binary.name, selection.device.backend, selection.device.index,
                self._port, model_file.name,
            )
            with open(log_path, "ab") as log_fh:
                self._proc = spawn_owned(
                    [str(binary), "--config", str(self._server_json)],
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
            atexit.register(self._terminate_server)
            self._wait_for_health()

    def _wait_for_health(self) -> None:
        if self._proc is None or self._port is None:
            raise RuntimeError("managed audio.cpp server was not started")
        deadline = time.monotonic() + _HEALTH_TIMEOUT_S
        last_err = "unknown"
        url = self._base_url() + "/health"
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(
                    "audiocpp_server exited during startup "
                    f"(code {self._proc.returncode}). See the server log next "
                    "to server.json under the app data audiocpp/ directory — "
                    "the managed port may already be in use."
                )
            try:
                # ``url`` is always the hard-coded loopback host plus a
                # validated integer port; arbitrary schemes are impossible.
                with urllib.request.urlopen(url, timeout=5) as resp:  # nosec B310
                    if resp.status == 200:
                        self._verify_server_identity()
                        if self._proc.poll() is None:
                            logger.info(
                                "audio.cpp: managed server is healthy on loopback"
                            )
                            return
                    last_err = f"HTTP {resp.status}"
            except Exception as exc:  # noqa: BLE001 — still starting; retry
                last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)
        self._terminate_server()
        raise RuntimeError(
            f"audiocpp_server did not become healthy within "
            f"{_HEALTH_TIMEOUT_S:.0f}s (last: {last_err})."
        )

    def _get_json(self, path: str, timeout: float = 5.0) -> dict:
        """GET one loopback JSON endpoint without sending request content."""
        if self._port is None:
            raise RuntimeError("managed audio.cpp server port is missing")
        req = urllib.request.Request(self._base_url() + path, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            obj = json.loads(resp.read().decode("utf-8"))
        if not isinstance(obj, dict):
            raise TypeError("audio.cpp returned an invalid JSON response")
        return obj

    def _verify_server_identity(self) -> None:
        """Prove the loopback listener owns this launch's random model id."""
        if self._proc is None or self._proc.poll() is not None:
            raise RuntimeError("managed audio.cpp server is not running")
        expected = self._server_model_id
        if not expected:
            raise RuntimeError("managed audio.cpp server identity is missing")
        obj = self._get_json("/v1/models")
        data = obj.get("data", [])
        if not isinstance(data, list):
            raise TypeError("managed audio.cpp server identity is invalid")
        model_ids = {
            item.get("id") for item in data
            if isinstance(item, dict)
        }
        if expected not in model_ids or self._proc.poll() is not None:
            raise RuntimeError(
                "loopback listener did not prove managed audio.cpp ownership"
            )

    def _post_json(self, path: str, payload: dict, timeout: float) -> dict:
        """Verify child ownership, then POST JSON to the managed server."""
        if self._port is None:
            raise RuntimeError("managed audio.cpp server port is missing")
        self._verify_server_identity()
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._base_url() + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            # ``req`` targets only ``_base_url()`` (127.0.0.1 + validated
            # integer port), never a caller-provided URL.
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"audio.cpp {path} failed (HTTP {exc.code}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError("audio.cpp request timed out") from exc
            raise

    def _terminate_server(self) -> None:
        proc, self._proc = self._proc, None
        self._server_model_id = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=_TERMINATE_GRACE_S)
        except Exception:  # noqa: BLE001 — kill as last resort, never raise
            try:
                proc.kill()
                proc.wait(timeout=_TERMINATE_KILL_S)
            except Exception as exc:  # noqa: BLE001 — process is already failing
                logger.debug("audio.cpp: final server kill failed: %s", exc)

    # ── generate ────────────────────────────────────────────────────────

    def generate(self, text: str, **kw) -> torch.Tensor:
        self._check_language(kw.get("language"))
        import torch
        from services.model_manager import (
            GENERATE_PROGRESS_GRACE_S,
            generate_timeout_s,
            report_generate_progress,
        )

        if not text or not text.strip():
            raise TTSInputError(
                "audio.cpp: the input contains no speakable text — "
                "send at least one word."
            )
        ref_audio = kw.get("ref_audio")
        ref_text = kw.get("ref_text")
        if ref_text and not ref_audio:
            logger.info(
                "audio.cpp: ref_text supplied without ref_audio; ignoring."
            )
            ref_text = None

        # Voice design: our `description=` (no ref) and voice direction
        # (`instruct=` + ref) both ride the server's `instructions` field —
        # verified spelling against app/server/runtime.cpp.
        instruct = kw.get("instruct") or kw.get("description") or None

        language = kw.get("language")
        if language and str(language).strip().lower() not in {
            "auto", "en", "english", "zh", "chinese",
        }:
            logger.info(
                "audio.cpp (Breeze-TTS-2) is en+zh only; ignoring "
                "language=%r.", language,
            )
        if kw.get("speed", 1.0) != 1.0:
            logger.info("audio.cpp: speed is not supported; ignoring.")

        request_started = time.monotonic()
        with self._lock:
            self._ensure_loaded()
            selected = self._selection.device if self._selection else None
            min_vram_gb = _device_min_vram_gb(selected)
            request_budget = generate_timeout_s(
                text,
                execution_device=selected.target if selected else "cpu",
                min_vram_gb=min_vram_gb,
                hardware_family=selected.hardware_family if selected else None,
                vram_gb=self._selection.verified_vram_gb
                if self._selection else 0.0,
            )
            if not self._server_model_id:
                raise RuntimeError("managed audio.cpp server identity is missing")
            payload = build_speech_payload(
                model_id=self._server_model_id,
                text=text,
                ref_audio=str(ref_audio) if ref_audio else None,
                ref_text=ref_text,
                instructions=instruct,
                guidance_scale=kw.get("guidance_scale", 1.0),
                seed=kw.get("seed"),
            )
            # Device discovery and server startup can consume part of the soft
            # budget. This fresh synthesis lease gives the lazy model load and
            # request a bounded window. The inner request always expires early
            # enough to reap the owned server before the outer guard abandons us.
            report_generate_progress()
            soft_remaining = request_budget - (time.monotonic() - request_started)
            timeout = (
                max(soft_remaining, GENERATE_PROGRESS_GRACE_S)
                - _GENERATE_TIMEOUT_MARGIN_S
            )
            if timeout <= 0:
                self._terminate_server()
                raise TimeoutError(
                    "audio.cpp startup exhausted the generation time budget"
                )
            try:
                obj = self._post_json(
                    "/v1/audio/speech", payload, timeout=timeout,
                )
            except TimeoutError:
                self._terminate_server()
                raise RuntimeError(
                    "audio.cpp generation timed out; its managed server was reset"
                ) from None
        sr, wav_np = decode_speech_json(obj)
        self._sr = sr
        wav = torch.from_numpy(wav_np).float()
        if wav.ndim == 0:
            raise RuntimeError("audio.cpp produced empty audio")
        return wav.unsqueeze(0)

    # ── lifecycle ───────────────────────────────────────────────────────

    def unload(self) -> None:
        """Free the model server-side, then stop it. Idempotent."""
        with self._lock:
            if self._port is not None and self._proc is not None \
                    and self._proc.poll() is None:
                try:
                    self._post_json("/v1/tasks/unload_all_models", {}, timeout=30)
                except Exception as exc:  # noqa: BLE001 — best effort
                    logger.warning("audio.cpp: server unload failed: %s", exc)
            self._port = None
            self._terminate_server()
        super().unload()


__all__ = [
    "ENGINE_ID",
    "AudioCPPBackend",
    "build_server_config",
    "build_speech_payload",
    "decode_speech_json",
]
