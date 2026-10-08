"""Automatic Hugging Face endpoint selection — probe, pick, remember.

Restricted-network first-runs (e.g. China, where huggingface.co is blocked)
used to dead-end until the user found the mirror setting. This service makes
that class of failure self-healing: it *races* the official endpoint against
the community mirror with real connectivity probes and remembers the winner,
so model downloads work out of the box wherever at least one endpoint is
reachable.

Principles (owner-set):

- **Probes are the truth.** The decision comes only from actual reachability
  and latency measurements against endpoints the app would legitimately
  download from. Device locale/timezone is used *only* to order which
  endpoint gets probed first — never to decide. No geo-IP lookups, no
  third-party calls, no telemetry.
- **Explicit choices are never auto-switched.** A user with an endpoint
  configured anywhere (Settings → Network, ``HF_ENDPOINT`` env, the
  ``hf_endpoint`` pref) is in manual mode; auto applies only where nothing
  was chosen. ``OMNIVOICE_HF_ENDPOINT_MODE=manual`` is a hard env opt-out.
- **Sticky, canonical-first decisions.** With both endpoints reachable the
  official endpoint wins unless the mirror is *decisively* faster
  (``MIRROR_SPEEDUP_FACTOR``× on latency, confirmed by an optional small
  ranged-GET throughput sample) — so noise can't flap users onto a mirror.
  The decision is cached in prefs and re-raced only on: no cached decision
  (first run), a network-classified download failure, an explicit
  "Test again", or a decision older than ``DECISION_MAX_AGE_S``.
- **Integrity is a non-issue.** huggingface_hub verifies every download by
  etag/sha regardless of endpoint, so a mirror cannot silently corrupt
  models.

Application is **per-call**: download paths (Model Store installs, the model
cache auto-repair) pass the effective endpoint as an ``endpoint=`` kwarg. The
auto decision is never written to ``HF_ENDPOINT``/user_env — doing so would
make it indistinguishable from an explicit user choice.

Pure and mocked-transport-testable: ``race()`` takes injectable probers, and
tests patch the module-level ``probe_endpoint`` / ``throughput_probe``
(resolved at call time). Stdlib only.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Callable, Optional
from urllib.parse import urlsplit

from services.hf_auth import CANONICAL_ENDPOINT, host_gets_auth

logger = logging.getLogger("omnivoice.endpoint_race")

COMMUNITY_MIRROR = "https://hf-mirror.com"

# Hard env opt-out: any of these values disables auto selection entirely.
MODE_ENV = "OMNIVOICE_HF_ENDPOINT_MODE"
_OPT_OUT_VALUES = {"manual", "off", "0", "false", "no"}

# prefs keys (core.prefs conventions: env > prefs.json > default).
_MODE_PREF = "hf_endpoint_mode"          # "auto" | "manual"; absent → default
_DECISION_PREF = "hf_endpoint_auto"      # cached decision dict (see race())

DECISION_MAX_AGE_S = 7 * 24 * 3600.0     # re-race a decision older than 7 days
PROBE_TIMEOUT_S = 8.0                    # high-latency / China paths often need >3s
MIRROR_SPEEDUP_FACTOR = 3.0              # mirror must be ≥3× faster to win

# Small, stable, long-lived public file for the optional ranged-GET
# throughput tiebreak (mirrors proxy the same /resolve/ paths).
_THROUGHPUT_SAMPLE_PATH = "/openai-community/gpt2/resolve/main/model.safetensors"
_THROUGHPUT_SAMPLE_BYTES = 256 * 1024

# Serialises race+persist so concurrent callers can't double-race.
_race_lock = threading.Lock()

# Repos this process already re-raced for after a download failure — the
# failover may only happen ONCE per repo per process (same guard pattern as
# model_manager._LINK_REPAIR_ATTEMPTED) so a network that stays broken can't
# loop probe↔retry.
_FAILOVER_ATTEMPTED: set[str] = set()


def _is_allowed_probe_endpoint(endpoint: str) -> bool:
    """Only probe the two fixed HTTPS origins shipped by VoiceStudio."""
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in {urlsplit(CANONICAL_ENDPOINT).hostname,
                                urlsplit(COMMUNITY_MIRROR).hostname}
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
    )


@dataclass
class ProbeResult:
    endpoint: str
    reachable: bool
    latency_ms: Optional[float] = None
    error: str = ""  # "", "timeout", "dns", "tls", "refused", "unreachable"


# ── Probes (the only network code in this module) ───────────────────────────

def _classify_probe_error(exc: Exception) -> str:
    """Coarse failure class for a probe, for logs/UI — never raises."""
    import socket
    import ssl

    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ssl.SSLError):
        return "tls"
    reason = getattr(exc, "reason", None)
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(reason, ssl.SSLError):
        return "tls"
    if isinstance(exc, ConnectionRefusedError) or isinstance(reason, ConnectionRefusedError):
        return "refused"
    return "unreachable"


def probe_endpoint(endpoint: str, timeout: float = PROBE_TIMEOUT_S) -> ProbeResult:
    """HTTPS reachability + latency: one HEAD to the endpoint root.

    Any HTTP response (even an error status) counts as reachable — the probe
    measures whether the network path works, not whether a specific resource
    exists. Never raises."""
    if not _is_allowed_probe_endpoint(endpoint):
        return ProbeResult(endpoint=endpoint, reachable=False, error="invalid_endpoint")
    url = endpoint.rstrip("/") + "/"
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "VoiceStudio-endpoint-probe"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout):  # nosec B310 -- fixed HTTPS allowlist above
            pass
    except urllib.error.HTTPError:
        pass  # the server answered → reachable
    except Exception as exc:
        return ProbeResult(endpoint=endpoint, reachable=False, error=_classify_probe_error(exc))
    return ProbeResult(
        endpoint=endpoint,
        reachable=True,
        latency_ms=round((time.monotonic() - start) * 1000.0, 1),
    )


def throughput_probe(endpoint: str, timeout: float = PROBE_TIMEOUT_S) -> Optional[float]:
    """Bytes/second over a small ranged GET of a stable public file, or None.

    Used only as a tiebreak confirmation when latency says the mirror is
    decisively faster — throughput is what a multi-GB download actually
    feels. Best-effort; any failure returns None (tiebreak skipped)."""
    if not _is_allowed_probe_endpoint(endpoint):
        return None
    url = endpoint.rstrip("/") + _THROUGHPUT_SAMPLE_PATH
    req = urllib.request.Request(
        url,
        headers={
            "Range": f"bytes=0-{_THROUGHPUT_SAMPLE_BYTES - 1}",
            "User-Agent": "VoiceStudio-endpoint-probe",
        },
    )
    deadline = time.monotonic() + timeout
    total = 0
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 -- fixed HTTPS allowlist above
            while total < _THROUGHPUT_SAMPLE_BYTES and time.monotonic() < deadline:
                chunk = resp.read(min(65536, _THROUGHPUT_SAMPLE_BYTES - total))
                if not chunk:
                    break
                total += len(chunk)
    except Exception:
        return None
    elapsed = max(time.monotonic() - start, 1e-6)
    if total <= 0:
        return None
    return total / elapsed


# ── Locale/timezone probe-ORDER hint (stdlib only, never a decision) ────────

def _hint_sources() -> tuple[list[str], list[str]]:
    """(locale strings, timezone strings) from the environment — best-effort."""
    locs: list[str] = []
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(key)
        if v:
            locs.append(v)
    try:
        import locale as _locale

        locs.extend(x for x in _locale.getlocale() if x)
    except Exception:
        pass
    tzs: list[str] = []
    tz_env = os.environ.get("TZ")
    if tz_env:
        tzs.append(tz_env)
    try:
        tzs.extend(x for x in time.tzname if x)
    except Exception:
        pass
    return locs, tzs


_CN_TZ_NAMES = {"asia/shanghai", "asia/chongqing", "asia/urumqi", "asia/harbin"}


def cn_probe_hint(
    locale_strings: Optional[list[str]] = None,
    tz_strings: Optional[list[str]] = None,
) -> bool:
    """True when device language/region/timezone *suggests* mainland China.

    Purely cosmetic: it reorders which endpoint gets probed first (so the
    likely winner's result lands soonest); every candidate is always probed
    and the decision comes from the probes alone (VPNs, expats, and corporate
    networks make region a lie). Never raises."""
    try:
        if locale_strings is None or tz_strings is None:
            env_locs, env_tzs = _hint_sources()
            locale_strings = env_locs if locale_strings is None else locale_strings
            tz_strings = env_tzs if tz_strings is None else tz_strings
        for raw in locale_strings:
            norm = raw.strip().lower().replace("-", "_")
            if "zh_cn" in norm or "zh_hans" in norm or "china" in norm:
                return True
        for raw in tz_strings:
            norm = raw.strip().lower()
            if norm in _CN_TZ_NAMES or "china standard time" in norm:
                return True
    except Exception:
        pass
    return False


def candidates(cn_hint: Optional[bool] = None) -> list[str]:
    """The endpoint registry, probe-ordered by the locale/timezone hint."""
    if cn_hint is None:
        cn_hint = cn_probe_hint()
    if cn_hint:
        return [COMMUNITY_MIRROR, CANONICAL_ENDPOINT]
    return [CANONICAL_ENDPOINT, COMMUNITY_MIRROR]


# ── Mode / explicit-setting resolution ──────────────────────────────────────

def env_opt_out() -> bool:
    return (os.environ.get(MODE_ENV) or "").strip().lower() in _OPT_OUT_VALUES


_PREF_READ_FAILED = object()


def explicit_endpoint():
    """The endpoint the user explicitly configured, or "".

    Same resolution the download paths use: ``HF_ENDPOINT`` env (what
    Settings → Network persists via user_env and what main.py loads at boot)
    with the ``hf_endpoint`` pref as fallback. Unlike
    ``core.failure.configured_hf_mirror`` this does NOT filter the official
    endpoint — explicitly choosing huggingface.co is still an explicit
    choice. Never raises."""
    ep = (os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")
    if ep:
        return ep
    try:
        from core import prefs

        return str(prefs.get("hf_endpoint", "") or "").strip().rstrip("/")
    except Exception:
        logger.warning("Endpoint preference could not be read; using manual mode")
        return _PREF_READ_FAILED


def mode() -> str:
    """``"auto"`` or ``"manual"``. Manual whenever the user opted out via
    env, has an explicit endpoint anywhere, or picked a manual mode in
    Settings (including explicitly choosing the official endpoint)."""
    if env_opt_out():
        return "manual"
    endpoint = explicit_endpoint()
    if endpoint is _PREF_READ_FAILED or endpoint:
        return "manual"
    try:
        from core import prefs

        if str(prefs.get(_MODE_PREF, "") or "").strip().lower() == "manual":
            return "manual"
    except Exception:
        # A failed preference read must not opt the user into network racing.
        logger.warning("Endpoint mode preference could not be read; using manual mode")
        return "manual"
    return "auto"


def set_mode_pref(value: str) -> None:
    """Persist the Settings-panel mode choice ("auto" | "manual")."""
    from core import prefs

    prefs.set_(_MODE_PREF, value)


# ── Decision cache (prefs conventions) ──────────────────────────────────────

def cached_decision() -> Optional[dict]:
    """The persisted race decision, or None. Shape-validated; never raises."""
    try:
        from core import prefs

        d = prefs.get(_DECISION_PREF)
    except Exception:
        return None
    if (
        isinstance(d, dict)
        and isinstance(d.get("endpoint"), str)
        and d.get("endpoint")
        and isinstance(d.get("checked_at"), (int, float))
    ):
        return d
    return None


def _store_decision(decision: dict) -> None:
    try:
        from core import prefs

        prefs.set_(_DECISION_PREF, decision)
    except Exception:  # a broken prefs file must never break downloads
        logger.warning("could not persist endpoint decision", exc_info=True)


def decision_is_fresh(decision: Optional[dict], now: Optional[float] = None) -> bool:
    if not decision:
        return False
    now = time.time() if now is None else now
    age = now - float(decision.get("checked_at") or 0)
    return 0 <= age <= DECISION_MAX_AGE_S


# ── The race ────────────────────────────────────────────────────────────────

def race(
    endpoints: Optional[list[str]] = None,
    prober: Optional[Callable[[str], ProbeResult]] = None,
    throughput_prober: Optional[Callable[[str], Optional[float]]] = None,
    now: Optional[float] = None,
) -> dict:
    """Probe all candidates in parallel and decide. Pure given the probers.

    Policy: reachable beats unreachable; with both reachable the canonical
    endpoint wins unless the mirror is ≥``MIRROR_SPEEDUP_FACTOR``× faster on
    latency AND the ranged-GET throughput sample doesn't contradict it (a
    failed/unavailable throughput probe leaves the latency verdict standing).
    Neither reachable → canonical, ``reachable=False`` (nothing works anyway;
    the offline copy owns messaging).

    Returns ``{"endpoint", "reachable", "latency_ms", "checked_at",
    "results": [...]}``.
    """
    cands = endpoints if endpoints is not None else candidates()
    # Resolve module attrs at call time so tests can patch probe_endpoint /
    # throughput_probe and every caller (preflight, Settings) picks it up.
    do_probe = prober if prober is not None else probe_endpoint
    do_throughput = throughput_prober if throughput_prober is not None else throughput_probe

    with ThreadPoolExecutor(max_workers=max(1, len(cands))) as pool:
        results = list(pool.map(do_probe, cands))

    by_endpoint = {r.endpoint: r for r in results}
    canonical = by_endpoint.get(CANONICAL_ENDPOINT)
    reachable = [r for r in results if r.reachable and r.latency_ms is not None]
    reachable.sort(key=lambda r: r.latency_ms)

    if not reachable:
        winner = ProbeResult(endpoint=CANONICAL_ENDPOINT, reachable=False,
                             error=(canonical.error if canonical else "unreachable"))
    elif canonical is None or not canonical.reachable:
        winner = reachable[0]
    else:
        winner = canonical
        fastest_mirror = next((r for r in reachable if r.endpoint != CANONICAL_ENDPOINT), None)
        if (
            fastest_mirror is not None
            and canonical.latency_ms is not None
            and fastest_mirror.latency_ms * MIRROR_SPEEDUP_FACTOR <= canonical.latency_ms
        ):
            # Decisive latency win — confirm with throughput (what a real
            # multi-GB download feels) before moving the user off canonical.
            tp_mirror = do_throughput(fastest_mirror.endpoint)
            tp_canonical = do_throughput(CANONICAL_ENDPOINT)
            if tp_mirror is not None and tp_canonical is not None and tp_mirror < tp_canonical:
                winner = canonical  # latency was noise; canonical still wins
            else:
                winner = fastest_mirror

    decision = {
        "endpoint": winner.endpoint,
        "reachable": winner.reachable,
        "latency_ms": winner.latency_ms,
        "checked_at": time.time() if now is None else now,
        "results": [asdict(r) for r in results],
    }
    logger.info(
        "HF endpoint race: picked %s (reachable=%s, latency=%sms) from %s",
        winner.endpoint, winner.reachable, winner.latency_ms,
        [(r.endpoint, r.reachable, r.latency_ms) for r in results],
    )
    return decision


def ensure_decision(
    force: bool = False,
    prober: Optional[Callable[[str], ProbeResult]] = None,
    throughput_prober: Optional[Callable[[str], Optional[float]]] = None,
) -> Optional[dict]:
    """The current auto decision, racing only when needed. None in manual mode.

    Races when: no cached decision (first run), the cache is stale
    (>``DECISION_MAX_AGE_S``), or ``force=True`` (preflight, "Test again",
    download-failure failover). Otherwise the cached decision is returned
    untouched — launches stay probe-free."""
    if mode() != "auto":
        return None
    with _race_lock:
        d = cached_decision()
        if not force and decision_is_fresh(d):
            return d
        d = race(prober=prober, throughput_prober=throughput_prober)
        _store_decision(d)
        return d


def effective_endpoint() -> Optional[str]:
    """The endpoint downloads should pass as ``endpoint=``, or None (canonical).

    Explicit user configuration always wins; in auto mode this returns the
    cached decision's mirror when one was picked. NEVER probes — safe on the
    per-download hot path. Never raises."""
    try:
        ep = explicit_endpoint()
        if ep is _PREF_READ_FAILED:
            return None
        if ep:
            return ep
        if mode() != "auto":
            return None
        d = cached_decision()
        if d and d.get("reachable") and d["endpoint"] != CANONICAL_ENDPOINT:
            return d["endpoint"]
    except Exception:
        logger.warning("effective_endpoint failed; using canonical", exc_info=True)
    return None


def download_endpoint(*, gated: bool = False) -> Optional[str]:
    """``effective_endpoint()`` for one download.

    A gated repository needs the token, which only Hugging Face receives, so an
    automatically picked mirror can never serve it: gated downloads use the
    canonical endpoint unless the user explicitly chose a mirror. Never raises.
    """
    endpoint = effective_endpoint()
    if not gated or not endpoint or host_gets_auth(endpoint):
        return endpoint
    return endpoint if explicit_mirror() else CANONICAL_ENDPOINT


def explicit_mirror() -> str:
    """The mirror the user explicitly configured, or "" for none. Never raises."""
    try:
        endpoint = explicit_endpoint()
    except Exception:
        return ""
    if not isinstance(endpoint, str) or not endpoint or host_gets_auth(endpoint):
        return ""
    return endpoint


def reselect_after_failure(repo_id: str, reason: Optional[str] = None) -> bool:
    """After a network-classified download failure: re-race once and report
    whether the effective endpoint changed (the caller then retries on it).

    Guarded once per repo per process (mirrors the cache-recovery ladder's
    retry-once guard) so a network that stays broken can't loop probe↔retry.
    No-op in manual mode and for non-network failures. Never raises."""
    try:
        if mode() != "auto":
            return False
        if reason is not None:
            from core.failure import is_hf_connectivity_error

            if not is_hf_connectivity_error(reason):
                return False
        if repo_id in _FAILOVER_ATTEMPTED:
            return False
        _FAILOVER_ATTEMPTED.add(repo_id)
        before = effective_endpoint()
        ensure_decision(force=True)
        after = effective_endpoint()
        if after != before:
            logger.warning(
                "HF endpoint failover for %s: %s → %s (download failed with a "
                "network error; retrying on the new endpoint)",
                repo_id, before or CANONICAL_ENDPOINT, after or CANONICAL_ENDPOINT,
            )
            return True
        return False
    except Exception:
        logger.warning("endpoint failover for %s errored", repo_id, exc_info=True)
        return False
