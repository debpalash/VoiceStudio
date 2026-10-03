"""Durable per-user environment file (`~/.config/omnivoice/env`).

`main.py` loads this file at startup (via dotenv) before importing torch/HF, so
values written here take effect on the next backend launch. Used by the
configurable models directory (#64): the Settings endpoint upserts
``OMNIVOICE_CACHE_DIR`` here, which main.py then maps to
``HF_HOME`` / ``HF_HUB_CACHE`` / ``TORCH_HOME``.

Format is dotenv-style ``KEY=value`` lines, with single-quoted escaping for
values containing comment, quote, whitespace or backslash characters. This
matches Electron's durable data-directory format. Upsert preserves other keys
(e.g. a persisted ``HF_TOKEN``) and writes the file ``0600`` (it can hold secrets).
"""
from __future__ import annotations

import os
import re
from typing import Optional

USER_ENV_PATH = os.path.expanduser("~/.config/omnivoice/env")


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []
    # Any *other* OSError (permission, I/O error) propagates: collapsing it to
    # an empty baseline would make a subsequent upsert silently drop existing
    # keys (e.g. a persisted HF_TOKEN) when it rewrites the file.


def _opener_0600(path: str, flags: int) -> int:
    # Create the file with 0600 from the start (no world-readable window before
    # a follow-up chmod) — it can hold secrets like HF_TOKEN. The mode is
    # masked by umask but only ever *more* restrictive; non-POSIX platforms
    # ignore the mode bits.
    return os.open(path, flags, 0o600)


def _write_lines(path: str, lines: list[str]) -> None:
    parent = os.path.dirname(path)
    if parent:  # bare filename (e.g. an OMNIVOICE_ENV_FILE override) has no parent
        os.makedirs(parent, exist_ok=True)
    body = "\n".join(lines)
    if body and not body.endswith("\n"):
        body += "\n"
    with open(path, "w", encoding="utf-8", opener=_opener_0600) as f:
        f.write(body)
    try:
        os.chmod(path, 0o600)  # tighten an existing file that predates the opener
    except OSError:
        pass  # best-effort; some filesystems/Windows don't support chmod


def get_user_env(key: str, path: Optional[str] = None) -> Optional[str]:
    path = path or os.environ.get("OMNIVOICE_ENV_FILE") or USER_ENV_PATH  # resolved at call time so tests can monkeypatch
    prefix = f"{key}="
    for line in _read_lines(path):
        if line.startswith(prefix):
            value = line[len(prefix):]
            if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
                return re.sub(r"\\(['\\])", r"\1", value[1:-1])
            return value
    return None


def set_user_env(key: str, value: str, path: Optional[str] = None) -> None:
    """Upsert ``KEY=value``, preserving all other lines."""
    path = path or os.environ.get("OMNIVOICE_ENV_FILE") or USER_ENV_PATH
    prefix = f"{key}="
    if any(char.isspace() or char in "#'\"\\" for char in value):
        value = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    lines = _read_lines(path)
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    _write_lines(path, lines)


def unset_user_env(key: str, path: Optional[str] = None) -> None:
    """Remove ``KEY=...`` if present, preserving all other lines."""
    path = path or os.environ.get("OMNIVOICE_ENV_FILE") or USER_ENV_PATH
    prefix = f"{key}="
    lines = [ln for ln in _read_lines(path) if not ln.startswith(prefix)]
    _write_lines(path, lines)


def load_into_environ(path: Optional[str] = None) -> bool:
    """Load the durable per-user env file into ``os.environ``, **overriding**
    any value a launcher already injected. Returns True if a file was loaded.

    This file is the in-app Settings source of truth. The desktop launcher
    (Tauri) injects defaults like ``OMNIVOICE_CACHE_DIR`` (and ``HF_ENDPOINT``)
    from its *own* config into the backend's environment *before* startup, so
    loading this file with ``override=False`` meant a models directory the user
    changed in Settings was silently ignored on every launch — the effective
    location stayed on the old one no matter how many restarts (#480). Both keys
    this file can hold are the user's explicit Settings choice and should beat
    the launcher's default, so we override. Restores this file's documented
    "values written here take effect on the next backend launch" contract.
    """
    path = path or os.environ.get("OMNIVOICE_ENV_FILE") or USER_ENV_PATH
    if not os.path.isfile(path):
        return False
    try:
        import dotenv
    except ImportError:
        return False
    dotenv.load_dotenv(path, override=True)
    _drop_invalid_path_keys()
    return True


#: Path-valued keys this file can persist. A reinstall that skipped uninstall
#: inherits the old file unconditionally — including e.g. an OMNIVOICE_CACHE_DIR
#: pointing at an unplugged drive or a deleted folder. Exporting a dead path
#: sends every model download/lookup somewhere that cannot exist and the app
#: looks broken out of the box (the audit's "reinstall inherits stale durable
#: state" gap). Validate after load: a directory that exists or can be created
#: is honored; anything else is dropped for THIS run with a loud log line (the
#: file itself is left alone — plugging the drive back in restores the setting).
_PATH_KEYS = ("OMNIVOICE_CACHE_DIR", "OMNIVOICE_DATA_DIR")


def _drop_invalid_path_keys() -> None:
    import logging
    logger = logging.getLogger("omnivoice.user_env")
    for key in _PATH_KEYS:
        val = os.environ.get(key)
        if not val:
            continue
        try:
            os.makedirs(val, exist_ok=True)
            # Existing-but-read-only (an external mount, a permissions accident)
            # passes isdir yet fails on first real use — probe actual write
            # capability, not just existence (review finding).
            probe = os.path.join(val, f".omnivoice-write-probe-{os.getpid()}")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            usable = True
        except OSError:
            usable = False
        if not usable:
            logger.warning(
                "%s from the saved env file points at an unusable path (%s) — "
                "ignoring it for this run and falling back to the default "
                "location. For the models folder, choose it again in Settings → "
                "Storage; otherwise fix or remove the entry in the saved env file.",
                key, val,
            )
            os.environ.pop(key, None)
