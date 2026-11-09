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
    # #2462 — the host ran out of system RAM. A CPU-only machine has no other
    # memory device, so without its own class every OOM there reached the user
    # as the unclassified floor message and the report could only say
    # `RuntimeError`.
    "HOST_MEMORY_EXHAUSTED": f"{_BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis",
    # The volume holding data / model cache / temp is full (ENOSPC, EDQUOT,
    # WinError 112/39); the error journal already used this class name.
    "DISK_FULL": f"{_BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis",
    "WINDOWS_APP_CONTROL_BLOCKED": f"{_BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis",
    "AUDIO_IO_FAILED": f"{_BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis",
    "INVALID_MEDIA_FILE": f"{_BASE}/docs/electron-dubbing.md#unreadable-source-file",

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
    # Install failure codes, classified by Electron main before Python can run.
    "ANTIVIRUS_BLOCKED": f"{_BASE}/docs/install/troubleshooting.md#antivirus_blocked",
    "EXEC_REFUSED": f"{_BASE}/docs/install/troubleshooting.md#exec_refused",
    "EXEC_NOEXEC_MOUNT": f"{_BASE}/docs/install/troubleshooting.md#exec_noexec_mount",
    "RUNTIME_DOWNLOAD_STALLED": f"{_BASE}/docs/install/troubleshooting.md#runtime_download_stalled",
    "RUNTIME_DOWNLOAD_INTERRUPTED": f"{_BASE}/docs/install/troubleshooting.md#runtime_download_interrupted",
    "RUNTIME_FOLDER_DENIED": f"{_BASE}/docs/install/troubleshooting.md#runtime_folder_denied",
    "RUNTIME_FILES_LOCKED": f"{_BASE}/docs/install/troubleshooting.md#runtime_files_locked",
    "RUNTIME_IN_SYNCED_FOLDER": f"{_BASE}/docs/install/troubleshooting.md#runtime_in_synced_folder",
    "SYSTEM_LIBRARY_PROBLEM": f"{_BASE}/docs/install/troubleshooting.md#system_library_problem",
    "UNSUPPORTED_PLATFORM": f"{_BASE}/docs/install/troubleshooting.md#unsupported_platform",
    "SOURCE_ENV_INCOMPLETE": f"{_BASE}/docs/install/troubleshooting.md#source_env_incomplete",
    "BROKEN_VENV": f"{_BASE}/docs/install/troubleshooting.md#broken_venv",
    "NATIVE_ACCESS_VIOLATION": f"{_BASE}/docs/install/troubleshooting.md#native_access_violation",
    "NATIVE_ILLEGAL_INSTRUCTION": f"{_BASE}/docs/install/troubleshooting.md#native_illegal_instruction",
    "NATIVE_CRASH": f"{_BASE}/docs/install/troubleshooting.md#native_crash",
    "STARTUP_TIMEOUT_SILENT": f"{_BASE}/docs/install/troubleshooting.md#startup_timeout_silent",
    "STARTUP_TIMEOUT_OUTPUT": f"{_BASE}/docs/install/troubleshooting.md#startup_timeout_output",
    "BACKEND_STARTUP_FAILED": f"{_BASE}/docs/install/troubleshooting.md#backend_startup_failed",
    "PORT_IN_USE": f"{_BASE}/docs/install/troubleshooting.md#port_in_use",
    "ROCM_NOT_CONFIGURED": f"{_BASE}/docs/install/troubleshooting.md#rocm_not_configured",
}

DEFAULT_DOCS: str = f"{_BASE}/docs/install/troubleshooting.md"


def lookup(error_class: str | None) -> str:
    """Return the docs URL for `error_class`, or DEFAULT_DOCS when the
    class is None / unknown."""
    return ERROR_DOCS.get(error_class or "", DEFAULT_DOCS)
