"""Class-level authorization guard for every mutating backend route.

`tests/test_admin_route_policy.py` proves that *listed* routers carry the strict
dependency and that *listed* routes are marked ``@consumer_generation``. It is
blind to a route nobody listed — which is how 42 mutating routes shipped with
``dependencies=[]`` and no test noticed (route dump: 379 routes, 203 mutating,
42 unguarded). This file closes that class.

`scripts/dump_route_auth.py` reports, for every route, the authorization
callables actually attached to it (walked out of the FastAPI dependant tree, so
router-level and route-level ``dependencies=[...]`` are seen the same way).
Every mutating route must then have **at least one** such dependency, or an
explicit ``ALLOWLIST`` entry stating the authorization decision in one line.
A new mutating route with no decision fails here by default, which is the point:
the failure is the missing decision, not a missing list entry somewhere.

The allowlist is also checked for rot (each key must still resolve to a real
route) and for honesty (each reason is a non-empty single line). A listed route
that later *gains* a real dependency is deliberately still accepted, so sibling
PRs can tighten routes and land in any order.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_DUMP = _REPO / "scripts" / "dump_route_auth.py"

# Every WebSocket route is treated as mutating. A stream can change state (the
# dictation sockets persist transcripts) and a new one can be written to do so,
# so the guard cannot tell "read-only" from "state-changing" by the transport.
# Each WS route therefore needs a dependency the dump can see or an allowlist
# entry that states its in-body authorization decision.
_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# Every mutating route that carries no authorization dependency AT THIS COMMIT,
# with the decision written out. Reasons are grouped by the decision they record:
# "by design" (the route must stay reachable pre-credential, or is a stateless
# computation) versus "known gap" (the documented policy says admin, the route
# has no dependency yet — sibling PRs wire it and the entry then goes unused).
ALLOWLIST: dict[str, str] = {
    # ── Credential bootstrap ─────────────────────────────────────────────
    # These three mint or revoke the credential a gate would check, so they
    # authorize in-body instead of through Depends.
    "POST /api/auth/session": (
        "Credential exchange: validates the master API key (or legacy cookie + CSRF) "
        "in-body before minting ov_session; a Depends credential gate would be circular."
    ),
    "DELETE /api/auth/session": (
        "Logout: revokes only the caller's own credential and clears the cookie, so it "
        "must stay reachable when that credential is already invalid."
    ),
    "POST /api/auth/ws-ticket": (
        "Issues a one-shot WebSocket ticket; the in-body principal_for check requires an "
        "admin session plus cookie CSRF before minting it."
    ),
    # ── Mutating WebSockets (inline guard: a handshake closes, not 403s) ──
    "WS /ws/transcribe": (
        "Saves dictation history; guarded in-body by require_ws_access (is_local_host + "
        "ws_remote_authorized, API key or one-shot ws_ticket) because a WS handshake closes "
        "1008, not 403. A router-level dependency cannot express that."
    ),
    "WS /v1/audio/transcriptions/stream": (
        "Same ws_transcribe handler as /ws/transcribe (platform path), so it awaits the same "
        "in-body require_ws_access dictation guard."
    ),
    "WS /ws/tts": (
        "Streams synthesized audio; awaits the shared in-body require_ws_access guard, so a "
        "non-loopback client without the API key is closed with 1008 before accept()."
    ),
    "WS /ws/events": (
        "Internal event feed for the sidebar; awaits the shared in-body require_ws_access guard "
        "like every other WebSocket, because the PIN gate never covers WebSockets."
    ),
    # ── Consumption generation surface (PIN / trusted-network reachable) ──
    # docs/api-auth.md states these run a generation and stay open to consumers;
    # what they still lack is the router-level require_consumer_reads_admin_writes
    # plus @consumer_generation marking, which a sibling PR adds.
    "POST /generate": (
        "Core TTS generation — the primary consumption route, open to PIN/trusted-network "
        "consumers; needs the consumer reads/admin writes router guard plus @consumer_generation."
    ),
    "POST /dub/upload": (
        "Dub consumption: accepts the source video for a new job; open to PIN/trusted-network "
        "consumers, still missing the require_consumer_reads_admin_writes router guard."
    ),
    "POST /dub/ingest-url": (
        "Dub consumption: starts a yt-dlp fetch for a job's source; consumer-reachable by "
        "design, still missing the consumer reads/admin writes router guard."
    ),
    "POST /dub/transcribe/{job_id}": (
        "Dub consumption: transcribes a job's source audio; open to consumers by design, "
        "still missing the consumer reads/admin writes router guard."
    ),
    "POST /dub/parse-subtitle-text": (
        "Dub consumption: parses caller-supplied subtitle text into job segments; stateless "
        "parse plus job state, still missing the consumer reads/admin writes router guard."
    ),
    "POST /dub/import-srt/{job_id}": (
        "Dub consumption: imports an SRT into an existing job; open to consumers by design, "
        "still missing the consumer reads/admin writes router guard."
    ),
    "POST /dub/use-downloaded-captions/{job_id}": (
        "Dub consumption: switches a job to its downloaded captions; open to consumers by "
        "design, still missing the consumer reads/admin writes router guard."
    ),
    "POST /dub/cleanup-segments/{job_id}": (
        "Dub consumption: clears a job's generated segment state so it can re-render; open to "
        "consumers by design, still missing the consumer reads/admin writes router guard."
    ),
    "POST /dub/sync-transcript/{job_id}": (
        "Dub consumption: re-syncs a job's transcript onto its segments; open to consumers by "
        "design, still missing the consumer reads/admin writes router guard."
    ),
    "POST /dub/abort/{job_id}": (
        "Dub consumption: aborts the caller's own running dub job; open to consumers by design, "
        "still missing the consumer reads/admin writes router guard."
    ),
    "POST /dub/prosody-mirror/{job_id}": (
        "Dub consumption: runs the prosody-mirror pass over a job; open to consumers by design, "
        "still missing the consumer reads/admin writes router guard."
    ),
    "POST /dub/qc/{job_id}": (
        "Dub consumption: runs the quality-control pass over a job; open to consumers by design, "
        "still missing the consumer reads/admin writes router guard."
    ),
    "POST /preview/upload": (
        "Dub consumption: accepts a short preview clip for the preview renderer; open to "
        "consumers by design, still missing the consumer reads/admin writes router guard."
    ),
    "POST /tasks/cancel/{task_id}": (
        "Dub consumption: cancels a task the caller started; open to consumers by design, still "
        "missing the consumer reads/admin writes router guard."
    ),
    # ── Stored-content edit/delete — known gaps, documented as admin-only ──
    # docs/api-auth.md §Admin routes: "Editing, deleting and importing stored
    # content ... also uses this admin gate." These five do not yet.
    "DELETE /history": (
        "KNOWN GAP: deleting stored generation history is documented admin-only but has no "
        "dependency yet; requires the consumer reads/admin writes guard."
    ),
    "DELETE /history/{history_id}": (
        "KNOWN GAP: deleting one stored generation is documented admin-only but has no "
        "dependency yet; requires the consumer reads/admin writes guard."
    ),
    "PUT /history/{history_id}/starred": (
        "KNOWN GAP: editing stored generation metadata is documented admin-only but has no "
        "dependency yet; requires the consumer reads/admin writes guard."
    ),
    "DELETE /dub/history": (
        "KNOWN GAP: clearing stored dub history is documented admin-only but has no dependency "
        "yet; requires the consumer reads/admin writes guard."
    ),
    "DELETE /dub/history/{history_id}": (
        "KNOWN GAP: deleting one stored dub job is documented admin-only but has no dependency "
        "yet; requires the consumer reads/admin writes guard."
    ),
    # ── First-run setup: must work before any admin credential exists ─────
    "POST /models/install": (
        "First-run setup downloads a catalogue model before any admin credential exists; "
        "server mode already refuses credential-less mutations."
    ),
    "POST /models/install/cancel": (
        "Cancels the in-flight first-run model download; setup-time surface, same pre-credential "
        "window as POST /models/install."
    ),
    "DELETE /models/{repo_id:path}": (
        "First-run/setup model removal from the model manager; setup-time surface, still missing "
        "the admin gate its engine-install siblings have."
    ),
    "POST /setup/warmup": (
        "Setup: kicks off a background model load so the first dub is warm; safe to repeat, "
        "setup-time surface before any credential exists."
    ),
    # ── Model licence review flow (local evidence only, no weights) ───────
    "POST /models/install/prepare": (
        "Reads bundled licence evidence only and never resolves or downloads weights; part of "
        "the reviewed-install flow, still missing the admin gate."
    ),
    "POST /models/install/commit": (
        "Persists the licence acknowledgement then runs the reviewed exact-file transfer; "
        "download-adjacent, still missing the admin gate."
    ),
    "POST /models/licenses/verify/{plan_digest}": (
        "Verifies already-installed files against a local receipt; no install or delete, still "
        "missing the admin gate."
    ),
    "DELETE /models/licenses/receipts/{plan_digest}": (
        "Removes a local licence receipt only, keeping files and evidence; storage mutation, "
        "still missing the admin gate."
    ),
    # ── Opt-in SoniTranslate sidecar ─────────────────────────────────────
    # docs/api-auth.md lists engine install/uninstall as admin-class; these
    # three are still open (only /engines/sonitranslate/dub carries
    # require_native_access).
    "POST /engines/sonitranslate/install": (
        "KNOWN GAP: installs an opt-in external engine (~15 GB clone), admin-class per "
        "docs/api-auth.md but carries no dependency yet."
    ),
    "POST /engines/sonitranslate/start": (
        "KNOWN GAP: starts the installed SoniTranslate sidecar process, admin-class per "
        "docs/api-auth.md but carries no dependency yet."
    ),
    "POST /engines/sonitranslate/stop": (
        "KNOWN GAP: stops the SoniTranslate sidecar process, admin-class per docs/api-auth.md "
        "but carries no dependency yet."
    ),
    # ── Catalog browsing side effects (profile creation / consent) ────────
    "PUT /archetypes/previews": (
        "Toggles the pre-rendered preview gallery; turning it on IS the user's explicit "
        "consent to the outbound featured-set fetch it then makes."
    ),
    "POST /archetypes/previews/check": (
        "Forces a signature-verified preview catalog check, bypassing only the 24 h throttle; "
        "read-only against the network, harmless to repeat."
    ),
    "POST /archetypes/{archetype_id}/use": (
        "Saves a bundled archetype recipe as a local design profile without rendering; "
        "profile creation is consumer-reachable per docs/api-auth.md."
    ),
    "POST /community/items/{item_id}/use": (
        "Materializes a community item into a profile (preset recipe, or a host-allow-listed "
        "SHA-256-verified clip); profile creation is consumer-reachable by design."
    ),
    # ── Stateless computation on caller-supplied input ───────────────────
    "POST /tools/incremental": (
        "Pure function: plans which dub segments need regenerating from caller-supplied "
        "text/hashes; touches no stored state or path."
    ),
    "POST /tools/direction": (
        "Pure function: parses natural-language direction text into TTS instruct tokens; no "
        "I/O and no stored state."
    ),
    "POST /tools/rate-fit": (
        "Pure function: fits caller-supplied text into a slot duration; no I/O and no stored "
        "state."
    ),
    "POST /tools/video-context/{job_id}": (
        "Read-only analysis of an existing dub job's source video (path re-validated with "
        "resolve_within); returns cues and writes nothing."
    ),
    "POST /tools/normalize-speech": (
        "Bounded in-memory peak-normalise of a caller-supplied WAV, re-marked synthetic via "
        "mark_synthetic; nothing is persisted."
    ),
    # ── Startup-gate escape hatch ────────────────────────────────────────
    "POST /system/shutdown-intent": (
        "Authorizes in-body with check_admin(request); registered on the bare app so Electron's "
        "quit clears the crash sentinel while the system router is still gated."
    ),
}


@pytest.fixture(scope="module")
def live_auth_routes():
    """Route+guard records from a fresh subprocess — isolated from this process.

    Importing the app in-process would pollute `sys.modules` and break later
    DB-touching tests, so this mirrors tests/test_api_route_inventory.py.
    """
    env = dict(os.environ, OMNIVOICE_MODEL="test", OMNIVOICE_DISABLE_FILE_LOG="1")
    proc = subprocess.run(
        [sys.executable, str(_DUMP)],
        cwd=str(_REPO), env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"route auth dump failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _route_keys(route: dict) -> list[str]:
    """``"METHOD /path"`` keys for a route, or ``"WS /path"`` for a WebSocket."""
    if route["websocket"]:
        return [f"WS {route['path']}"]
    return [f"{method} {route['path']}" for method in route["methods"]]


def _is_mutating(route: dict) -> bool:
    if route["websocket"]:
        return True
    return bool(set(route["methods"]) & set(_MUTATING_METHODS))


def _mutating_keys(route: dict) -> list[str]:
    """The keys of a mutating route that must carry an authorization decision."""
    if route["websocket"]:
        return _route_keys(route)
    return [
        f"{method} {route['path']}"
        for method in route["methods"]
        if method in _MUTATING_METHODS
    ]


def test_every_mutating_route_has_an_authorization_decision(live_auth_routes):
    """The class-level invariant: no mutating route ships undecided.

    A route is decided when it carries at least one authorization dependency,
    or when the ALLOWLIST states the decision in writing. Anything else is a
    silent hole — exactly the shape of the 42 routes this guard was written for.
    """
    undecided: dict[str, str] = {}
    for route in live_auth_routes["routes"]:
        if not _is_mutating(route) or route["auth_dependencies"]:
            continue
        where = f'{route["module"]}.{route["endpoint"]}'
        for key in _mutating_keys(route):
            if key not in ALLOWLIST:
                undecided[key] = where

    assert not undecided, (
        "Mutating routes with no authorization dependency and no ALLOWLIST entry:\n  "
        + "\n  ".join(f"{key}  ({where})" for key, where in sorted(undecided.items()))
        + "\n\nAdd the intended gate (Depends(require_admin), "
        "require_consumer_reads_admin_writes, @consumer_generation, ...) or — only if "
        "leaving it open is a decision you can defend in one line — an ALLOWLIST entry "
        "in tests/test_route_authorization_policy.py."
    )


def test_allowlist_entries_still_resolve_to_live_routes(live_auth_routes):
    """No stale entries: every allowlisted key must still be a real route.

    This keeps the list from rotting into a pile of keys nobody can trace. A
    listed route that later *gains* a real dependency is intentionally fine —
    the entry simply stops being consulted — so sibling PRs can tighten routes
    and land in any order.
    """
    live = {
        key
        for route in live_auth_routes["routes"]
        for key in _route_keys(route)
    }
    stale = sorted(key for key in ALLOWLIST if key not in live)
    assert not stale, (
        "ALLOWLIST entries that no longer match any route on the live app "
        "(delete them):\n  " + "\n  ".join(stale)
    )


def test_allowlist_reasons_are_one_line_justifications():
    """A reason that is empty, multi-line, or a stub is not a decision.

    Mechanical on purpose (CLAUDE.md: "Mechanical rules live in deterministic
    tests"): it keeps the allowlist honest as a written policy rather than a
    list of paths with placeholder text.
    """
    for key, reason in ALLOWLIST.items():
        assert reason.strip(), f"{key}: empty justification"
        assert "\n" not in reason, f"{key}: justification must be a single line"
        assert len(reason.strip()) >= 30, (
            f"{key}: justification is too short to be a real reason: {reason!r}"
        )


def test_route_counts_are_sane(live_auth_routes):
    """A floor so a broken router mount cannot pass silently.

    A router that fails to import disappears from the dump; without this, every
    invariant above would hold vacuously over the routes that survived.
    """
    routes = live_auth_routes["routes"]
    mutating = [route for route in routes if _is_mutating(route)]
    assert live_auth_routes["count"] == len(routes)
    assert len(routes) >= 180, (
        f"Only {len(routes)} routes registered — a router likely failed to mount. "
        "Expected 300+."
    )
    assert len(mutating) >= 150, (
        f"Only {len(mutating)} mutating routes registered — the dump or a router "
        "mount is broken. Expected 190+."
    )
