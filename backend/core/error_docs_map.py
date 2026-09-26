"""Error class → docs URL mapping for the in-app deeplink button.

Used by the React ErrorBoundary's "Open docs for this error" button (via the
TypeScript mirror at `electron/src/shared/utils/errorDocsMap.ts`) and by the Phase 5
bug-reporter for "this error has a docs page" links.

The error taxonomy below is the contract — Phase 5 reporter consumes it,
the TS map mirrors it, and `test_error_docs_map.test_keys_match_taxonomy`
locks the key set. To add a new class:

  1. Pick a stable ALL_CAPS key (the API will live forever).
  2. Add the entry here.
  3. Mirror it in `electron/src/shared/utils/errorDocsMap.ts`.
  4. Update the `test_keys_match_taxonomy` set and the TS-side keys-sync test.
"""
from __future__ import annotations

from core import links

_BASE = links.PROJECT_REPO_BLOB_MAIN

ERROR_DOCS: dict[str, str] = {
    "GPU_ARCH_UNSUPPORTED": f"{_BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis",
    "WINDOWS_APP_CONTROL_BLOCKED": f"{_BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis",
    "AUDIO_IO_FAILED": f"{_BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis",

    "DIARIZATION_LOAD_FAILED": f"{_BASE}/docs/features/diarization.md#troubleshooting",
    "DIARIZATION_MODEL_MISSING": f"{_BASE}/docs/features/diarization.md#local-installation-and-repair",
    "GATEKEEPER_QUARANTINE":       f"{_BASE}/docs/install/macos.md#gatekeeper-quarantine",
    "APPIMAGE_WEBKIT_WHITESCREEN": f"{_BASE}/docs/install/linux.md#appimage-white-screen-on-fedora-44--ubuntu-2404",
    "PKG_RESOURCES_MISSING":       f"{_BASE}/docs/install/troubleshooting.md#pkg_resources-missing",
    "HF_AUTH_FAILED":              f"{_BASE}/docs/setup/huggingface-token.md",
    # Issue #78 — pyannote/speaker-diarization-3.1 + pyannote/segmentation-3.0
    # are both gated on HuggingFace. A valid HF_TOKEN by itself isn't enough:
    # the user must also click "Agree and access repository" on both model
    # pages. `docs/features/diarization.md` walks through that flow in its
    # "License acceptance flow" section, so the deeplink targets that anchor
    # directly. Distinct from HF_AUTH_FAILED (which is the more general
    # token-missing-or-invalid case pointing at the token-setup doc).
    "PYANNOTE_LICENSE_REQUIRED":   f"{_BASE}/docs/features/diarization.md#license-acceptance-flow",
    "POCKETTTS_GATED_WEIGHTS":     f"{_BASE}/docs/install/troubleshooting.md#pockettts-gated-weights",
}

DEFAULT_DOCS: str = f"{_BASE}/docs/install/troubleshooting.md"


def lookup(error_class: str | None) -> str:
    """Return the docs URL for `error_class`, or DEFAULT_DOCS when the
    class is None / unknown."""
    return ERROR_DOCS.get(error_class or "", DEFAULT_DOCS)
