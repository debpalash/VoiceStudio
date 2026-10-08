"""
Tiny user-preferences store — JSON file in DATA_DIR.

Keeps UI-selected choices (engine picks, translator provider, …) across
process restarts without reaching for a DB table. Environment variables
still win — users who set `OMNIVOICE_TTS_BACKEND=…` are opting into an
explicit override that the UI cannot silently undo.

    resolve("tts_backend", env="OMNIVOICE_TTS_BACKEND", default="omnivoice")
      → env var if set, else prefs.json value, else default.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any, Optional

from core.config import DATA_DIR

logger = logging.getLogger("omnivoice.prefs")

_PREFS_PATH = os.path.join(DATA_DIR, "prefs.json")

# Serializes the load-modify-save cycle of every mutation. Writers run on
# many threads (FastAPI's request threadpool, background workers like the
# sidecar-engine installer); without the lock two concurrent set_/delete
# calls interleave their read-modify-write and the later save silently
# drops the other's key. Reads stay lock-free — the atomic os.replace in
# _save guarantees they never see a torn file.
_MUTATE_LOCK = threading.RLock()


def _load() -> dict:
    try:
        with open(_PREFS_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.warning("prefs.json unreadable (%s); treating as empty", e)
        return {}


def _save(data: dict) -> None:
    # Atomic write — no half-written JSON if the process dies mid-flush.
    # Derive temp-dir from _PREFS_PATH (not DATA_DIR) so os.replace() always
    # operates within the same filesystem — important when tests redirect the path.
    target_dir = os.path.dirname(_PREFS_PATH) or DATA_DIR
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".prefs.", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _PREFS_PATH)
        os.chmod(_PREFS_PATH, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get(key: str, default: Any = None) -> Any:
    return _load().get(key, default)


def set_(key: str, value: Any) -> None:
    with _MUTATE_LOCK:
        data = _load()
        data[key] = value
        _save(data)


def update_mapping(key: str, changes: dict, *, replace: bool = False) -> None:
    """Atomically update one preference object without losing concurrent edits."""
    with _MUTATE_LOCK:
        data = _load()
        current = data.get(key)
        value = dict(current) if isinstance(current, dict) and not replace else {}
        value.update(changes)
        data[key] = value
        _save(data)


def delete(key: str) -> None:
    """Remove *key* from prefs.json if present."""
    with _MUTATE_LOCK:
        data = _load()
        data.pop(key, None)
        _save(data)


def resolve(key: str, *, env: Optional[str] = None, default: Any = None) -> Any:
    """Env var > prefs.json > default. Env is authoritative so power-users
    can pin a backend without the UI silently changing it."""
    if env:
        v = os.environ.get(env)
        if v:
            return v
    return get(key, default)


# ── external-override detection (#1787 review fix) ──────────────────────────
# restore_env() below uses os.environ.setdefault(), so a value already present
# in the process's environment (shell profile, `.env`, Docker `-e`, systemd
# unit, …) silently wins over anything saved in prefs.json — the setdefault
# call is a no-op. That is the right behavior (env stays authoritative,
# matching resolve()'s contract above), but a Settings control that persists a
# value to prefs.json must not tell the user it "took effect after restart"
# when an external source will keep shadowing it on every future restart too.
#
# _EXTERNALLY_PROVIDED records, once per process start, every bare key that
# was ALREADY present in os.environ the moment restore_env() ran — i.e.
# before our own setdefault() calls could have put it there, and before any
# value our Settings UI ever wrote (Settings only ever writes prefs.json plus
# the CURRENT process's os.environ; it never touches a shell profile or `.env`
# file). Snapshotting unconditionally — not only for keys prefs.json already
# has an entry for — means is_env_shadowed() also answers correctly for a key
# a user is about to save for the FIRST time. Membership is stable for the
# life of the process (nothing removes an inherited env var), and since a
# plain restart re-inherits the same shell / container environment, it is
# also a reliable predictor for the NEXT start: if the external source is
# still exporting the key, the next restart will be shadowed again the same
# way.
_EXTERNALLY_PROVIDED: frozenset[str] = frozenset()


# Ports older versions persisted although the process that binds them never
# reads prefs.json. Restoring one made the backend report a port it was not
# listening on (Settings → Sharing, LAN share and Tailscale targets).
UNRESTORED_ENV_KEYS = frozenset({"OMNIVOICE_PORT", "OMNIVOICE_UI_PORT"})


def restore_env(data: dict) -> None:
    """Restore ``env.*`` prefs into ``os.environ`` (startup only).

    Called once from main.py's ``env_prefs`` step, before any user code reads
    ``os.environ``. Snapshots which keys were already externally provided —
    see :func:`is_env_shadowed` — then applies every saved ``env.*`` pref via
    ``setdefault`` (never overriding an explicitly-set env var).
    """
    global _EXTERNALLY_PROVIDED
    _EXTERNALLY_PROVIDED = frozenset(os.environ.keys())
    from services.translation_apis import SECRET_ENV_KEYS
    from services import settings_store
    # Ordinary settings must restore even if the encrypted store is unavailable.
    for k, v in data.items():
        if not k.startswith("env.") or not v or k[len("env."):] in SECRET_ENV_KEYS:
            continue
        if k[len("env."):] in UNRESTORED_ENV_KEYS:
            continue
        os.environ.setdefault(k[len("env."):], str(v))
    for key in SECRET_ENV_KEYS:
        legacy = data.get("env." + key)
        saved = None
        try:
            saved = settings_store.get_secret("translation_env." + key)
            if legacy and not saved:
                settings_store.set_secret("translation_env." + key, str(legacy))
                saved = str(legacy)
            if saved:
                os.environ.setdefault(key, saved)
            if legacy:
                delete("env." + key)
        except Exception:
            # Keep legacy values usable for this launch and retry migration on
            # the next start; never delete the only copy after a failed save.
            if saved or legacy:
                os.environ.setdefault(key, str(saved or legacy))
            logger.warning("Could not restore an encrypted preference; other settings are unaffected")



def is_env_shadowed(key: str) -> bool:
    """Whether *key* was already present in the environment from a source
    other than our own prefs restore, as of the last time :func:`restore_env`
    ran. If prefs.json holds (or will hold) a saved value for *key*, that
    value is being silently ignored — and will be again on the next restart —
    unless the external source is removed."""
    return key in _EXTERNALLY_PROVIDED
