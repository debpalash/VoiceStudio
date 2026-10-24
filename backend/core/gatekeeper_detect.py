"""macOS Gatekeeper quarantine detection.

Issue #54 — When a user downloads the .dmg/.zip outside the Mac App Store,
macOS Gatekeeper attaches an `com.apple.quarantine` extended attribute to the
.app bundle. On first launch, that attribute propagates to every binary inside
the bundle, and Gatekeeper refuses to exec any of them. The user sees
"VoiceStudio can't be opened" or the app crashes silently — both bad UX.

The fix is documented in Phase 1 Wave 2's `docs/install/macos-gatekeeper.md`
(shipped by Plan 01-02 — Wave 2 scope) and reachable via the React
ErrorBoundary's deeplink button when this module flags the bundle as
quarantined.

This module is **detection only**. It never runs `xattr -cr` to clear the
quarantine — the app cannot fix its own quarantine state (the very process
calling `xattr -cr` would itself be quarantined and refused exec). Surface
the workaround to the user; they run `xattr -cr /Applications/VoiceStudio\\ Studio.app`
from Terminal once, and the next launch succeeds.

Plan: 01-03-PLAN.md (Phase 1 Wave 3)
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Optional

logger = logging.getLogger("omnivoice.gatekeeper")

#: Structured error class emitted on quarantine detection. The React
#: ErrorBoundary (wired in Plan 01-02) matches on this exact string to choose
#: the right docs deeplink.
ERROR_CLASS = "GATEKEEPER_QUARANTINE"


def _resolve_app_bundle_path() -> Optional[str]:
    """Walk up from ``sys.executable`` until we find a ``.app`` directory.

    When VoiceStudio is launched from an installed .app bundle, Python runs from
    `VoiceStudio.app/Contents/Resources/.venv/bin/python` (or similar),
    so ``sys.executable`` is several levels below the bundle root.

    Returns the .app path (e.g. ``/Applications/VoiceStudio.app``) or
    ``None`` for dev runs where we are not inside a bundle.
    """
    if sys.platform != "darwin":
        return None
    path = os.path.realpath(sys.executable)
    # Bound the walk so a pathological symlink loop cannot hang us.
    for _ in range(20):
        if path.endswith(".app"):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    return None


def is_app_quarantined(bundle_path: Optional[str] = None) -> bool:
    """Return True if the running .app bundle has the quarantine xattr.

    Parameters
    ----------
    bundle_path
        Override the detected bundle path. Mainly for testing — production
        callers should pass nothing and let :func:`_resolve_app_bundle_path`
        find the bundle via ``sys.executable``.

    Returns
    -------
    bool
        - False on non-macOS platforms (no Gatekeeper exists).
        - False on dev runs where we are not inside a .app bundle.
        - False when ``xattr`` is missing or errors (safe default — we'd
          rather miss a quarantine warning than crash a startup probe).
        - True when ``xattr -p com.apple.quarantine`` succeeds on the bundle.
    """
    if sys.platform != "darwin":
        return False

    bundle = bundle_path if bundle_path is not None else _resolve_app_bundle_path()
    if not bundle:
        return False

    try:
        result = subprocess.run(
            ["xattr", "-p", "com.apple.quarantine", bundle],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        # No xattr binary on PATH — extremely unlikely on macOS, but be safe.
        logger.debug("xattr binary not found; cannot probe quarantine state")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("xattr probe timed out after 5s")
        return False
    except OSError as e:
        logger.warning("xattr probe failed: %s", e)
        return False

    return result.returncode == 0


def quarantine_status() -> dict:
    """Return a dict describing the bundle's quarantine state.

    Shape:
        {"quarantined": bool, "bundle_path": str | None, "error_class": str | None}

    ``error_class`` is :data:`ERROR_CLASS` when ``quarantined`` is True, else
    None. The React frontend polls ``/system/quarantine-status`` and renders
    the docs deeplink when ``error_class`` is set.
    """
    bundle = _resolve_app_bundle_path()
    quarantined = is_app_quarantined(bundle)
    return {
        "quarantined": quarantined,
        "bundle_path": bundle,
        "error_class": ERROR_CLASS if quarantined else None,
    }
