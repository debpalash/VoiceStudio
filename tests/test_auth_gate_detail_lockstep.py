"""Cross-layer contract lock for the admin-gate 403 detail string.

The backend's ``require_admin``/``require_admin_action`` answer 403 with a
mode-distinct ``detail`` (``_admin_gate_403`` in backend/api/dependencies.py):
"loopback origin or admin API key required" in server mode, plain
"loopback origin required" on the desktop build. The SPA's ``apiFetch`` routes
a 403 to the API-key login gate exactly when the detail contains the substring
"admin api key" (electron/src/shared/api/client.ts) — i.e. when presenting the key
could actually satisfy the gate. The per-mode behaviour is pinned by
tests/test_loopback_server_mode.py; this file pins the LITERAL contract across
layers: a backend reword keeps backend tests green while the frontend matcher
silently stops firing, and a LAN user is back to raw 403 spam instead of the
login form.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "backend" / "api" / "dependencies.py"
CLIENT = ROOT / "electron" / "src" / "shared" / "api" / "client.ts"


def _frontend_sniff() -> str:
    """The substring apiFetch matches on a 403 to admit it to the auth gate."""
    text = CLIENT.read_text(encoding="utf-8")
    # adminGate403 = ... detail.toLowerCase().includes('<sniff>')
    m = re.search(r"adminGate403 =.*?includes\('([^']+)'\)", text, re.DOTALL)
    assert m, "adminGate403 matcher not found in electron/src/shared/api/client.ts"
    return m.group(1)


def _key_named_details() -> set[str]:
    """Every quoted string in dependencies.py that names the admin API key."""
    return set(re.findall(r'"([^"]*admin API key[^"]*)"', DEPS.read_text(encoding="utf-8")))


def test_key_named_details_match_frontend_sniff():
    """Every backend literal naming the admin key must contain the SPA matcher."""
    details = _key_named_details()
    assert details, (
        "no 'admin API key' detail literal left in dependencies.py — moved or "
        "reworded? Update electron/src/shared/api/client.ts in the same change."
    )
    sniff = _frontend_sniff()
    for detail in details:
        # Case-insensitive substring, mirroring apiFetch's toLowerCase match.
        assert sniff in detail.lower(), (
            f"backend detail {detail!r} no longer contains the frontend matcher "
            f"{sniff!r} — the SPA would stop routing it to the API-key gate. "
            "Update electron/src/shared/api/client.ts in the same change."
        )


def test_frontend_sniff_rejects_details_a_key_cannot_fix():
    """The sniff must not swallow 403s an API key cannot satisfy.

    The desktop admin-gate arm (loopback-only regardless of credentials), the
    legacy require_loopback desktop 403, the CSRF rejection, and the
    desktop-only filesystem gate: routing any of these to the login form would
    trap the user in a form that can never succeed.
    """
    sniff = _frontend_sniff()
    unfixable = (
        "loopback origin required",  # desktop admin arm + require_loopback
        "browser origin rejected",  # BearerKeyMiddleware CSRF (main.py)
        "desktop origin required",  # require_desktop — loopback-only forever
        "native filesystem access requires loopback origin",  # require_native
    )
    for detail in unfixable:
        assert sniff not in detail.lower(), (
            f"frontend matcher {sniff!r} now also matches {detail!r}, which "
            "an API key cannot satisfy — the login gate would loop."
        )
