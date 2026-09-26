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
  WINDOWS_APP_CONTROL_BLOCKED: `${BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis`,
  AUDIO_IO_FAILED: `${BASE}/docs/install/troubleshooting.md#generation-failure-diagnosis`,

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
  'WINDOWS_APP_CONTROL_BLOCKED',
  'AUDIO_IO_FAILED',

  'DIARIZATION_LOAD_FAILED',
  'DIARIZATION_MODEL_MISSING',
  'GATEKEEPER_QUARANTINE',
  'APPIMAGE_WEBKIT_WHITESCREEN',
  'PKG_RESOURCES_MISSING',
  'HF_AUTH_FAILED',
  'PYANNOTE_LICENSE_REQUIRED',
  'POCKETTTS_GATED_WEIGHTS',
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
