"""Opt-in product analytics — hardened.

VoiceStudio is local-first, so analytics here is held to a higher bar than the
usual SDK drop-in. Three rules, each enforced in code below and pinned by tests:

1. **Off unless the user says yes.** Two independent gates must BOTH be true:
   a configured Supermetry site (``config/analytics.json``, overridden by
   ``SUPERMETRY_SITE_ID`` when set) *and*
   the user's explicit ``analytics_enabled`` preference, which defaults to
   **False**. A default install transmits nothing, so the product's promise
   holds out of the box. ``OMNIVOICE_ANALYTICS_DISABLED=1`` is a hard kill
   switch that outranks both.

2. **No exception autocapture, ever.** The obvious SDK default
   (autocapturing exceptions) ships raw tracebacks — which carry
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
import json
import logging
import math
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from core.analytics_transport import SupermetryTransport, user_agent, valid_url

logger = logging.getLogger("omnivoice.analytics")

_client = None
_client_key: Optional[str] = None  # the destination the live client was built for

_KILL_SWITCH = "OMNIVOICE_ANALYTICS_DISABLED"
_OFF_VALUES = {"1", "true", "yes", "on"}

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "analytics.json"
_client_lock = threading.RLock()


def _destination() -> dict:
    """The public Supermetry destination (``config/analytics.json``). It only
    names a site: nothing is sent without the user's explicit opt-in.
    ``SUPERMETRY_SITE_ID`` overrides it (a developer's own site, or empty to
    switch delivery off)."""
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        site = os.environ.get("SUPERMETRY_SITE_ID")
        result = {
            "site": (data.get("site", "") if site is None else site).strip(),
            "host": os.environ.get("SUPERMETRY_HOST", data.get("host", "https://app.supermetry.com")).strip().rstrip("/"),
            "appUrl": str(data.get("appUrl", "")).strip().rstrip("/"),
        }
        if (not valid_url(result["host"], origin=True) or not valid_url(result["appUrl"])
                or not re.fullmatch(r"[a-z0-9]{6,32}", result["site"])):
            return {}
        return result
    except Exception:
        return {}


#: The ONLY property keys that may leave this machine. Anything else is dropped.
#: Deliberately conservative: no free text, no paths, no names, no ids of user
#: content. Add here only after asking "could this ever hold something the user
#: typed, recorded, or named?" — if yes, it doesn't belong.
_ALLOWED_PROPS: frozenset[str] = frozenset({
    "feature",
    "action",
    "outcome",
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

#: The shape of an engine key (``omnivoice``, ``cosyvoice-3``). Anything else is dropped.
_ENGINE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]{0,39}")


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


def token_configured() -> bool:
    """Whether this build has an analytics destination at all (name kept for
    existing callers). Consent stays the real gate regardless."""
    return bool(_destination())


def enabled() -> bool:
    """The single source of truth: BOTH gates true, and not kill-switched."""
    return (not _kill_switched()) and token_configured() and user_opted_in()


def _get_client():
    """Lazily build the transport, but only while `enabled()`. Rebuilt if the
    destination changes; torn down the moment consent is withdrawn."""
    global _client, _client_key

    with _client_lock:
        if not enabled():
            if _client is not None:
                shutdown()
            return None
        destination = _destination()
        key = json.dumps(destination, sort_keys=True)
        if _client is not None and _client_key == key:
            return _client
        try:
            _client = SupermetryTransport(destination, enabled)
            _client_key = key
            atexit.register(shutdown)
            logger.info("Analytics enabled by user opt-in (host=%s).", destination["host"])
        except Exception as e:  # noqa: BLE001 — analytics must never break the app
            logger.warning("Analytics client unavailable: %s", e)
            _client, _client_key = None, None
        return _client


def shutdown() -> None:
    """Flush and drop the client. Safe to call repeatedly."""
    global _client, _client_key
    with _client_lock:
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


def profile_id() -> str:
    """The opaque Supermetry profile: ``install_`` + the random installation id."""
    return "install_" + installation_id().replace("-", "")


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
        if k == "engine_id" and not (isinstance(v, str) and _ENGINE_ID_RE.fullmatch(v)):
            continue  # our engine keys only: never a path, a file name or typed text
        if isinstance(v, float) and not math.isfinite(v):
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
    return out


def _identify() -> None:
    """Tell Supermetry once per app version which kind of install this is."""
    from core import prefs

    if prefs.get("analytics_identified") == _app_version():
        return
    client = _client
    if client is not None and client.capture(
        "identify", {}, profile_id=profile_id(),
        traits={"platform": _platform()[:256], "app_version": _app_version()[:256], "profile_kind": "installation"},
    ):
        prefs.set_("analytics_identified", _app_version())


def capture(event: str, properties: Optional[dict] = None, path: str = "/backend") -> bool:
    """Record one product event. A no-op unless the user opted in. Never raises."""
    try:
        client = _get_client()
        if client is None:
            return False
        _identify()
        return client.capture(event, sanitize_properties(properties), profile_id=profile_id(), path=path)
    except Exception as e:  # noqa: BLE001 — analytics may never break a feature
        logger.debug("analytics capture failed (%s): %s", event, e)
        return False


#: Screens the UI may report. Fixed routes only: dynamic ids never appear here.
_SAFE_SCREEN = re.compile(
    r"view:(?:home|clone|personas|stories|dub|batch|gallery|transcriptions|design|audiobook|projects|tools|calls|integrations|pro|other"
    r"|settings/(?:appearance|general|models|logs|media|pronunciation|network|sharing|credentials|performance|usage|privacy|permissions|storage|support|updates|openapi|diagnostics|other))"
)
_EVENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_:-]{0,63}")


def capture_from_ui(event: str, properties: Optional[dict] = None, screen: Optional[str] = None) -> bool:
    """Events the interface reports. Same opt-in gate and allowlist as the
    backend's own; a screen view becomes a ``pageview`` on a fixed path."""
    if event == "pageview":
        if not isinstance(screen, str) or not _SAFE_SCREEN.fullmatch(screen):
            return False
        path = "/" + screen[len("view:"):]
        return capture("pageview", {**sanitize_properties(properties), "source": "electron"}, path=path)
    if not isinstance(event, str) or not _EVENT_NAME.fullmatch(event) or event in {"identify", "pageview"}:
        return False
    return capture(event, properties)


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
    if not capture("app_installed", _common_props()):
        return False
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
    data. The site id is public (config/analytics.json), so this file grants nothing new.
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
            **_destination(),
            "user_agent": user_agent(),
            "profile_id": profile_id(),
            "policy": "opt-in",
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
