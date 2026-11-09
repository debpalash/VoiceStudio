"""Launch the Docker backend while preserving user-supplied Uvicorn options."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from http.client import HTTPConnection
from pathlib import Path

from services.network_share import backend_port

_PROC_ROOT = Path("/proc")
_DEFAULT_DOCKER_HOST = "0.0.0.0"  # nosec B104: containers must accept published traffic.


def _has_option(arguments: Sequence[str], option: str) -> bool:
    """Return whether an option is present in split or ``--name=value`` form."""
    return any(argument == option or argument.startswith(f"{option}=") for argument in arguments)


def effective_port(arguments: Sequence[str]) -> int:
    """Resolve an explicit Uvicorn port before falling back to app configuration."""
    for index, argument in enumerate(arguments):
        if argument.startswith("--port="):
            return int(argument.partition("=")[2])
        if argument == "--port" and index + 1 < len(arguments):
            return int(arguments[index + 1])
    return backend_port()


def uvicorn_arguments(arguments: Sequence[str], port: int | None = None) -> list[str]:
    """Build Uvicorn arguments without overriding explicit container options."""
    supplied = list(arguments)
    resolved = ["-m", "uvicorn", "backend.main:app"]
    if not _has_option(supplied, "--host"):
        resolved.extend(["--host", os.environ.get("OMNIVOICE_BIND_HOST", _DEFAULT_DOCKER_HOST)])
    if not _has_option(supplied, "--port"):
        resolved.extend(["--port", str(port if port is not None else effective_port(supplied))])
    return [*resolved, *supplied]


def serving_port(proc_root: Path = _PROC_ROOT) -> int:
    """Discover the live Uvicorn port without requiring writable container state."""
    for command_file in proc_root.glob("[0-9]*/cmdline"):
        try:
            arguments = [
                value.decode("utf-8", errors="replace")
                for value in command_file.read_bytes().split(b"\0")
                if value
            ]
        except OSError:
            continue
        if "uvicorn" in arguments and "backend.main:app" in arguments:
            return effective_port(arguments)
    return backend_port()


def healthcheck(proc_root: Path = _PROC_ROOT) -> None:
    """Probe the exact loopback port used by the serving container process."""
    connection = HTTPConnection("127.0.0.1", serving_port(proc_root), timeout=4)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        if response.status >= 400:
            raise RuntimeError(f"backend health check returned HTTP {response.status}")
    finally:
        connection.close()


def main() -> None:
    """Replace this process with Uvicorn so container signals reach the server."""
    supplied = sys.argv[1:]
    if supplied == ["--health-check"]:
        healthcheck()
        return
    port = effective_port(supplied)
    os.environ["OMNIVOICE_PORT"] = str(port)
    os.execv(sys.executable, [sys.executable, *uvicorn_arguments(supplied, port)])


if __name__ == "__main__":
    main()
