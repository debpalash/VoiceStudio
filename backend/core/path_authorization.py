"""Consume one-shot host paths authorized by the native desktop process.

The web API never accepts a filesystem destination or executable path. The
Electron main process (electron/src/main/media-authorization.ts) validates the
user's native IPC request, writes a private capability file, and only the
unguessable capability token crosses loopback HTTP.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import stat

from core.config import DATA_DIR

logger = logging.getLogger("omnivoice.path_authorization")

_TOKEN_RE = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {
    "models_dir",
    "ffmpeg",
    "ffprobe",
    "dub_export",
    "soni_input",
    "soni_output_dir",
    "sidecar_dir",
}
_AUTH_DIR = os.path.join(DATA_DIR, ".path-authorizations")


class PathAuthorizationError(ValueError):
    pass


def local_model_root_identity(path: str) -> list[int]:
    """Identify the canonical directory, refusing newly introduced links."""
    try:
        info = os.lstat(path)
        if os.path.realpath(path) != path or not stat.S_ISDIR(info.st_mode):
            raise OSError("Model root changed")
        return [info.st_dev, info.st_ino]
    except OSError as exc:
        raise PathAuthorizationError("The selected model folder changed; discover its actual folder again") from exc


class LocalModelSelection(str):
    """Scoped path retaining its object binding through validation/persistence."""

    identity: list[int] | None

    def __new__(cls, path: str, identity: list[int] | None):
        value = super().__new__(cls, path)
        value.identity = identity
        return value

    def check(self) -> None:
        if local_model_root_identity(self) != self.identity:
            raise PathAuthorizationError("The selected model folder changed; discover its actual folder again")


def issue_local_model_selection(path: str, family: str, engine_id: str) -> str:
    """Derive a narrowly scoped capability after authorized discovery validation.

    Internal only: no HTTP endpoint may issue a capability from a supplied path.
    Model files are never touched; only the existing private capability store is
    written. Generic models-dir consumers cannot redeem this scoped token.
    """
    token = secrets.token_hex(32)
    canonical = os.path.realpath(path)
    # Discovery supplies a canonical candidate; never silently authorize a swap.
    if canonical != path:
        raise PathAuthorizationError("The selected model folder changed; discover its actual folder again")
    payload = {"token": token, "kind": "models_dir", "path": canonical,
               "root_identity": local_model_root_identity(canonical),
               "scope": f"local-model:{family}:{engine_id}"}
    try:
        os.makedirs(_AUTH_DIR, mode=0o700, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(os.path.join(_AUTH_DIR, f"{token}.json"), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError as exc:
        raise PathAuthorizationError("Could not authorize model selection; check the app data folder permissions and discover again") from exc
    return token


def consume(token: str, expected_kind: str, *, expected_scope: str | None = None) -> str:
    """Consume and return a single desktop-authorized path.

    Capability files are one-shot and opened without following symlinks. The
    desktop app writes them into the app's private data directory;
    source/Docker callers cannot mint a valid token through HTTP.
    """
    if expected_kind not in _KINDS or not _TOKEN_RE.fullmatch(token or ""):
        raise PathAuthorizationError("Invalid or expired desktop authorization")
    root = _AUTH_DIR
    # Distinguish "the store exists but this token isn't in it" (expired /
    # already consumed / never issued — normal, no server-side signal) from
    # "the store doesn't exist at all" (the desktop app and this backend are
    # very likely pointed at different data directories, e.g. a dev backend
    # started without OMNIVOICE_DATA_DIR, or a stale custom data folder — see
    # #1781). The client-facing message is byte-identical either way (never
    # leak local filesystem paths, or even which case occurred, over HTTP —
    # CWE-200); the mismatch case additionally gets a server log line so it's
    # diagnosable instead of a silent 403. That log line is deliberately
    # path-free too (CWE-532: per-user filesystem paths, e.g. a home
    # directory username, are sensitive and don't belong in application
    # logs) — it names the failure mode, not the directory.
    try:
        entries = os.scandir(root)
    except FileNotFoundError as exc:
        logger.warning(
            "path authorization store does not exist; the desktop app and "
            "this backend likely resolved different data directories "
            "(see #1781)"
        )
        raise PathAuthorizationError("Invalid or expired desktop authorization") from exc
    except OSError as exc:
        raise PathAuthorizationError("Invalid or expired desktop authorization") from exc
    candidate = None
    try:
        with entries:
            for entry in entries:
                if not _TOKEN_RE.fullmatch(entry.name.removesuffix(".json")):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    with open(entry.path, "r", encoding="utf-8") as handle:
                        probe = json.load(handle)
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue  # Ignore corrupt/stale capabilities; they authorize nothing.
                if isinstance(probe, dict) and secrets.compare_digest(
                    str(probe.get("token", "")), token
                ):
                    candidate = entry.path
                    break
        if candidate is None:
            raise PathAuthorizationError("Invalid or expired desktop authorization")
        claimed = os.path.join(root, f".consuming-{os.getpid()}-{secrets.token_hex(16)}")
        os.replace(candidate, claimed)
    except OSError as exc:
        raise PathAuthorizationError("Invalid or expired desktop authorization") from exc
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(claimed, flags)
    except OSError as exc:
        raise PathAuthorizationError("Invalid or expired desktop authorization") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16_384:
            raise PathAuthorizationError("Invalid desktop authorization")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise PathAuthorizationError("Invalid desktop authorization") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(claimed)
        except OSError:
            pass  # Best-effort cleanup; the random claimed name cannot be reused.
    if not isinstance(payload, dict):
        raise PathAuthorizationError("Invalid desktop authorization")
    if not secrets.compare_digest(str(payload.get("token", "")), token):
        raise PathAuthorizationError("Invalid desktop authorization")
    if payload.get("kind") != expected_kind or not isinstance(payload.get("path"), str):
        raise PathAuthorizationError("Desktop authorization does not match this setting")
    scope = payload.get("scope")
    if scope is not None and scope != expected_scope:
        raise PathAuthorizationError("Desktop authorization does not match this engine; discover its models again")
    if scope is not None and scope.startswith("local-model:"):
        selected = LocalModelSelection(payload["path"], payload.get("root_identity"))
        selected.check()
        return selected
    return payload["path"]
