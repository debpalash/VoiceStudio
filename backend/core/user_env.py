"""Durable per-user environment file (`~/.config/omnivoice/env`).

`main.py` loads this file at startup (via dotenv) before importing torch/HF, so
values written here take effect on the next backend launch. Used by the
configurable models directory (#64): the Settings endpoint upserts
``OMNIVOICE_CACHE_DIR`` here, which main.py then maps to
``HF_HOME`` / ``HF_HUB_CACHE`` / ``TORCH_HOME``.

Format is dotenv-style ``KEY=value`` lines. Upsert preserves other keys (e.g. a
persisted ``HF_TOKEN``) and writes the file ``0600`` (it can hold secrets).
"""
from __future__ import annotations

import io
import os
import tempfile
import re
import tempfile
from typing import Optional

USER_ENV_PATH = os.path.expanduser("~/.config/omnivoice/env")

# Where a desktop user sets a backend variable by hand, for refusal messages
# that name one (core.browser_guard, core.url_safety). Stdlib-only module, so
# the guarded yt-dlp subprocess can import it too.
DESKTOP_ENV_FILE_HINT = (
    "for the desktop app, as a line in ~/.config/omnivoice/env on macOS and "
    "Linux, or %USERPROFILE%\\.config\\omnivoice\\env on Windows"
)


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().split("\n")
    except FileNotFoundError:
        return []
    # Any *other* OSError (permission, I/O error) propagates: collapsing it to
    # an empty baseline would make a subsequent upsert silently drop existing
    # keys (e.g. a persisted HF_TOKEN) when it rewrites the file.


def _write_lines(path: str, lines: list[str]) -> None:
    path = os.path.realpath(path)
    parent = os.path.dirname(path)
    if parent:  # bare filename (e.g. an OMNIVOICE_ENV_FILE override) has no parent
        os.makedirs(parent, exist_ok=True)
    body = "\n".join(lines)
    if body and not body.endswith("\n"):
        body += "\n"
    fd, temporary = tempfile.mkstemp(prefix=".omnivoice-env-", dir=parent or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            # Successful replacement already removed this path; cleanup failures
            # must not mask the original write or replacement exception.
            pass
    try:
        os.chmod(path, 0o600)  # best-effort private permissions on the replacement
    except OSError:
        pass  # best-effort; some filesystems/Windows don't support chmod


# Characters that never need quoting; ordinary values (URLs, tokens, simple
# paths) stay unquoted exactly as older versions wrote them.
_PLAIN_VALUE = re.compile(r"^[A-Za-z0-9_\-./:@%+=,~]*$")


def _encode_value(value: str) -> str:
    """Render ``value`` so dotenv reads back exactly the same string.

    An unquoted ``Books #1`` is cut at `` #`` as a comment, so anything outside
    the plain set is single-quoted with backslash and apostrophe escaped (the
    same scheme Electron uses for ``OMNIVOICE_DATA_DIR``).
    """
    if re.search(r"[\0\r\n]", value):
        raise ValueError("value must not contain NUL or line breaks")
    if _PLAIN_VALUE.match(value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _read_bindings(path: str):
    from dotenv.parser import parse_stream

    return parse_stream(io.StringIO("\n".join(_read_lines(path))))


def get_user_env(key: str, path: Optional[str] = None) -> Optional[str]:
    path = path or os.environ.get("OMNIVOICE_ENV_FILE") or USER_ENV_PATH
    value = None
    for binding in _read_bindings(path):
        if binding.key == key:
            value = binding.value
    return value


def set_user_env(key: str, value: str, path: Optional[str] = None) -> None:
    """Upsert one effective assignment, preserving all other bindings."""
    path = path or os.environ.get("OMNIVOICE_ENV_FILE") or USER_ENV_PATH
    encoded = _encode_value(value)
    parts = []
    replaced = False
    for binding in _read_bindings(path):
        if binding.key == key:
            if not replaced:
                parts.append(f"{key}={encoded}\n")
                replaced = True
        else:
            parts.append(binding.original.string)
    if not replaced:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")
        parts.append(f"{key}={encoded}\n")
    _write_lines(path, ["".join(parts)])


def unset_user_env(key: str, path: Optional[str] = None) -> None:
    """Remove every assignment to ``key``, preserving all other bindings."""
    path = path or os.environ.get("OMNIVOICE_ENV_FILE") or USER_ENV_PATH
    body = "".join(
        binding.original.string for binding in _read_bindings(path)
        if binding.key != key
    )
    _write_lines(path, [body])


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
    # dotenv expands ``${...}`` even inside single quotes, which would corrupt
    # a folder name containing it (#2519). Single-quoted values (the form
    # set_user_env writes for anything non-plain) stay literal; unquoted and
    # double-quoted ones — e.g. a hand-written ``HF_HOME=${HOME}/models`` —
    # keep their usual expansion. App-written unquoted values never hold ``$``.
    literal = dotenv.dotenv_values(path, interpolate=False, encoding="utf-8")
    expanded = dotenv.dotenv_values(path, interpolate=True, encoding="utf-8")
    single_quoted = _single_quoted_keys(path)
    for key, value in literal.items():
        if value is None:
            continue
        if key not in single_quoted and expanded.get(key) is not None:
            value = expanded[key]
        os.environ[key] = value
    _drop_invalid_path_keys()
    return True


def _single_quoted_keys(path: str) -> set[str]:
    """Keys whose effective (last) assignment in ``path`` is single-quoted."""
    from dotenv.parser import parse_stream

    keys: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for binding in parse_stream(f):
            if binding.key is None:
                continue
            rhs = binding.original.string.split("=", 1)[-1].lstrip()
            if rhs.startswith("'"):
                keys.add(binding.key)
            else:
                keys.discard(binding.key)
    return keys


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
            with tempfile.TemporaryFile(mode="w", dir=val) as probe:
                probe.write("ok")
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
