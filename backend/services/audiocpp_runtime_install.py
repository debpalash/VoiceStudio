"""Checksummed, user-triggered installer for the native audio.cpp runtime."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.request
import zipfile

from core.fs_retry import retry_on_lock
from engines.audiocpp import bootstrap

logger = logging.getLogger("omnivoice.audiocpp.install")
_CHUNK = 256 * 1024
_lock = threading.Lock()
_job = {"state": "idle", "progress": 0.0, "error": None}


def _snapshot() -> dict:
    with _lock:
        return dict(_job)


def _update(**fields) -> None:
    with _lock:
        _job.update(fields)


def _runtime_paths() -> tuple[Path | None, Path | None]:
    try:
        server = bootstrap.resolve_server_binary()
    except (OSError, RuntimeError):
        return None, None
    cli = server.with_name("audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli")
    if not cli.is_file() or (os.name != "nt" and not os.access(cli, os.X_OK)):
        return server, None
    return server, cli


def status() -> dict:
    server, cli = _runtime_paths()
    managed = bootstrap.managed_runtime_dir()
    is_managed = bool(
        server
        and server.resolve() == (managed / bootstrap.binary_name()).resolve()
    )
    return {
        "supported": bootstrap.default_asset() is not None,
        "installed": server is not None and cli is not None,
        "managed": is_managed,
        "version": bootstrap.VERSION if server and cli and is_managed else None,
        "platform": bootstrap.platform_slug(),
        "job": _snapshot(),
    }


def _download(url: str, destination: Path, digest: str, expected_size: int) -> None:
    if not url.startswith("https://github.com/"):
        raise ValueError("audio.cpp downloads require the pinned GitHub release")
    request = urllib.request.Request(url, headers={"User-Agent": "VoiceStudio"})
    hasher = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as out:
        total = expected_size or int(response.headers.get("Content-Length") or 0)
        while chunk := response.read(_CHUNK):
            out.write(chunk)
            hasher.update(chunk)
            received += len(chunk)
            if total:
                _update(progress=min(received / total, 0.9))
    if received != expected_size:
        raise RuntimeError("The audio.cpp runtime download size did not match the release")
    if hasher.hexdigest() != digest:
        raise RuntimeError("The audio.cpp runtime checksum did not match the release")


def _safe_destination(root: Path, name: str) -> Path:
    destination = (root / name.replace("\\", "/")).resolve()
    if destination != root and root not in destination.parents:
        raise RuntimeError("The audio.cpp archive contains an unsafe path")
    return destination


def _extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    root = destination.resolve()
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = _safe_destination(root, member.filename)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
        return
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            target = _safe_destination(root, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError("The audio.cpp archive contains an unsupported link")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeError("The audio.cpp archive contains an unreadable file")
            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
            target.chmod(member.mode & 0o700)


def _install() -> None:
    asset = bootstrap.default_asset()
    expected_size = bootstrap.default_asset_size()
    if asset is None or expected_size is None:
        raise RuntimeError("No audio.cpp runtime is published for this platform")
    filename, digest = asset
    url = (
        f"https://github.com/{bootstrap.GH_REPO}/releases/download/"
        f"{bootstrap.VERSION}/{filename}"
    )
    target = bootstrap.managed_runtime_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    # A scanner may still hold the probed binary when the context exits; a
    # leftover temp folder must not turn a finished install into an error.
    with tempfile.TemporaryDirectory(
        prefix="audiocpp-install-", dir=target.parent, ignore_cleanup_errors=True
    ) as temp:
        temp_path = Path(temp)
        archive = temp_path / filename
        _download(url, archive, digest, expected_size)
        extracted = temp_path / "extracted"
        _extract(archive, extracted)
        candidates = sorted(
            extracted.rglob(bootstrap.binary_name()), key=lambda path: len(path.parts)
        )
        if not candidates:
            raise RuntimeError("The audio.cpp release does not contain its server")
        source_dir = candidates[0].parent
        cli_name = "audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli"
        if not (source_dir / cli_name).is_file():
            raise RuntimeError("The audio.cpp release does not contain its CLI")
        prepared = temp_path / "prepared"
        shutil.copytree(source_dir, prepared)
        for executable in (prepared / bootstrap.binary_name(), prepared / cli_name):
            executable.chmod(0o700)
        probe = subprocess.run(  # nosec B603 -- checksummed fixed release binary
            [str(prepared / bootstrap.binary_name()), "--list-devices"],
            capture_output=True,
            timeout=20,
            check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError("The downloaded audio.cpp runtime failed its device check")
        # Windows antivirus scans the binary the device probe just ran and
        # holds it for a moment, so publishing the folder at once failed with
        # WinError 5 (#2685). Wait that lock out instead of failing the install.
        if target.exists():
            retry_on_lock(lambda: shutil.rmtree(target))
        retry_on_lock(lambda: os.replace(prepared, target))
    bootstrap.invalidate()


def start_install(*, wait: bool = False) -> dict:
    current = status()
    if current["installed"]:
        return {"status": "already_installed", **current}
    if not current["supported"]:
        raise RuntimeError("No audio.cpp runtime is published for this platform")
    with _lock:
        running = _job["state"] == "running"
        if not running:
            _job.update(state="running", progress=0.0, error=None)
    if running:
        return {"status": "already_running", **status()}

    def worker() -> None:
        try:
            _install()
            _update(state="done", progress=1.0, error=None)
        except Exception:
            logger.exception("audio.cpp runtime installation failed")
            _update(
                state="error",
                error="The audio.cpp runtime could not be installed. Check the backend log.",
            )

    if wait:
        worker()
    else:
        threading.Thread(target=worker, name="audiocpp-install", daemon=True).start()
    return {"status": "started", **status()}


def reset_job_for_tests() -> None:
    _update(state="idle", progress=0.0, error=None)
