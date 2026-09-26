/**
 * Schema guard for the persisted `omni_ui` localStorage blob.
 *
 * The #1067 class ("app got empty" after quit-mid-job) was healed per-field —
 * dubStep got a clamp, vdStates got a shape merge — but every OTHER field was
 * restored unvalidated inside one big try/catch, so (a) any future persisted
 * field re-opens the class, and (b) a single malformed value (say, dubSegments
 * saved as a string by a buggy build) throws mid-restore and silently discards
 * every field after it. This is the generic pass the audit called for: a
 * whitelist of known keys with a type/shape check each; unknown keys and
 * malformed values are dropped (with one console warning naming them) instead
 * of crashing or leaking into state.
 *
 * Deliberately NOT a validation library: predicates only, no coercion — a
 * value that fails its predicate is exactly the value we don't want in state.
 */

const isStr = (v) => typeof v === 'string';
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const isBool = (v) => typeof v === 'boolean';
const isObj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
const isArrOfObj = (v) => Array.isArray(v) && v.every(isObj);
const isArr = (v) => Array.isArray(v);

/** key → predicate for every field the restore path reads. Adding a new
 * persisted field REQUIRES adding it here — restore drops unknown keys. */
export const OMNI_UI_SCHEMA = {
  uiScale: isNum,
  text: isStr,
  mode: isStr,
  defineMethod: isStr,
  vdStates: isObj, // completed to full CATEGORIES shape by the caller (#983)
  language: isStr,
  isSidebarCollapsed: isBool,
  sidebarTab: isStr,
  dubJobId: isStr,
  dubFilename: isStr,
  dubDuration: isNum,
  dubSegments: isArrOfObj,
  dubLang: isStr,
  dubLangCode: isStr,
  dubTracks: isArr,
  dubStep: isStr, // additionally clamped by clampRestoredDubStep (#1067)
  dubTranscript: isStr,
  exportTracks: isObj,
  preserveBg: isBool,
  defaultTrack: isStr,
  exportHistory: isArr,
  speed: isNum,
  steps: isNum,
  cfg: isNum,
  denoise: isBool,
  showOverrides: isBool,
};

/**
 * Return a copy of `saved` containing only whitelisted, well-shaped fields.
 * Never throws; on non-object input returns {}.
 */
export function sanitizeOmniUi(saved) {
  if (!isObj(saved)) return {};
  const out = {};
  const dropped = [];
  for (const [key, value] of Object.entries(saved)) {
    const check = OMNI_UI_SCHEMA[key];
    if (!check) {
      dropped.push(key);
      continue;
    }
    if (value === undefined || value === null) continue;
    if (check(value)) out[key] = value;
    else dropped.push(key);
  }
  if (dropped.length) {
    // One line, not one per key — this fires at most once per app start.
    console.warn(`omni_ui restore: dropped unknown/malformed field(s): ${dropped.join(', ')}`);
  }
  return out;
}
