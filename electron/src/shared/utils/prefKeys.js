/**
 * Registry of every localStorage key VoiceStudio uses, split into the
 * preferences that Settings → Factory reset clears and the keys it must
 * preserve. Factory reset promises "reset ALL in-app preferences" — before
 * this registry it only removed the zustand blob ('omnivoice.app'), leaving
 * nav-rail side, capture live-typing, stories speed, dismissed-tip flags and
 * the legacy 'omni_ui' blob behind. Long-form user data now lives separately
 * in IndexedDB and is intentionally preserved by a preferences-only reset.
 *
 * Adding a new persisted preference? Pick a key under one of
 * PREF_KEY_PREFIXES (preferred: 'omnivoice.<area>.<name>') and factory reset
 * covers it automatically. Keys that must NOT be wiped (user data,
 * connection/credentials) go in PRESERVED_KEYS with a reason.
 * utils/prefKeys.test.js scans the source tree and fails on any localStorage
 * key that is in neither bucket.
 */
import { suspendJsonWrites } from './coalescedJsonStorage';
import { preserveRevisionedLongformFallback } from './longformPersistence';

const APPLICATION_STATE_KEY = 'omnivoice.app';

/** Prefixes owned by UI preferences — factory reset clears every match. */
export const PREF_KEY_PREFIXES = [
  'omnivoice.', // zustand blob ('omnivoice.app') + navRailSide, logs.*, settings.category, donate.*, recents.*, dismissed-tip flags
  'omni_capture_', // CaptureWidget mode + live-typing
  'ov_stories_', // StoriesEditor global speed
];

/** Exact preference keys that don't fit a prefix (legacy spellings). */
export const PREF_KEYS = [
  'omni_ui', // legacy pre-zustand UI blob (useAppData shim)
  'dismissed_lang_suggestion', // BootstrapSplash language-suggestion dismissal
];

/**
 * Keys factory reset must NEVER touch:
 *  - 'ov_backend_url': remote-backend connection target.
 *  - 'ov_api_key': legacy master credential awaiting migration into a scoped
 *    session (api/client.ts bootstrap deletes it after the first SUCCESSFUL
 *    exchange). Connection credential like 'ov_backend_url': wiping it before
 *    that migration succeeds strands a remote-backend user whose backend is
 *    unreachable — the stored copy is the only one they have.
 *  - 'omni_transcriptions': dictation history — user DATA, not a preference.
 *  - 'ov_last_backend_contact': crash diagnostics (#1164) — sessionStorage
 *    timestamp of the backend's last response; not a preference, and wiping
 *    it would erase the "was it ever answering?" evidence mid-incident.
 *  - 'ov_admin_session': short-lived sessionStorage connection state. It is
 *    cleared by logout/backend switching, not by localStorage preference reset.
 *  - 'ov_stale_chunk_reload': sessionStorage timestamp of the one automatic
 *    reload ErrorBoundary performs when a lazy chunk went missing (dev-server
 *    restart / new build under an open tab). Per-tab loop guard, not a
 *    preference — and clearing it mid-incident would re-arm the reload.
 */
export const PRESERVED_KEYS = [
  'ov_backend_url',
  'ov_api_key',
  'omni_transcriptions',
  'ov_last_backend_contact',
  'ov_admin_session',
  'ov_stale_chunk_reload',
];

/** True when `key` is a resettable in-app preference. */
export function isPrefKey(key) {
  if (PRESERVED_KEYS.includes(key)) return false;
  return PREF_KEYS.includes(key) || PREF_KEY_PREFIXES.some((p) => key.startsWith(p));
}

/**
 * Reset every persisted in-app preference (and nothing else) in storage.
 * Returns the list of keys that were reset.
 */
export function clearLocalPreferences(storage = window.localStorage) {
  const resumeWrites = suspendJsonWrites(isPrefKey);
  try {
    const storageLength = storage.length;
    const keys =
      typeof storageLength === 'number' && typeof storage.key === 'function'
        ? Array.from({ length: storageLength }, (_, i) => storage.key(i))
        : Object.keys(storage);
    const doomed = keys.filter((k) => k && isPrefKey(k));
    const longformFallback =
      doomed.includes(APPLICATION_STATE_KEY) && typeof storage.getItem === 'function'
        ? preserveRevisionedLongformFallback(storage.getItem(APPLICATION_STATE_KEY))
        : null;
    for (const k of doomed) {
      if (
        k === APPLICATION_STATE_KEY &&
        longformFallback !== null &&
        typeof storage.setItem === 'function'
      ) {
        storage.setItem(APPLICATION_STATE_KEY, longformFallback);
      } else {
        storage.removeItem(k);
      }
    }
    // A successful reset intentionally keeps matching writes suspended until
    // the scheduled reload. Background state updates and pagehide must not
    // recreate preferences that the user just removed.
    return doomed;
  } catch (error) {
    // The reset did not complete, so restore normal persistence before the
    // existing ResetPanel error path reports the failure.
    resumeWrites();
    throw error;
  }
}
