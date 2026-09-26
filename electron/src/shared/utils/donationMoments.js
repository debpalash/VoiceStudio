/**
 * Donation moments — the "kind Clippy" eligibility engine behind the footer's
 * donation popover. This is the ONE donation-prompt decision point in the app
 * (it supersedes the spec-007 postcard toast; those call sites now route
 * here, so a single success can never fire two competing donation UIs).
 *
 * Modeled on what actually works for major OSS — value-moment timing, strict
 * rarity, permanent opt-out — and explicitly NOT core-js-style nagging:
 *
 *   - Call `recordValueMoment(kind)` ONLY right after a genuine
 *     value-creation success (export saved, dub finished, audiobook rendered,
 *     batch job done, clone saved). Never on errors, never at app start.
 *   - A prompt is shown only when EVERY gate passes:
 *       (a) ≥ MIN_LIFETIME_MOMENTS lifetime value-moments — brand-new users
 *           are never prompted;
 *       (b) ≥ PROMPT_COOLDOWN_DAYS since the last prompt was shown; the FIRST
 *           prompt additionally waits ≥ FIRST_PROMPT_MIN_DAYS after the very
 *           first value-moment;
 *       (c) not permanently opted out — "Don't ask again" is terminal, and
 *           the legacy postcard opt-out persisted inside `omnivoice.app` is
 *           honored too;
 *       (d) at most once per app session;
 *       (e) a PROMPT_PROBABILITY random roll — most eligible moments stay
 *           silent, so the prompt reads as a rare aside, not a toll booth.
 *   - State lives in localStorage under `omnivoice.donate.*` so it survives
 *     restarts without touching the app DB.
 *
 * Pure module: the clock and the RNG are injectable so every gate is
 * deterministic under test. The one deliberate side effect: when a prompt IS
 * eligible, the shown-state is committed immediately (session cap + cooldown
 * anchor — a second success in the same tick cannot double-fire) and
 * DONATION_MOMENT_EVENT is dispatched so LogsFooter can render the popover.
 * Call sites stay one line and never handle UI.
 */

// ── Gate thresholds (every threshold a named constant) ─────────────────────
/** (a) Lifetime value-moments required before the first prompt is possible. */
export const MIN_LIFETIME_MOMENTS = 5;
/** (b) Days that must pass between two prompts. */
export const PROMPT_COOLDOWN_DAYS = 7;
/** (b) Days after the FIRST value-moment before the first prompt may show. */
export const FIRST_PROMPT_MIN_DAYS = 3;
/** (e) Probability that an otherwise-eligible moment actually prompts. */
export const PROMPT_PROBABILITY = 0.25;

/** Number of rotating friendly lines (footer_donate.line_1..N in en.json). */
export const DONATE_LINE_COUNT = 4;

/** Window event dispatched when a prompt should show. detail: {kind, line}. */
export const DONATION_MOMENT_EVENT = 'omnivoice:donation-moment';

// ── localStorage keys (omnivoice.donate.*) ─────────────────────────────────
export const LS_MOMENT_COUNT = 'omnivoice.donate.momentCount';
export const LS_FIRST_MOMENT_AT = 'omnivoice.donate.firstMomentAt';
export const LS_LAST_PROMPT_AT = 'omnivoice.donate.lastPromptAt';
export const LS_PROMPT_COUNT = 'omnivoice.donate.promptCount';
export const LS_OPT_OUT = 'omnivoice.donate.optOut';
/** zustand persist blob — the postcard-era `optedOut` flag lives in here. */
const LEGACY_STORE_KEY = 'omnivoice.app';

const DAY_MS = 24 * 60 * 60 * 1000;

/** (d) Session cap — module state, reset only by a fresh app launch. */
let sessionPromptShown = false;

/** Non-negative finite number from storage, or 0 for missing/corrupt values. */
function readNum(key) {
  const v = Number(localStorage.getItem(key));
  return Number.isFinite(v) && v > 0 ? v : 0;
}

/**
 * (c) Permanent opt-out — true if the user ever clicked "Don't ask again",
 * on this popover OR on the legacy postcard prompt (whose flag persists in
 * the zustand `omnivoice.app` blob). A promise made once is kept forever.
 */
export function isDonationOptedOut() {
  try {
    if (localStorage.getItem(LS_OPT_OUT) === '1') return true;
  } catch {
    return false; // storage unavailable → nothing persisted, nothing to honor
  }
  try {
    const raw = localStorage.getItem(LEGACY_STORE_KEY);
    if (raw && JSON.parse(raw)?.state?.optedOut === true) return true;
  } catch {
    /* unreadable legacy blob → fall through */
  }
  return false;
}

/** "Don't ask again" — terminal. No donation prompt will ever show again. */
export function optOutOfDonationMoments() {
  try {
    localStorage.setItem(LS_OPT_OUT, '1');
  } catch {
    /* storage unavailable — the session cap still silences this launch */
  }
  sessionPromptShown = true;
}

/**
 * Record one genuine value-creation success and decide whether to prompt.
 *
 * @param {string} kind  what succeeded ('export' | 'dub' | 'audiobook' |
 *                       'batch' | 'clone' | ...) — carried in the event detail.
 * @param {{ now?: number, random?: () => number }} [opts]  injectable clock
 *                       (epoch ms) and RNG for deterministic tests.
 * @returns {{ show: boolean, reason: string, line?: number }}
 */
export function recordValueMoment(kind, { now = Date.now(), random = Math.random } = {}) {
  let momentCount;
  let firstMomentAt;
  try {
    momentCount = readNum(LS_MOMENT_COUNT) + 1;
    firstMomentAt = readNum(LS_FIRST_MOMENT_AT) || now;
    localStorage.setItem(LS_MOMENT_COUNT, String(momentCount));
    localStorage.setItem(LS_FIRST_MOMENT_AT, String(firstMomentAt));
  } catch {
    return { show: false, reason: 'storage-unavailable' };
  }

  // Gates, cheapest-first. Counters above are recorded regardless, so the
  // lifetime history stays truthful even while a gate blocks.
  if (isDonationOptedOut()) return { show: false, reason: 'opted-out' };
  if (sessionPromptShown) return { show: false, reason: 'session-cap' };
  if (momentCount < MIN_LIFETIME_MOMENTS) return { show: false, reason: 'too-few-moments' };

  const lastPromptAt = readNum(LS_LAST_PROMPT_AT);
  if (lastPromptAt > 0) {
    if (now - lastPromptAt < PROMPT_COOLDOWN_DAYS * DAY_MS) {
      return { show: false, reason: 'cooldown' };
    }
  } else if (now - firstMomentAt < FIRST_PROMPT_MIN_DAYS * DAY_MS) {
    return { show: false, reason: 'first-prompt-delay' };
  }

  if (random() >= PROMPT_PROBABILITY) return { show: false, reason: 'lost-roll' };

  // Eligible. Commit the shown-state BEFORE dispatching so a second success
  // in the same tick can't double-fire, then rotate through the lines.
  const line = readNum(LS_PROMPT_COUNT) % DONATE_LINE_COUNT;
  sessionPromptShown = true;
  try {
    localStorage.setItem(LS_LAST_PROMPT_AT, String(now));
    localStorage.setItem(LS_PROMPT_COUNT, String(readNum(LS_PROMPT_COUNT) + 1));
  } catch {
    /* best effort — the session cap alone still prevents same-run repeats */
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(DONATION_MOMENT_EVENT, { detail: { kind, line } }));
  }
  return { show: true, reason: 'shown', line };
}

/** Test-only: clear the once-per-session cap (simulates a fresh launch). */
export function _resetDonationSessionForTests() {
  sessionPromptShown = false;
}
