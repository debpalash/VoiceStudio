"""Encrypted settings store for VoiceStudio — AUTH-02 + threat T-01-01.

Persists small key/value rows in the SQLite `settings` table. The `hf_token`
row is encrypted at rest via Fernet (symmetric AEAD). The Fernet key itself
is derived per-install in `_secret_key.derive_fernet_key()` so a copied
omnivoice.db is not a smoking gun on another machine.

Public API (consumed by `token_resolver.py`):
    get_hf_token() -> Optional[str]
    set_hf_token(token: str) -> None
    clear_hf_token() -> None
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Optional
from core.logging_utils import log_safe

logger = logging.getLogger("omnivoice.settings_store")

_TOKEN_KEY = "hf_token"


def _store_not_created(exc: BaseException) -> bool:
    """A fresh install reads settings before ``init_db`` creates the table.

    Startup restores saved preferences first, because they can move the data
    directory, so "no table yet" means nothing is stored rather than a failure.
    """
    return isinstance(exc, sqlite3.OperationalError) and "no such table: settings" in str(exc)


def _fernet():
    """Build a Fernet instance from the derived per-install key.

    Lazy import of cryptography keeps the module importable even if the
    dep ever falls out of the install (graceful warn instead of ImportError
    at module load).
    """
    from cryptography.fernet import Fernet
    from services._secret_key import derive_fernet_key

    return Fernet(derive_fernet_key())


def get_hf_token() -> Optional[str]:
    """Return the decrypted HF token from the settings table, or None.

    On `InvalidToken` (e.g. user migrated omnivoice_data/ across machines so
    machine-id no longer matches the salt used to encrypt), log a warning
    and return None — the caller's cascade (env / HF-CLI) takes over
    naturally. Per Open Question #5 in 01-RESEARCH.md.
    """
    from core.db import db_conn

    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (_TOKEN_KEY,)
            ).fetchone()
        if row is None or not row[0]:
            return None
        blob = row[0]
        try:
            from cryptography.fernet import InvalidToken
        except ImportError:  # pragma: no cover — dep should always be present
            logger.error("cryptography unavailable; cannot decrypt HF token")
            return None
        try:
            return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.warning(
                "Stored HF token failed to decrypt — most likely the install "
                "moved across machines or the salt row was tampered with. "
                "Falling back to env/HF-CLI sources."
            )
            return None
    except Exception as exc:
        if _store_not_created(exc):
            return None
        # SQLite errors must not break the resolver — return None and let
        # the resolver cascade.
        logger.exception("settings_store.get_hf_token: SQLite read failed")
        return None


def set_hf_token(token: str) -> None:
    """Persist an encrypted HF token. The first call also writes the per-install
    salt row inside the same transaction (atomic, no torn state)."""
    if not token:
        # Defense in depth — callers should pass a non-empty string. An empty
        # token in the cascade is the same as "no token" and we should not
        # write a row that round-trips to the empty string.
        clear_hf_token()
        return

    from core.db import db_conn

    # Derive the key first — this lazily generates the salt row on first
    # write, inside its own transaction. Subsequent ops use the cached key.
    cipher = _fernet()
    blob = cipher.encrypt(token.encode("utf-8")).decode("ascii")

    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (_TOKEN_KEY, blob, time.time()),
        )


def clear_hf_token() -> None:
    """Remove the HF token row. The salt row is preserved so a re-save by
    the same user on the same machine produces ciphertext the resolver can
    still decrypt."""
    from core.db import db_conn

    with db_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (_TOKEN_KEY,))


# ── Generic encrypted secrets (LLM provider API keys, future tokens) ───────
# The HF token got the first bespoke encrypted row; the LLM-providers feature
# needs the *same* at-rest protection for a dozen provider keys. Rather than
# copy the Fernet dance per provider, expose generic secret helpers. Rows are
# namespaced with the ``secret.`` prefix so a misrouted ``get_text`` on a
# secret key returns opaque ciphertext (defence in depth), and so plaintext
# ``settings`` rows can never collide with a secret. Same InvalidToken →
# None degrade as the HF path (install moved across machines → fall back to
# env), same per-install key.
_SECRET_PREFIX = "secret."


def _secret_key_name(name: str) -> str:
    if not name or not isinstance(name, str):
        raise ValueError(f"secret name must be a non-empty string, got {name!r}")
    if name == _TOKEN_KEY or name.startswith(_SECRET_PREFIX):
        raise ValueError(f"invalid secret name {name!r}")
    return f"{_SECRET_PREFIX}{name}"


def get_secret(name: str) -> Optional[str]:
    """Return a decrypted secret (e.g. an LLM provider API key), or None.

    Mirrors :func:`get_hf_token`: on decrypt failure (install migrated across
    machines) or any SQLite error, log and return None so callers fall back to
    env / provider defaults instead of crashing.
    """
    from core.db import db_conn

    key = _secret_key_name(name)
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            from cryptography.fernet import InvalidToken
        except ImportError:  # pragma: no cover — dep should always be present
            logger.error("cryptography unavailable; encrypted setting cannot be decrypted")
            return None
        try:
            return _fernet().decrypt(row[0].encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.warning(
                "Stored encrypted setting failed to decrypt (install moved across "
                "machines or salt tampered) — falling back to env/default."
            )
            return None
    except sqlite3.Error as exc:
        if _store_not_created(exc):
            return None
        # This path may hold plaintext/ciphertext secret values in locals.
        # Keep the record fixed-shape; never attach exception state or traceback.
        logger.error("settings_store.get_secret: SQLite read failed")
        return None


def set_secret(name: str, value: str) -> None:
    """Persist an encrypted secret. Empty value clears the row."""
    if not value:
        clear_secret(name)
        return
    from core.db import db_conn

    key = _secret_key_name(name)
    blob = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (key, blob, time.time()),
        )


def clear_secret(name: str) -> None:
    """Remove a secret row (salt row preserved, like clear_hf_token)."""
    from core.db import db_conn

    key = _secret_key_name(name)
    with db_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def list_secret_names() -> list[str]:
    """Return the bare names of all stored secrets (no values, no ciphertext).

    Lets the LLM-providers settings API report *which* providers have a key
    configured without ever decrypting or returning the key material.
    """
    from core.db import db_conn

    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT key FROM settings WHERE key LIKE ?",
                (f"{_SECRET_PREFIX}%",),
            ).fetchall()
        return [r[0][len(_SECRET_PREFIX):] for r in rows if r and r[0]]
    except Exception as exc:
        if _store_not_created(exc):
            return []
        logger.exception("settings_store.list_secret_names: SQLite read failed")
        return []


# ── Non-secret text settings ──────────────────────────────────────────────
# Plan 01-02 Task 4 (INST-12): the Performance panel needs to persist a
# boolean toggle (`perf.torch_compile_disabled`). It is NOT a secret — no
# user-recoverable harm comes from a leaked "user disabled torch.compile"
# bit — so we store the raw text directly in the same `settings` table
# without Fernet wrap.
#
# Use these helpers (not `set_hf_token`) for non-secret config:
#   set_text("perf.torch_compile_disabled", "1")
#   get_text("perf.torch_compile_disabled", default="0")


def get_text(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a non-encrypted text value from the settings table.

    Returns `default` if the row is missing OR if reading the row fails.
    The HF-token row is encrypted ciphertext and will round-trip here
    looking like opaque bytes — callers MUST use `get_hf_token()` for
    secrets and only ever pass non-secret keys to `get_text()`.
    """
    if key == _TOKEN_KEY or key.startswith(_SECRET_PREFIX):
        # defence in depth — never let a misrouted call leak ciphertext
        return default
    from core.db import db_conn

    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None or row[0] is None:
            return default
        return str(row[0])
    except Exception as exc:
        if _store_not_created(exc):
            return default
        logger.error(
            "settings_store.get_text(%s): SQLite read failed: %s",
            log_safe(key), log_safe(exc),
        )
        return default


def get_text_state(key: str) -> tuple[bool, str]:
    """Return ``(is_present, value)`` without hiding storage failures.

    Rollback snapshots must distinguish a missing row from an unreadable
    database.  ``get_text`` deliberately collapses those cases for ordinary
    preference reads, so transactional callers use this strict variant.
    """
    if key == _TOKEN_KEY or key.startswith(_SECRET_PREFIX):
        raise ValueError(
            "get_text_state refuses to read an encrypted secret row; "
            "use get_hf_token()/get_secret() for secrets"
        )
    from core.db import db_conn

    with db_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return False, ""
    if row[0] is None:
        return True, ""
    return True, str(row[0])


def set_text(key: str, value: str) -> None:
    """Persist a non-encrypted text value into the settings table.

    Use for non-secret config only. For tokens, use `set_hf_token()`.
    """
    if key == _TOKEN_KEY or key.startswith(_SECRET_PREFIX):
        raise ValueError(
            "set_text refuses to write to an encrypted secret row; "
            "use set_hf_token()/set_secret() for secrets"
        )
    from core.db import db_conn

    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (key, value, time.time()),
        )
    _announce(key)


def _announce(key: str) -> None:
    from core import change_hooks

    change_hooks.notify("settings", key)


def clear_text(key: str) -> None:
    """Remove a non-encrypted text setting, preserving a missing-row default."""
    if key == _TOKEN_KEY or key.startswith(_SECRET_PREFIX):
        raise ValueError(
            "clear_text refuses to delete an encrypted secret row; "
            "use clear_hf_token()/clear_secret() for secrets"
        )
    from core.db import db_conn

    with db_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    _announce(key)


# ── Phase 4 Plan 04-01 (GGUF-04): per-engine quant override ────────────────
#
# Settings row "gguf_quant_override" holds either:
#   * "auto"          — explicit auto-select (default, equivalent to row absent)
#   * a quant filename listed in quant_map.json (e.g. "omnivoice-base-F32.gguf")
# Any other value is rejected at write time (T-04-05 — the quant override UI
# cannot be used to load an attacker-controlled GGUF path).

_QUANT_OVERRIDE_KEY = "gguf_quant_override"


def get_quant_override() -> Optional[str]:
    """Return the user's GGUF quant override, or None when auto-select.

    Returns:
        ``None`` — no row present, or row holds the sentinel ``"auto"``.
        ``str``  — a quant filename from ``quant_map.json``'s allow-list.

    Reads through the same disk-backed SQLite path as ``get_text``, so
    the override survives process restart (per GGUF-04 acceptance).
    """
    raw = get_text(_QUANT_OVERRIDE_KEY)
    if raw is None or raw == "auto":
        return None
    return raw


def set_quant_override(value: Optional[str]) -> None:
    """Persist a GGUF quant override (or clear it on ``None`` / ``"auto"``).

    Args:
        value: ``None`` or ``"auto"`` to clear; otherwise a quant filename
            from ``quant_map.json``'s allow-list
            (e.g. ``"omnivoice-base-F32.gguf"``).

    Raises:
        ValueError: ``value`` is a string but is not in the allow-list,
            or it contains a path separator / ``..``. This protects
            against UI input being used to load a quant from an
            attacker-controlled path (T-04-05 in the Plan 04-01 threat
            model).
    """
    from core.db import db_conn

    if value is None or value == "auto":
        # Clear the row entirely — get_quant_override returns None for
        # both "row absent" and the explicit "auto" sentinel; we
        # canonicalize on "absent" so the table stays clean.
        with db_conn() as conn:
            conn.execute(
                "DELETE FROM settings WHERE key = ?",
                (_QUANT_OVERRIDE_KEY,),
            )
        return

    if not isinstance(value, str):
        raise ValueError(
            f"set_quant_override expects a str or None, got {type(value).__name__}"
        )

    # Defence-in-depth: never accept anything that could escape the HF
    # cache. The allow-list check below would catch this too, but
    # failing fast on path separators surfaces the bug closer to the
    # caller.
    if "/" in value or "\\" in value or ".." in value:
        raise ValueError(
            f"set_quant_override refuses path components in {value!r}"
        )

    # Allow-list against quant_map.json. Importing here keeps the engine
    # package off settings_store's hot import path for non-GGUF callers.
    try:
        from engines.omnivoice_gguf.backend import _allowed_quant_filenames
    except Exception as exc:  # pragma: no cover - engine package always present
        raise ValueError(
            f"GGUF engine package unavailable; cannot validate quant override: {exc}"
        ) from exc

    allowed = _allowed_quant_filenames()
    if value not in allowed:
        raise ValueError(
            f"set_quant_override rejects {value!r}: not in quant_map.json "
            f"allow-list. Allowed values: {sorted(allowed)}"
        )

    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (_QUANT_OVERRIDE_KEY, value, time.time()),
        )


# ── License acceptance helpers (Phase 3 Plan 03-01 / TTS-05) ──────────────
# Tiny wrappers around the plaintext ``set_text``/``get_text`` helpers so
# every engine that needs an acceptance gate (Supertonic-3 today; future
# OpenRAIL-M / non-commercial engines) reads + writes the same key shape:
# ``"<engine_id>_license_accepted"`` -> ``"1"`` | ``"0"``.
#
# Pitfall 6 in 03-RESEARCH.md: ``set_license_accepted`` MUST block until
# the SQLite commit returns. ``db_conn()`` is a context manager that
# commits on ``__exit__`` (Phase 1 contract verified at 01-RESEARCH.md
# settings_store section), so ``set_text`` already satisfies the
# "readable on next call" invariant. We re-read inside this helper as a
# belt-and-braces verification path that tests can rely on.


_LICENSE_KEY_SUFFIX = "_license_accepted"


def _license_key(engine_id: str) -> str:
    """Map an engine id to its license-flag settings key.

    Public so tests can assert on the exact stored row. The HF-token
    row's name ``hf_token`` will never collide because we always append
    the suffix.
    """
    if not engine_id or not isinstance(engine_id, str):
        raise ValueError(f"engine_id must be a non-empty string, got {engine_id!r}")
    # Defence in depth: disallow the literal hf_token key so a misrouted
    # call can never overwrite the encrypted-token row.
    if engine_id == _TOKEN_KEY:
        raise ValueError("engine_id cannot be the reserved HF-token key")
    return f"{engine_id}{_LICENSE_KEY_SUFFIX}"


def get_license_accepted(engine_id: str) -> bool:
    """Return True iff the user has accepted the engine's license terms.

    Reads from the plaintext ``settings`` table. Missing row, SQLite
    read failure, or any non-``"1"`` value all return False so the
    callsite (``Supertonic3Backend.is_available()``) defaults to safe.
    """
    key = _license_key(engine_id)
    raw = get_text(key, default="0")
    return raw == "1"


def set_license_accepted(engine_id: str, accepted: bool) -> None:
    """Persist the acceptance flag. Blocks until SQLite commits.

    ``accepted=False`` writes ``"0"`` (not a delete) so a once-accepted
    user who later revokes acceptance still has an explicit row in the
    audit-trail-friendly settings table.
    """
    key = _license_key(engine_id)
    set_text(key, "1" if accepted else "0")
    # Re-read invariant ‑‑ Pitfall 6 defence. A failure here means the
    # commit silently dropped, which would be a SQLite/db_conn bug; we
    # surface it as a hard error rather than hand back a stale state.
    actual = get_text(key, default="0")
    expected = "1" if accepted else "0"
    if actual != expected:
        raise RuntimeError(
            f"set_license_accepted({engine_id!r}, {accepted!r}) did not "
            f"persist (read back {actual!r}, expected {expected!r})"
        )
