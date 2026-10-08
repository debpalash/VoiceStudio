"""3-source HF token resolver — AUTH-01, AUTH-03, AUTH-06.

Resolution priority (highest → lowest):

  1. app    — `settings_store.get_hf_token()` (encrypted in SQLite)
  2. env    — `HF_TOKEN` or the legacy `HUGGING_FACE_HUB_TOKEN` env var
  3. hf-cli — the selected local Hub token file (`HF_TOKEN_PATH`)

For each candidate, the resolver calls whoami on huggingface.co itself
(`hf_auth.canonical_whoami`, never a configured mirror) to verify the token
is live; any HTTP error (401, 403, network) skips to
the next source. Results are cached per (source, token-sha256) for 300
seconds so repeat reads from the UI/dub_core don't hammer the HF API.

Replaces every bare `os.environ.get("HF_TOKEN")` call site in the backend
(per Pitfall #1 in 01-RESEARCH.md and the grep gate in 01-01-PLAN.md
Task 2 verification).
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import threading
import time
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger("omnivoice.token_resolver")

Source = Literal["app", "env", "hf-cli"]
_PRIORITY: tuple[Source, ...] = ("app", "env", "hf-cli")

_CACHE_TTL_SECONDS = 300.0  # UI "Test now" busts it via GET /hf-token/state?fresh=1.


@dataclass(frozen=True)
class ResolvedToken:
    token: str
    source: Source
    username: Optional[str]


@dataclass(frozen=True)
class SourceState:
    source: Source
    set: bool
    masked: Optional[str]
    whoami_user: Optional[str]
    whoami_ok: Optional[bool]


# ── module-level cache ────────────────────────────────────────────────────

_VALIDATION_CACHE: dict[tuple[Source, str], tuple[float, Optional[str]]] = {}
_CACHE_LOCK = threading.Lock()


def invalidate_cache() -> None:
    """Drop the whoami validation cache. Called by the Settings UI "Test now"
    button (GET /api/settings/hf-token/state?fresh=1), by save/clear API
    endpoints, and by on_401()."""
    with _CACHE_LOCK:
        _VALIDATION_CACHE.clear()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── source readers ────────────────────────────────────────────────────────


def _read_app() -> Optional[str]:
    try:
        from services import settings_store
        return settings_store.get_hf_token()
    except Exception:
        logger.exception("settings_store read failed")
        return None


def _clean_token(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.replace("\r", "").replace("\n", "").strip() or None


def _read_env() -> Optional[str]:
    # HF docs explicitly accept either name; user may have either exported.
    val = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    return _clean_token(val)


def _read_hf_cli() -> Optional[str]:
    try:
        from huggingface_hub import constants
        return _clean_token(Path(constants.HF_TOKEN_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("Could not read the local Hugging Face token file")
        return None


_READERS: dict[Source, callable] = {  # type: ignore[type-arg]
    "app": _read_app,
    "env": _read_env,
    "hf-cli": _read_hf_cli,
}


# ── whoami validation ─────────────────────────────────────────────────────


def _validate(source: Source, token: str) -> Optional[str]:
    """Returns the validated whoami username, or None if the token is invalid.

    Caches results for `_CACHE_TTL_SECONDS` per (source, token-hash) so the
    Settings panel's repeated state() calls don't hit the HF API every load.
    """
    key = (source, _hash(token))
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _VALIDATION_CACHE.get(key)
    if cached is not None:
        ts, username = cached
        if now - ts < _CACHE_TTL_SECONDS:
            return username

    from services.hf_auth import canonical_whoami
    try:
        info = canonical_whoami(token)
        name = (info or {}).get("name") if isinstance(info, dict) else None
        with _CACHE_LOCK:
            _VALIDATION_CACHE[key] = (now, name)
        return name
    except Exception as exc:
        # Any failure — HfHubHTTPError 401/403, network — disqualifies this source.
        # Cache the negative result so we don't slam the API in tight loops;
        # the cache TTL is bounded so transient failures still recover.
        with _CACHE_LOCK:
            _VALIDATION_CACHE[key] = (now, None)
        logger.debug("whoami failed for source=%s: %s", source, exc)
        return None


def _mask(token: str) -> str:
    """`hf_…<last 3>` — what the Settings UI shows in the "currently set"
    field. We never reveal the full token in any read API."""
    if not token:
        return ""
    tail = token[-3:] if len(token) >= 3 else token
    return f"hf_…{tail}"


# ── public API ────────────────────────────────────────────────────────────


def resolve(skip: frozenset[Source] = frozenset()) -> Optional[ResolvedToken]:
    """Return the highest-priority valid token, or None if all sources are
    empty/invalid. `skip` excludes specific sources — used by `on_401()`
    when a previously-resolved token started returning 401 mid-job."""
    for source in _PRIORITY:
        if source in skip:
            continue
        token = _READERS[source]()
        if not token:
            continue
        username = _validate(source, token)
        if username is None and not _all_validation_skipped():
            # Token present but whoami failed — log once at debug and try
            # the next source. We do NOT log the token (the redactor would
            # mask it anyway, but no need to even emit it).
            continue
        return ResolvedToken(token=token, source=source, username=username)
    return None


def _all_validation_skipped() -> bool:
    """Hook left here as a no-op for now. Originally intended to allow
    network-disabled environments to bypass whoami; left in for future
    extension and explicit so reviewers see the choice."""
    return False


def on_401(active_source: Source) -> Optional[ResolvedToken]:
    """AUTH-06: when the active source started returning 401 mid-job (e.g.
    the user rotated the token externally), invalidate the cache and try
    resolving again while skipping the offending source."""
    invalidate_cache()
    return resolve(skip=frozenset({active_source}))


def state(*, validate: bool = False) -> dict:
    """Return one SourceState per priority position so the Settings UI can
    render the cascade table. Includes a masked token + whoami result;
    never includes the raw token. Reads are local unless validation is explicitly requested."""
    rows: list[SourceState] = []
    active: Optional[Source] = None
    for source in _PRIORITY:
        token = _READERS[source]()
        if token:
            username = _validate(source, token) if validate else None
            ok = (username is not None) if validate else None
            rows.append(SourceState(
                source=source,
                set=True,
                masked=_mask(token),
                whoami_user=username,
                whoami_ok=ok,
            ))
            if active is None and ok:
                active = source
        else:
            rows.append(SourceState(
                source=source,
                set=False,
                masked=None,
                whoami_user=None,
                whoami_ok=False,
            ))
    return {"sources": rows, "active": active}


def persist_hub_token(token: str) -> None:
    """Validate ``token`` on huggingface.co and make it the local Hub login.

    Writes the same files as ``huggingface_hub.login()`` (``HF_TOKEN_PATH``
    plus the named entry in ``stored_tokens``) without its whoami call, which
    goes to ``HF_ENDPOINT`` and would hand the token to a configured mirror.
    Never writes a git credential. Raises when the token is invalid.
    """
    from services.hf_auth import canonical_whoami

    if token.startswith("api_org"):
        raise ValueError("Use a personal account token, not an organization token")
    info = canonical_whoami(token)
    access = ((info or {}).get("auth") or {}).get("accessToken") or {}
    name = access.get("displayName") or f"oauth-{(info or {}).get('name') or 'user'}"
    try:
        from huggingface_hub import _login
        save, activate = _login._save_token, _login._set_active_token
    except (ImportError, AttributeError):
        # Library internals moved: the token file alone is what every Hub
        # client reads.
        from huggingface_hub import constants
        path = Path(constants.HF_TOKEN_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            # Filesystems without POSIX modes (e.g. some Windows volumes) skip this.
            pass
        return
    save(token=token, token_name=name)
    activate(token_name=name, add_to_git_credential=False)


def save_app_token(token: str) -> None:
    """Persist token to the encrypted settings store AND the local Hub token
    files via `persist_hub_token()`. Per Pitfall #2 no git credential is ever
    written — that would leak the token into the user's global git config."""
    if not token:
        clear_app_token()
        return
    from services import settings_store
    settings_store.set_hf_token(token)
    try:
        persist_hub_token(token)
    except Exception as exc:
        # Hub login failure must not strand the user — the token is still
        # in the encrypted store and the resolver will pick it up.
        logger.warning("Could not save the Hugging Face token file (%s, non-fatal)", type(exc).__name__)
    invalidate_cache()


def clear_hf_cli_tokens() -> None:
    """Remove recognized Hub token files without refreshing or revoking tokens."""
    from core.config import HF_CLI_TOKEN_PATHS
    from huggingface_hub import constants

    # Hub's active path remains authoritative if imported before app config.
    paths = set(HF_CLI_TOKEN_PATHS) | {constants.HF_TOKEN_PATH}
    failed = False
    for token_path in paths:
        path = Path(token_path)
        for target in (path, path.parent / "stored_tokens"):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                failed = True
    invalidate_cache()
    if failed:
        raise OSError("Could not clear all local Hugging Face token files")


def clear_app_token(also_clear_hf_cli: bool = False) -> None:
    """Clear the encrypted app token, optionally recognized local Hub files."""
    from services import settings_store
    settings_store.clear_hf_token()
    if also_clear_hf_cli:
        clear_hf_cli_tokens()
    invalidate_cache()
