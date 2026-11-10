// MIRROR OF backend/core/error_docs_map.py — keep in sync.
// The Python test_keys_match_python_map / test_keys_match_taxonomy guards
// the taxonomy on the backend; the `_KEYS` array below is the
// TS-side anchor (the keys-sync test imports it and asserts equality).
//
// This `BASE` constant is the SECOND hardcoded URL drift site: the
// canonical Python-side resolver is `backend/core/links.py`
// (`PROJECT_REPO_BLOB_MAIN`). The TS half runs in the browser and can't
// read `pyproject.toml`, so it gets a hand-maintained mirror. Centralising
// the URL on the TS side is a deferred hardening item — for now we accept the
// drift risk and rely on the keys-sync test + threat-model T-02-01
// to bound the blast radius.

import { openExternal } from '../api/external';

const BASE = 'https://github.com/debpalash/VoiceStudio/blob/main';

export const ERROR_DOCS: Record<string, string> = {
  GPU_ARCH_UNSUPPORTED: `${BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis`,
  // #2462 — the host ran out of system RAM (the only memory class a
  // CPU-only machine can produce).
  HOST_MEMORY_EXHAUSTED: `${BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis`,
  // The volume holding data / model cache / temp is full (ENOSPC, EDQUOT,
  // WinError 112/39).
  DISK_FULL: `${BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis`,
  WINDOWS_APP_CONTROL_BLOCKED: `${BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis`,
  AUDIO_IO_FAILED: `${BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis`,
  INVALID_MEDIA_FILE: `${BASE}/docs/electron-dubbing.md#unreadable-source-file`,

  DIARIZATION_LOAD_FAILED: `${BASE}/docs/features/diarization.md#troubleshooting`,
  DIARIZATION_MODEL_MISSING: `${BASE}/docs/features/diarization.md#local-installation-and-repair`,
  GATEKEEPER_QUARANTINE: `${BASE}/docs/install/macos.md#gatekeeper-quarantine`,
  APPIMAGE_WEBKIT_WHITESCREEN: `${BASE}/docs/install/linux.md#appimage-white-screen-on-fedora-44--ubuntu-2404`,
  PKG_RESOURCES_MISSING: `${BASE}/docs/install/troubleshooting.md#pkg_resources-missing`,
  HF_AUTH_FAILED: `${BASE}/docs/setup/huggingface-token.md`,
  // Issue #78 — pyannote gated-model license not accepted on HF.
  // Distinct from HF_AUTH_FAILED (which is a missing/invalid token).
  PYANNOTE_LICENSE_REQUIRED: `${BASE}/docs/features/diarization.md#license-acceptance-flow`,
  POCKETTTS_GATED_WEIGHTS: `${BASE}/docs/install/troubleshooting.md#pockettts-gated-weights`,
  // Install failure codes (installFailure.ts).
  ANTIVIRUS_BLOCKED: `${BASE}/docs/install/troubleshooting.md#antivirus_blocked`,
  EXEC_REFUSED: `${BASE}/docs/install/troubleshooting.md#exec_refused`,
  EXEC_NOEXEC_MOUNT: `${BASE}/docs/install/troubleshooting.md#exec_noexec_mount`,
  RUNTIME_DOWNLOAD_STALLED: `${BASE}/docs/install/troubleshooting.md#runtime_download_stalled`,
  RUNTIME_DOWNLOAD_INTERRUPTED: `${BASE}/docs/install/troubleshooting.md#runtime_download_interrupted`,
  RUNTIME_FOLDER_DENIED: `${BASE}/docs/install/troubleshooting.md#runtime_folder_denied`,
  RUNTIME_FILES_LOCKED: `${BASE}/docs/install/troubleshooting.md#runtime_files_locked`,
  RUNTIME_IN_SYNCED_FOLDER: `${BASE}/docs/install/troubleshooting.md#runtime_in_synced_folder`,
  SYSTEM_LIBRARY_PROBLEM: `${BASE}/docs/install/troubleshooting.md#system_library_problem`,
  UNSUPPORTED_PLATFORM: `${BASE}/docs/install/troubleshooting.md#unsupported_platform`,
  SOURCE_ENV_INCOMPLETE: `${BASE}/docs/install/troubleshooting.md#source_env_incomplete`,
  BROKEN_VENV: `${BASE}/docs/install/troubleshooting.md#broken_venv`,
  NATIVE_ACCESS_VIOLATION: `${BASE}/docs/install/troubleshooting.md#native_access_violation`,
  NATIVE_ILLEGAL_INSTRUCTION: `${BASE}/docs/install/troubleshooting.md#native_illegal_instruction`,
  NATIVE_CRASH: `${BASE}/docs/install/troubleshooting.md#native_crash`,
  STARTUP_TIMEOUT_SILENT: `${BASE}/docs/install/troubleshooting.md#startup_timeout_silent`,
  STARTUP_TIMEOUT_OUTPUT: `${BASE}/docs/install/troubleshooting.md#startup_timeout_output`,
  BACKEND_STARTUP_FAILED: `${BASE}/docs/install/troubleshooting.md#backend_startup_failed`,
  PORT_IN_USE: `${BASE}/docs/install/troubleshooting.md#port_in_use`,
  ROCM_NOT_CONFIGURED: `${BASE}/docs/install/troubleshooting.md#rocm_not_configured`,
};

export const DEFAULT_DOCS = `${BASE}/docs/install/troubleshooting.md`;

// Deep-link for the Dub tab's "needs install" translation-engine popover.
// Reuses BASE so it can't drift from the other GitHub-blob links above.
export const TRANSLATION_ENGINES_DOCS = `${BASE}/docs/dubbing/translation-engines.md#installing-optional-translation-engines-from-source-vs-packaged-build`;

// Locked taxonomy keys — Phase 5 bug reporter consumes this exact set.
// Adding a class is a contract change; update the Python map at the
// same time (`backend/core/error_docs_map.py`).
export const ERROR_CLASS_KEYS = [
  'GPU_ARCH_UNSUPPORTED',
  'HOST_MEMORY_EXHAUSTED',
  'DISK_FULL',
  'WINDOWS_APP_CONTROL_BLOCKED',
  'AUDIO_IO_FAILED',
  'INVALID_MEDIA_FILE',

  'DIARIZATION_LOAD_FAILED',
  'DIARIZATION_MODEL_MISSING',
  'GATEKEEPER_QUARANTINE',
  'APPIMAGE_WEBKIT_WHITESCREEN',
  'PKG_RESOURCES_MISSING',
  'HF_AUTH_FAILED',
  'PYANNOTE_LICENSE_REQUIRED',
  'POCKETTTS_GATED_WEIGHTS',
  'ANTIVIRUS_BLOCKED',
  'EXEC_REFUSED',
  'EXEC_NOEXEC_MOUNT',
  'RUNTIME_DOWNLOAD_STALLED',
  'RUNTIME_DOWNLOAD_INTERRUPTED',
  'RUNTIME_FOLDER_DENIED',
  'RUNTIME_FILES_LOCKED',
  'RUNTIME_IN_SYNCED_FOLDER',
  'SYSTEM_LIBRARY_PROBLEM',
  'UNSUPPORTED_PLATFORM',
  'SOURCE_ENV_INCOMPLETE',
  'BROKEN_VENV',
  'NATIVE_ACCESS_VIOLATION',
  'NATIVE_ILLEGAL_INSTRUCTION',
  'NATIVE_CRASH',
  'STARTUP_TIMEOUT_SILENT',
  'STARTUP_TIMEOUT_OUTPUT',
  'BACKEND_STARTUP_FAILED',
  'PORT_IN_USE',
  'ROCM_NOT_CONFIGURED',
] as const;

export type ErrorClass = (typeof ERROR_CLASS_KEYS)[number];

/**
 * Heuristic error message → ErrorClass classifier. ErrorBoundary uses this
 * when the thrown Error doesn't carry an explicit `errorClass` property.
 */
export function classifyError(error: unknown): ErrorClass | null {
  const message =
    (error as { message?: string } | null | undefined)?.message ?? String(error ?? '');
  const lower = message.toLowerCase();
  if (/media file is damaged or incomplete/.test(lower)) return 'INVALID_MEDIA_FILE';
  // Same signatures as backend/core/failure.py `is_disk_full_error`.
  if (
    /no space left|disk quota exceeded|disc quota exceeded|not enough space on the disk/.test(
      lower,
    ) ||
    /\berrno 28\b|\bwinerror (?:112|39)\b/.test(lower)
  ) {
    return 'DISK_FULL';
  }
  if (/pkg_resources/.test(lower)) return 'PKG_RESOURCES_MISSING';
  if (
    /pocket(?:tts|[-_ ]tts)|kyutai/.test(lower) &&
    /gated|share your contact|access (?:agreement|conditions)/.test(lower)
  ) {
    return 'POCKETTTS_GATED_WEIGHTS';
  }
  const diarisation = /pyannote|diari[sz]ation|sortformer/.test(lower);
  const accessFailure =
    /gated|unauthorized|forbidden|401|403|accept the|license|user conditions/.test(lower);
  if (diarisation && !accessFailure) {
    if (
      /files are missing|filenotfounderror|localentrynotfounderror|model is missing/.test(lower)
    ) {
      return 'DIARIZATION_MODEL_MISSING';
    }
    if (/failed to load|load failed|runtime failed/.test(lower)) {
      return 'DIARIZATION_LOAD_FAILED';
    }
  }
  if (
    (diarisation && accessFailure) ||
    /\bgated\b/.test(lower) ||
    /accept.*(license|terms|conditions)/.test(lower)
  ) {
    return 'PYANNOTE_LICENSE_REQUIRED';
  }
  if (/\b401\b/.test(lower) || /hfhub|hfhubhttp/.test(lower) || /unauthorized/.test(lower)) {
    return 'HF_AUTH_FAILED';
  }
  if (/webkit/.test(lower) || /white\s*screen/.test(lower)) {
    return 'APPIMAGE_WEBKIT_WHITESCREEN';
  }
  // Gatekeeper match: includes the literal "is damaged" macOS phrasing
  // (English + Chinese 已损坏 per issue #72). Lower-case test is safe;
  // '已损坏' is unaffected by lowercasing.
  if (
    /quarantine/.test(lower) ||
    /gatekeeper/.test(lower) ||
    /\bdamaged\b/.test(lower) ||
    /已损坏/.test(message || '')
  )
    return 'GATEKEEPER_QUARANTINE';
  return null;
}

export function urlFor(errorClass: ErrorClass | string | null | undefined): string {
  if (!errorClass) return DEFAULT_DOCS;
  return ERROR_DOCS[errorClass] ?? DEFAULT_DOCS;
}

/** Open the docs URL for `errorClass` in the user's default browser. */
export async function openDocsFor(
  errorClass: ErrorClass | string | null | undefined,
): Promise<void> {
  const url = ERROR_DOCS[errorClass as string] ?? DEFAULT_DOCS;
  await openExternal(url);
}
