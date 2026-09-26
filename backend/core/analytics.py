"""Opt-in product analytics — hardened.

VoiceStudio is local-first, so analytics here is held to a higher bar than the
usual SDK drop-in. Three rules, each enforced in code below and pinned by tests:

1. **Off unless the user says yes.** Two independent gates must BOTH be true:
   a configured destination token (the in-repo publishable default, overridden
   by ``POSTHOG_PROJECT_TOKEN`` when set — see ``_PUBLIC_PROJECT_TOKEN``) *and*
   the user's explicit ``analytics_enabled`` preference, which defaults to
   **False**. A default install transmits nothing, so the product's promise
   holds out of the box. ``OMNIVOICE_ANALYTICS_DISABLED=1`` is a hard kill
   switch that outranks both.

2. **No exception autocapture, ever.** The obvious SDK default
   (``enable_exception_autocapture=True``) ships raw tracebacks — which carry
   absolute paths (``/Users/<name>/…``), and in this codebase can carry Hugging
   Face tokens and model paths straight out of exception messages. That would
   bypass ``core.failure.sanitize()``, the redaction this project already runs on
   every error surface. It is explicitly disabled.

3. **Metadata only, enforced by allowlist.** Every event property is filtered
   through ``_ALLOWED_PROPS``. A key that isn't on the list is *dropped*, not
   trusted — so no future caller can leak the text of a take, a file path, or a
   voice name by adding a field. Counts, durations, ids of *engines* (not users),
   and booleans are all that can get through.

The person id is a random UUID minted per installation. It is not derived from
hardware, hostname, username, or anything else identifying — it exists only to
tell "same install" from "different install".
"""
from __future__ import annotations

import atexit
import logging
import os
import uuid
from typing import Any, Optional

logger = logging.getLogger("omnivoice.analytics")

_client = None
_client_key: Optional[str] = None  # the (token, host) the live client was built for

_KILL_SWITCH = "OMNIVOICE_ANALYTICS_DISABLED"
_OFF_VALUES = {"1", "true", "yes", "on"}

#: In-repo default analytics destination (owner-sanctioned reversal, #1193):
#: source builds get the SAME consent-gated analytics as installers. This is a
#: PostHog *publishable* client key — write-only event ingestion, no data
#: access; PostHog's own FAQ says these are designed to ship in client code —
#: NOT a secret. It only names a destination: not one event leaves the machine
#: without the user's explicit opt-in (see `enabled()`).
#: A `POSTHOG_PROJECT_TOKEN` env var (release builds bake one in via the
#: desktop shell; developers can point at their own project) always wins.
#: Committed-token guard: tests/test_no_committed_analytics_token.py allows a
#: `phc_` literal in exactly this file and electron/src/shared/utils/analytics.ts.
_PUBLIC_PROJECT_TOKEN = "phc_kJK9nFqtwFmB4CYWS4bMDTY7z9P4KCRyiRT7CzJTXm9f"  # gitleaks:allow — publishable write-only key (#1193)
_DEFAULT_HOST = "https://us.i.posthog.com"

#: The ONLY property keys that may leave this machine. Anything else is dropped.
#: Deliberately conservative: no free text, no paths, no names, no ids of user
#: content. Add here only after asking "could this ever hold something the user
#: typed, recorded, or named?" — if yes, it doesn't belong.
_ALLOWED_PROPS: frozenset[str] = frozenset({
    "engine_id",        # which TTS/ASR engine (our identifier, not the user's)
    "language",         # e.g. "en" / "auto"
    "mode",             # clone | design
    "kind",             # profile kind
    "source",           # upload | url
    "input_type",       # video | audio
    "effect_preset",
    "error_type",       # exception CLASS name only — never the message
    "duration_seconds",
    "gen_time_seconds",
    "text_length",      # the LENGTH of the text. never the text.
    "has_profile",
    "stream",
    "app_version",
    "platform",
    # Lifecycle events (owner-sanctioned 2026-07-16). All values are from
    # closed sets or version strings — never messages, paths, or filenames.
    "from_version",     # app_updated: semver we upgraded from
    "to_version",       # app_updated: semver we upgraded to
    "exit_kind",        # app_crashed: closed-set label (e.g. "unclean_exit")
    "uptime_bucket",    # app_crashed: BUCKETED prior-run uptime, never raw seconds
    "error_class",      # error_occurred/app_crashed: locked taxonomy key (GPU_OOM, …)
    "stage",            # error_occurred: coarse pipeline stage / route head only
    "install_channel",  # installer | docker | source — closed set, never a path
})

#: A string property longer than this is refused outright — a belt-and-braces
#: guard so a stray free-text value can't ride in on an allowlisted key.
_MAX_STR_LEN = 64


def _kill_switched() -> bool:
    return (os.environ.get(_KILL_SWITCH, "") or "").strip().lower() in _OFF_VALUES


def user_opted_in() -> bool:
    """The user's explicit choice. Default **False** — silence is not consent."""
    try:
        from core import prefs

        return bool(prefs.get("analytics_enabled", False))
    except Exception:  # noqa: BLE001 — a broken prefs file must not enable tracking
        return False


def set_opted_in(enabled: bool) -> None:
    """Persist the user's choice and rebuild/tear down the client immediately, so
    the toggle takes effect without a restart.

    Every call is an EXPLICIT user choice (Settings toggle, first-run consent
    step, or the one-time banner) — so it also marks the user as prompted:
    the ask is never shown again once any choice has been made."""
    from core import prefs

    prefs.set_("analytics_enabled", bool(enabled))
    prefs.set_("analytics_prompted", True)
    if not enabled:
        shutdown()
    else:
        # Consent often lands AFTER the first boot (the wizard runs mid-first-
        # run), so the one-shot install event fires here rather than being
        # permanently swallowed by a pre-consent startup.
        try:
            _maybe_send_installed()
        except Exception:  # noqa: BLE001
            logger.debug("install event on opt-in failed (non-fatal)", exc_info=True)
    # The uninstall-ping info file mirrors the consent state (present ⇔ enabled).
    sync_uninstall_ping_info()


def user_prompted() -> bool:
    """Whether the user has ever been explicitly ASKED for consent (first-run
    wizard step or the one-time banner). Controls showing the question exactly
    once — it never enables anything by itself. Default False; a broken prefs
    file reads as "not asked yet", which can only re-show the question, never
    turn tracking on."""
    try:
        from core import prefs

        return bool(prefs.get("analytics_prompted", False))
    except Exception:  # noqa: BLE001
        return False


def _resolved_token() -> str:
    """The destination token: env (baked builds / developer override) wins,
    the committed publishable default (#1193) is the fallback. Empty only when
    both are blank — a destination-less build can never run analytics."""
    return (os.environ.get("POSTHOG_PROJECT_TOKEN", "") or "").strip() or _PUBLIC_PROJECT_TOKEN


def _resolved_host() -> str:
    return (os.environ.get("POSTHOG_HOST") or _DEFAULT_HOST).strip()


def token_configured() -> bool:
    """Whether this build has an analytics destination at all. Since #1193 the
    in-repo default means source builds have one too — so they get the same
    first-run consent ask as installers. False only when both the env var and
    the committed default are blank; consent stays the real gate regardless."""
    return bool(_resolved_token())


def enabled() -> bool:
    """The single source of truth: BOTH gates true, and not kill-switched."""
    return (not _kill_switched()) and token_configured() and user_opted_in()


def _get_client():
    """Lazily build the client, but only while `enabled()`. Rebuilt if the token
    or host changes; torn down the moment consent is withdrawn."""
    global _client, _client_key

    if not enabled():
        if _client is not None:
            shutdown()
        return None

    token = _resolved_token()
    host = _resolved_host()
    key = f"{token}@{host}"
    if _client is not None and _client_key == key:
        return _client

    try:
        from posthog import Posthog

        _client = Posthog(
            token,
            host=host,
            # RULE 2. Tracebacks carry home paths and can carry HF tokens; they
            # would bypass core.failure.sanitize() entirely. Never turn this on.
            enable_exception_autocapture=False,
        )
        _client_key = key
        atexit.register(shutdown)
        logger.info("Analytics enabled by user opt-in (host=%s).", host)
    except Exception as e:  # noqa: BLE001 — analytics must never break the app
        logger.warning("Analytics client unavailable: %s", e)
        _client, _client_key = None, None
    return _client


def shutdown() -> None:
    """Flush and drop the client. Safe to call repeatedly."""
    global _client, _client_key
    if _client is not None:
        try:
            _client.shutdown()
        except Exception:  # noqa: BLE001
            logger.debug("analytics shutdown error (non-fatal)", exc_info=True)
    _client, _client_key = None, None


def installation_id() -> str:
    """A random per-installation UUID. NOT derived from hardware, hostname, or
    username — it only distinguishes one install from another."""
    from core import prefs

    iid = prefs.get("installation_id")
    if not iid:
        iid = str(uuid.uuid4())
        try:
            prefs.set_("installation_id", iid)
        except Exception:  # noqa: BLE001
            logger.debug("could not persist installation_id (non-fatal)", exc_info=True)
    return str(iid)


def sanitize_properties(properties: Optional[dict]) -> dict:
    """RULE 3. Drop every key not on the allowlist, and refuse long strings.

    Pure + exported so the guarantee is directly testable: this is what stops a
    future caller from leaking a take's text, a file path, or a voice name."""
    out: dict[str, Any] = {}
    for k, v in (properties or {}).items():
        if k not in _ALLOWED_PROPS:
            continue
        if isinstance(v, str) and len(v) > _MAX_STR_LEN:
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
    return out


def capture(event: str, properties: Optional[dict] = None) -> None:
    """Record one product event. A no-op unless the user opted in. Never raises."""
    try:
        client = _get_client()
        if client is None:
            return
        client.capture(
            event,
            distinct_id=installation_id(),
            properties=sanitize_properties(properties),
        )
    except Exception as e:  # noqa: BLE001 — analytics may never break a feature
        logger.debug("analytics capture failed (%s): %s", event, e)


# ── Lifecycle events (owner-sanctioned 2026-07-16) ──────────────────────────
# install / update / crash / error events, all behind the same dual gate
# (token AND explicit consent) and the same allowlist as everything else.
# Content-free by construction: version strings, closed-set labels, buckets.

#: Prefs markers. `analytics_install_recorded` is only set once app_installed
#: was actually SENT (consent may arrive after the first boot — the wizard runs
#: mid-first-run — so setting it earlier would permanently swallow the event).
#: `analytics_last_version` is updated on EVERY startup, consented or not, so a
#: user who consents later never emits stale historical updates.
_INSTALL_MARKER = "analytics_install_recorded"
_LAST_VERSION_MARKER = "analytics_last_version"

#: error_occurred budget: at most this many per backend session, deduped by
#: journal fingerprint — a crash-loop must not turn into an event firehose.
_ERROR_EVENT_CAP = 10
_error_fingerprints_sent: set[str] = set()

#: Written next to prefs.json when (and only when) analytics is enabled, so the
#: uninstall scripts can send a single best-effort `app_uninstalled` ping with
#: the SAME consent gate — the scripts are generic and have no baked token.
#: Removed the moment consent is withdrawn (or the token disappears).
UNINSTALL_PING_INFO_BASENAME = "analytics_info.json"


def _app_version() -> str:
    try:
        from core.version import APP_VERSION

        return str(APP_VERSION)
    except Exception:  # noqa: BLE001
        return "unknown"


def _platform() -> str:
    import platform as _pl

    return {"darwin": "macos"}.get(_pl.system().lower(), _pl.system().lower() or "unknown")


def install_channel() -> str:
    """How this backend was distributed — a closed set, never derived from
    paths or hostnames. "installer": the desktop shell sets
    ``OMNIVOICE_INSTALL_CHANNEL=installer`` (backend.rs analytics_env()).
    "docker": the image sets ``OMNIVOICE_SERVER_MODE=1`` (see
    api/dependencies.py — the pre-existing Docker marker). Else "source"."""
    ch = (os.environ.get("OMNIVOICE_INSTALL_CHANNEL", "") or "").strip().lower()
    if ch in {"installer", "docker", "source"}:
        return ch
    # _OFF_VALUES doubles as the repo's canonical truthy-string set.
    if (os.environ.get("OMNIVOICE_SERVER_MODE", "") or "").strip().lower() in _OFF_VALUES:
        return "docker"
    return "source"


def _common_props() -> dict:
    return {
        "app_version": _app_version(),
        "platform": _platform(),
        "install_channel": install_channel(),
    }


def uptime_bucket(seconds: Optional[float]) -> str:
    """Coarse bucket for how long the previous run lived — never raw seconds
    (a precise duration is a fingerprinting vector; a bucket answers the only
    question that matters: instant crash vs. died mid-session)."""
    if seconds is None:
        return "unknown"
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if s < 10:
        return "lt_10s"
    if s < 60:
        return "lt_1m"
    if s < 600:
        return "lt_10m"
    if s < 3600:
        return "lt_1h"
    if s < 86400:
        return "lt_1d"
    return "ge_1d"


def _maybe_send_installed() -> bool:
    """Fire `app_installed` exactly once per installation — the first time this
    install is BOTH consented and configured. Returns True when sent."""
    from core import prefs

    if not enabled():
        return False
    if prefs.get(_INSTALL_MARKER):
        return False
    capture("app_installed", _common_props())
    prefs.set_(_INSTALL_MARKER, True)
    return True


def record_startup_lifecycle(crash_record: Optional[dict] = None) -> None:
    """Called once from the FastAPI lifespan at startup.

    `crash_record` is run_sentinel.detect_unclean_shutdown()'s return — the
    ONE authoritative crash source for `app_crashed`. (The desktop shell's
    crash markers cover the same deaths: its watcher restarts the backend,
    whose next startup finds the sentinel. Firing from both would double-count,
    so the frontend never emits a crash event.)

    Never raises; a no-op without consent AND token (the dual gate lives in
    capture()/enabled()). Version bookkeeping still runs while un-consented so
    a later opt-in can never emit stale historical events.
    """
    try:
        from core import prefs

        installed_now = _maybe_send_installed()

        current = _app_version()
        last = prefs.get(_LAST_VERSION_MARKER)
        if enabled() and not installed_now and last and str(last) != current:
            capture(
                "app_updated",
                {"from_version": str(last), "to_version": current, **_common_props()},
            )
        if str(last or "") != current:
            # Always advance the marker — consented or not — so consenting
            # later never replays an update that predates the consent.
            prefs.set_(_LAST_VERSION_MARKER, current)

        if crash_record and enabled():
            last_activity = crash_record.get("last_activity") or {}
            capture(
                "app_crashed",
                {
                    "exit_kind": "unclean_exit",
                    "stage": str(last_activity.get("kind") or "idle")[:40],
                    "uptime_bucket": uptime_bucket(crash_record.get("uptime_hint_s")),
                    **_common_props(),
                },
            )

        sync_uninstall_ping_info()
    except Exception:  # noqa: BLE001 — lifecycle telemetry must never break startup
        logger.debug("analytics startup lifecycle failed (non-fatal)", exc_info=True)


def record_error_event(error_class: str, fingerprint: str, stage: str = "") -> None:
    """`error_occurred` — fired from core.error_journal.record with the error
    CLASS and a coarse stage only (never messages, paths, or filenames; the
    allowlist would drop them anyway). Hard-capped at _ERROR_EVENT_CAP per
    session and deduped by journal fingerprint. Never raises."""
    try:
        if not enabled():
            return
        fp = str(fingerprint or "")
        if fp in _error_fingerprints_sent:
            return
        if len(_error_fingerprints_sent) >= _ERROR_EVENT_CAP:
            return
        _error_fingerprints_sent.add(fp)
        capture(
            "error_occurred",
            {
                "error_class": str(error_class or "UNKNOWN")[:40],
                "stage": str(stage or "")[:40],
                **_common_props(),
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("analytics error event failed (non-fatal)", exc_info=True)


def _reset_error_events_for_tests() -> None:
    _error_fingerprints_sent.clear()


def sync_uninstall_ping_info() -> None:
    """Keep DATA_DIR/analytics_info.json in lockstep with the consent state.

    Present  ⇔ analytics is enabled (token AND consent AND not kill-switched).
    The uninstall scripts read it (plus the consent pref itself, belt and
    braces) to send one best-effort `app_uninstalled` ping before deleting the
    data. The token is PostHog's publishable write-only client key — the same
    one baked into every release binary — so this file grants nothing new.
    Never raises."""
    import json

    try:
        from core.config import DATA_DIR

        path = os.path.join(DATA_DIR, UNINSTALL_PING_INFO_BASENAME)
        if not enabled():
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return
        payload = {
            "token": _resolved_token(),
            "host": _resolved_host(),
            "distinct_id": installation_id(),
            "app_version": _app_version(),
            "platform": _platform(),
        }
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001
        logger.debug("analytics_info sync failed (non-fatal)", exc_info=True)
