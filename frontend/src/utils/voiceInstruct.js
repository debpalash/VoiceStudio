// Explicit .js extension so this module also loads under plain node ESM
// (tests/frontend/describeVoice.test.mjs runs via `node --test`).
import { CATEGORIES } from './constants.js';

// Lowercased tag -> its category name. The engine validator
// (omnivoice/models/omnivoice.py::_resolve_instruct) accepts ONLY these
// whitelist tags, one per category — so the Voice Design payload must be built
// from them, not raw free text.
const TAG_TO_CATEGORY = (() => {
  const map = {};
  for (const [cat, values] of Object.entries(CATEGORIES)) {
    for (const v of values) {
      if (v !== 'Auto') map[v.toLowerCase()] = cat;
    }
  }
  return map;
})();

// EnglishAccent and ChineseDialect are two independent CATEGORIES entries (so
// the picker renders them as two independent controls), but the engine
// (omnivoice/models/omnivoice.py::_resolve_instruct) rejects any instruct that
// sets both at once: "Cannot mix Chinese dialect and English accent in a
// single instruct." Group them here so every place that builds or restores
// vdStates enforces the SAME exclusivity instead of relying on a round trip
// to the engine to catch it (#1771). A future exclusive pair joins by adding
// an entry here — no second ad-hoc check needed.
const EXCLUSIVE_GROUPS = {
  EnglishAccent: 'accent_dialect',
  ChineseDialect: 'accent_dialect',
};
const groupOf = (cat) => EXCLUSIVE_GROUPS[cat] || cat;

/**
 * Resolve cross-category exclusivity (#1771) on a complete vdStates-shaped
 * object: within each exclusive group, the first non-'Auto' category (in
 * CATEGORIES key order) wins and the rest are reset to 'Auto'.
 *
 * @param {Record<string,string>} states complete vdStates (every CATEGORIES key present)
 * @returns {{ states: Record<string,string>, cleared: string[] }} `cleared`
 *   lists the category names that got reset back to 'Auto'.
 */
function resolveVdConflicts(states) {
  const out = { ...states };
  const claimedGroups = new Set();
  const cleared = [];
  for (const cat of Object.keys(CATEGORIES)) {
    const value = out[cat];
    if (!value || value === 'Auto') continue;
    const group = groupOf(cat);
    if (claimedGroups.has(group)) {
      out[cat] = 'Auto';
      cleared.push(cat);
    } else {
      claimedGroups.add(group);
    }
  }
  return { states: out, cleared };
}

/**
 * Apply one picker change to `vdStates`, enforcing the accent/dialect
 * exclusivity live (#1771) instead of letting the picker build a payload the
 * engine will 400 on. Picking a non-'Auto' value in one category of an
 * exclusive group clears any other category already set within that group.
 *
 * @param {Record<string,string>} vdStates current picker state
 * @param {string} category CATEGORIES key being changed (e.g. 'ChineseDialect')
 * @param {string} value new value for that category ('Auto' clears it)
 * @returns {{ vdStates: Record<string,string>, clearedCategory: string|null }}
 *   `clearedCategory` names the OTHER category that got reset, so the caller
 *   can show a visible reason instead of a silent reset; null when nothing
 *   needed clearing.
 */
export function applyVdState(vdStates, category, value) {
  const next = { ...vdStates, [category]: value };
  let clearedCategory = null;
  if (value && value !== 'Auto') {
    const group = groupOf(category);
    for (const otherCat of Object.keys(EXCLUSIVE_GROUPS)) {
      if (otherCat === category || groupOf(otherCat) !== group) continue;
      if (next[otherCat] && next[otherCat] !== 'Auto') {
        next[otherCat] = 'Auto';
        clearedCategory = otherCat;
      }
    }
  }
  return { vdStates: next, clearedCategory };
}

/**
 * Build a validator-safe Voice Design instruct from the category dropdown
 * selections (`vdStates`) plus the optional free-text field.
 *
 * - Dropdowns contribute one valid tag per category (they win their category).
 * - Free-text items are accepted only if they're a known tag whose category is
 *   still open. The rest are returned split into buckets so the caller can
 *   show an accurate message:
 *     - `unsupported`: not a known tag (free-text prose) — the #115 case;
 *     - `duplicates`:  a valid tag whose category was already set (e.g. a
 *       dropdown's `low pitch` outranks a typed `high pitch`) — the #114 case;
 *     - `conflicts`:   a valid tag whose EXCLUSIVE GROUP was already claimed
 *       by a different category — e.g. an accent already set blocks a typed
 *       dialect, and vice versa (the engine's "Cannot mix Chinese dialect and
 *       English accent" rule, #1771). The live picker (`applyVdState`) and
 *       vdStates restoration (`mergeDescribedAttrs`, `instructToVdStates`)
 *       already keep this from happening in the common case; this is the
 *       last-resort backstop for anything that reaches here regardless.
 *
 * @returns {{ instruct: string, unsupported: string[], duplicates: string[], conflicts: string[] }}
 */
export function buildDesignInstruct(vdStates = {}, freeText = '') {
  const byCategory = {};
  const byGroup = {};
  const unsupported = [];
  const duplicates = [];
  const conflicts = [];

  const claim = (cat, item) => {
    byCategory[cat] = item;
    byGroup[groupOf(cat)] = cat;
  };

  // Dropdowns first — they win their category (and, within an exclusive
  // group, the first dropdown wins the group).
  for (const value of Object.values(vdStates || {})) {
    const item = String(value ?? '').trim();
    if (!item || item === 'Auto') continue;
    const cat = TAG_TO_CATEGORY[item.toLowerCase()];
    if (!cat) {
      // A dropdown value that isn't in CATEGORIES means the option list and the
      // whitelist have drifted — silently dropping it would hide a real bug.
      console.warn(`buildDesignInstruct: unknown dropdown value "${item}" (not in CATEGORIES)`);
      continue;
    }
    if (cat in byCategory) continue;
    if (groupOf(cat) in byGroup) {
      conflicts.push(item.toLowerCase());
      continue;
    }
    claim(cat, item.toLowerCase());
  }

  // Free-text field — accept valid tags in open categories; bucket the rest.
  for (const raw of String(freeText || '').split(/[,，]/)) {
    const item = raw.trim();
    if (!item) continue;
    const cat = TAG_TO_CATEGORY[item.toLowerCase()];
    if (!cat) {
      unsupported.push(item);
    } else if (cat in byCategory) {
      duplicates.push(item);
    } else if (groupOf(cat) in byGroup) {
      conflicts.push(item);
    } else {
      claim(cat, item.toLowerCase());
    }
  }

  return { instruct: Object.values(byCategory).join(', '), unsupported, duplicates, conflicts };
}

/**
 * Convert a persona's validator-token instruct into a fresh, complete vdStates
 * object. This is intentionally a projection, not a merge: Gallery hand-offs
 * must replace every prior slider value, including categories the persona omits.
 *
 * Unknown tokens are ignored. If multiple valid tokens claim one category, the
 * first wins and later conflicts are ignored, so malformed external persona data
 * can never produce conflicting controls or reach the engine unchanged.
 *
 * @param {string} instruct comma-separated validator tokens
 * @returns {Record<string, string>} complete vdStates (every category present)
 */
export function instructToVdStates(instruct = '') {
  const out = Object.fromEntries(Object.keys(CATEGORIES).map((cat) => [cat, 'Auto']));
  const claimed = new Set();

  for (const raw of String(instruct || '').split(/[,，]/)) {
    const normalized = raw.trim().toLowerCase();
    if (!normalized) continue;
    const cat = TAG_TO_CATEGORY[normalized];
    if (!cat) continue;
    // #1771: EnglishAccent and ChineseDialect share an exclusive group (the
    // engine's dialect-vs-accent rule) — claiming either blocks the other, not
    // just itself, so a poisoned/legacy instruct with both never resurrects a
    // conflicting picker state.
    const group = groupOf(cat);
    if (claimed.has(group)) continue;
    const canonical = CATEGORIES[cat].find((value) => value.toLowerCase() === normalized);
    if (!canonical || canonical === 'Auto') continue;
    out[cat] = canonical;
    claimed.add(group);
  }

  return out;
}

/**
 * Which `profile_id` (if any) to forward in DESIGN mode.
 *
 * Design mode generates a voice from attributes (the `instruct` built from the
 * sliders). A *clone* profile (reference audio, no instruct) must NOT be sent:
 * the backend would clone that voice, and its gender/timbre then overrides the
 * design attributes — so e.g. "Male" appears to do nothing (#674). A *design*
 * profile (carries an instruct) is fine to forward (re-render a designed voice).
 *
 * Conservative: only a KNOWN clone is suppressed; an unknown id (profiles not
 * loaded yet) or a design profile passes through, preserving existing behavior.
 *
 * @param {string} selectedProfile  selected profile id (or '')
 * @param {Array}  profiles         loaded profiles ({ id, instruct? })
 * @returns {string|null} the id to send, or null to omit it
 */
export function designModeProfileId(selectedProfile, profiles) {
  if (!selectedProfile) return null;
  const p = (profiles || []).find((x) => x && x.id === selectedProfile);
  if (p && !p.instruct) return null; // known clone → omit so it can't hijack the design
  return selectedProfile;
}

/**
 * Coerce an instruct value to the STRING that belongs in the FormData/payload.
 * `buildDesignInstruct()` returns `{ instruct, unsupported, duplicates }`, and
 * passing that object to `FormData.append` string-coerced it to the literal
 * `"[object Object]"`, poisoning saved design profiles (#550 #545 #542 #537
 * #530 #525). Always run instruct through this before sending it.
 *
 * @param {string | { instruct?: string } | null | undefined} instruct
 * @returns {string}
 */
export function instructToFormValue(instruct) {
  if (typeof instruct === 'string') return instruct;
  if (instruct && typeof instruct === 'object') return String(instruct.instruct ?? '');
  return '';
}

/**
 * Project the backend's "describe your voice" result (#317) onto a fresh
 * vdStates object. The description drives the *whole* parameter set — matched
 * categories get their token, everything else resets to 'Auto' — so retyping
 * a description never leaves stale tokens from the previous one behind. The
 * user can still hand-tune any control afterwards.
 *
 * Tokens are validated against CATEGORIES so a drifted/older backend can
 * never inject a value the picker (and the instruct whitelist) doesn't know.
 *
 * Also the shared "restore a complete vdStates from external data" path for a
 * saved design profile's `vd_states` (`hooks/useProfiles.js`) and an imported
 * project's persisted state (`hooks/useAppData.js`) — both can carry an
 * EnglishAccent and a ChineseDialect picked together (a profile saved before
 * #1771, or a hand-edited/imported file), so this resolves that exclusivity
 * the same way the live picker does before it ever reaches the form.
 *
 * @param {Record<string, string>} attrs  backend response `attrs`
 * @returns {Record<string, string>} complete vdStates (every category present)
 */
export function mergeDescribedAttrs(attrs = {}) {
  const out = {};
  for (const [cat, options] of Object.entries(CATEGORIES)) {
    const v = attrs?.[cat];
    out[cat] = v && v !== 'Auto' && options.includes(v) ? v : 'Auto';
  }
  return resolveVdConflicts(out).states;
}
