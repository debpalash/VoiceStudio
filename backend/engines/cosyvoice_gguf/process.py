"""Owned loopback-only native server lifecycle and authenticated requests."""

from __future__ import annotations

import json
import logging
import secrets
import socket
import subprocess  # nosec B404 -- executable and args are resolved locally
import time
import urllib.request
from pathlib import Path

from core.contained_subprocess import spawn_owned

from engines.cosyvoice_gguf import bootstrap

logger = logging.getLogger("omnivoice.cosyvoice_gguf")


class NativeServer:
    def __init__(self):
        self.process = None
        self.port = None
        self.api_key = None
        self.model_id = None
        self.prompt_key = None
        self.selection = None
        self.log_handle = None

    def _base(self):
        return f"http://127.0.0.1:{self.port}"

    def ensure(self, prompt: Path, key: str) -> None:
        if (
            self.process is not None
            and self.process.poll() is None
            and self.prompt_key == key
        ):
            return
        self.stop()
        server = bootstrap.resolve_server()
        bootstrap.resolve_cli()
        model = bootstrap.resolve_model()
        self.selection, fallback = bootstrap.selected_backend()
        self.port = _free_port()
        self.api_key = secrets.token_urlsafe(32)
        self.model_id = f"{bootstrap.MODEL_ID}-{secrets.token_hex(8)}"
        args = [
            str(server),
            "--api",
            "--model",
            str(model),
            "--voice-prompt",
            f"mira={prompt}",
            "--backend",
            self.selection.name,
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--served-model-name",
            self.model_id,
            # The pinned cosyvoice.cpp server reads its key only from argv (no
            # env or file option at 67d45feb), so the key is a fresh per-launch
            # secret on a loopback-only port, and every request also checks the
            # random served-model name. See docs/engines/cosyvoice-gguf.md.
            "--api-key",
            self.api_key,
        ]
        log_dir = Path(bootstrap.managed_dir()).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_handle = (log_dir / "server.log").open("ab")
        logger.info(
            "CosyVoice GGUF selected %s (%s)%s",
            self.selection.name,
            self.selection.device_name,
            f"; {fallback}" if fallback else "",
        )
        self.process = spawn_owned(
            args,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(server.parent),
        )
        self.prompt_key = key
        try:
            self._wait_ready()
        except Exception:
            self.stop()
            raise

    def _request(
        self, path: str, payload: dict | None = None, timeout: float = 30
    ) -> dict:
        req = urllib.request.Request(
            self._base() + path, method="POST" if payload is not None else "GET"
        )
        req.add_header("Authorization", f"Bearer {self.api_key}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
            req.data = json.dumps(payload).encode("utf-8")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as response:  # nosec B310 loopback only
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, dict):
            raise TypeError("CosyVoice GGUF server returned malformed JSON")
        return result

    def _wait_ready(self):
        deadline = time.monotonic() + 60
        last_error = "server did not respond"
        while time.monotonic() < deadline:
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError(
                    "CosyVoice GGUF server exited during startup; see its server.log"
                )
            try:
                self._request("/healthz", timeout=2)
                data = self._request("/v1/models", timeout=2).get("data", [])
                if any(
                    isinstance(row, dict) and row.get("id") == self.model_id
                    for row in data
                ):
                    return
                last_error = "server identity did not match this managed process"
            except (
                OSError,
                ValueError,
                RuntimeError,
            ) as exc:  # server may still be loading
                last_error = str(exc)
            time.sleep(0.5)
        raise TimeoutError(f"CosyVoice GGUF server did not become ready: {last_error}")

    def speak(self, payload: dict, timeout: float = 600) -> bytes:
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("managed CosyVoice GGUF server is not running")
        rows = self._request("/v1/models").get("data", [])
        if not any(
            isinstance(row, dict) and row.get("id") == self.model_id for row in rows
        ):
            raise RuntimeError(
                "loopback endpoint is not owned by this CosyVoice GGUF process"
            )
        request = urllib.request.Request(
            self._base() + "/v1/audio/speech",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        request.add_header("Authorization", f"Bearer {self.api_key}")
        request.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:  # nosec B310 loopback only
            return response.read()

    def stop(self):
        proc, self.process = self.process, None
        self.model_id = None
        self.prompt_key = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (OSError, subprocess.SubprocessError) as exc:
                    logger.debug("Unable to reap CosyVoice GGUF server: %s", exc)
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
