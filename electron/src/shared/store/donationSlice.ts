/**
 * Donation prompt slice — the "Support VoiceStudio development" kawaii postcard state machine
 * (spec 007, Phase 2/3).
 *
 * NOTE (footer donation moments): the postcard UI and its call sites were
 * superseded by `utils/donationMoments.js` + the LogsFooter popover — the
 * single donation-prompt surface now. This slice stays because its persisted
 * `optedOut` flag is a promise made to existing users ("Don't ask again" is
 * terminal): donationMoments honors it via the persisted `omnivoice.app`
 * blob, and the popover's opt-out mirrors into it. Do not re-wire prompts
 * through `recordDonationSuccess` without consolidating with donationMoments.
 *
 * Design goals (anti-dark-pattern):
 *   - NEVER on error / in-progress / setup / first-run.
 *   - Success-only: only ever evaluated right after a *successful* completion.
 *   - First-3-success grace: let a new user get value before we ask.
 *   - At most ONE prompt per app session.
 *   - Escalating cooldowns so we fade out, never nag: 7d → 14d → 30d → 75d.
 *   - `optedOut` is terminal — "Don't ask again" means never again.
 *   - Milestones (1st clone / 10th dub / 30-day sustained use) each fire at
 *     most once *ever*, and still obey the session cap, cooldowns and opt-out.
 *
 * All time logic flows through an injectable `now` so the truth table is
 * deterministic in tests.
 */
import type { StateCreator } from 'zustand';

/** Escalating cooldown ladder, in days. After the Nth shown prompt we wait
 *  COOLDOWNS[min(N-1, last)] days before the next one is eligible. */
export const COOLDOWN_DAYS = [7, 14, 30, 75] as const;
const DAY_MS = 24 * 60 * 60 * 1000;

/** Number of early successes to let pass before the first prompt is eligible. */
export const GRACE_SUCCESSES = 3;

/** Milestone identifiers — each fires at most once ever. */
type MilestoneId = 'first-clone' | 'tenth-dub' | 'sustained-30d';

export interface DonationState {
  /** Total successful "value" events seen (clone saved, dub done, longform done…). */
  successCount: number;
  /** Cumulative count of dubs completed (for the 10th-dub milestone). */
  dubCount: number;
  /** Epoch ms of the very first successful event (for the 30-day sustained milestone). */
  firstSuccessAt: number | null;
  /** Epoch ms the last postcard was shown (drives cooldowns). null = never. */
  lastShownAt: number | null;
  /** How many postcards we've shown in total (indexes the cooldown ladder). */
  shownCount: number;
  /** Milestones already fired (terminal per id). */
  firedMilestones: MilestoneId[];
  /** User clicked "Don't ask again" — terminal, no prompt ever again. */
  optedOut: boolean;
  /** Whether a postcard has already been shown *this session* (NOT persisted). */
  shownThisSession: boolean;
}

interface DonationDecision {
  show: boolean;
  /** Why we decided to show (milestone id) or not (reason string). */
  reason: string;
  /** Milestone that triggered the show, if any. */
  milestone: MilestoneId | null;
}

export interface DonationSlice extends DonationState {
  /**
   * Record a successful "value" event and decide whether to show the postcard.
   * Pure-ish: mutates counters, returns the decision. The caller (the shared
   * `evaluateDonationPrompt`) is responsible for actually rendering the toast
   * and then calling `markDonationShown()` if `show` is true.
   *
   * @param kind  which success happened (affects milestones)
   * @param now   injectable clock (defaults to Date.now)
   */
  recordDonationSuccess: (kind: DonationSuccessKind, now?: number) => DonationDecision;
  /** Commit that a postcard was shown now (sets cooldown anchor + session flag). */
  markDonationShown: (milestone?: MilestoneId | null, now?: number) => void;
  /** "Maybe later" — soft dismiss. Counts as shown (already handled), no extra state. */
  /** "Don't ask again" — terminal opt-out. */
  optOutOfDonation: () => void;
  /** Test/util: reset the in-memory session flag (e.g. on a fresh launch). */
  resetDonationSession: () => void;
}

type DonationSuccessKind = 'clone' | 'dub' | 'longform' | 'generic';

export const INITIAL_DONATION: DonationState = {
  successCount: 0,
  dubCount: 0,
  firstSuccessAt: null,
  lastShownAt: null,
  shownCount: 0,
  firedMilestones: [],
  optedOut: false,
  shownThisSession: false,
};

/** Days elapsed between two epoch-ms timestamps. */
function daysBetween(a: number, b: number): number {
  return (a - b) / DAY_MS;
}

/**
 * Pure cooldown gate: given current state + now, is a prompt eligible?
 * Returns a reason string when blocked, or null when eligible.
 *
 * Exported so tests can drive the truth table without the store.
 */
export function donationBlockReason(s: DonationState, now: number): string | null {
  if (s.optedOut) return 'opted-out';
  if (s.shownThisSession) return 'session-cap';
  // Grace: the first GRACE_SUCCESSES successes never prompt. successCount is
  // incremented *before* this gate, so `<=` makes the prompt first eligible on
  // success #(GRACE_SUCCESSES + 1).
  if (s.successCount <= GRACE_SUCCESSES) return 'grace';
  if (s.lastShownAt != null) {
    const idx = Math.min(s.shownCount - 1, COOLDOWN_DAYS.length - 1);
    const wait = COOLDOWN_DAYS[Math.max(0, idx)];
    if (daysBetween(now, s.lastShownAt) < wait) return 'cooldown';
  }
  return null;
}

/**
 * Decide which (if any) milestone fires for this success, given the *updated*
 * counters. A milestone only fires once ever (not already in firedMilestones).
 */
function pickMilestone(
  s: DonationState,
  kind: DonationSuccessKind,
  now: number,
): MilestoneId | null {
  const fired = new Set(s.firedMilestones);
  if (kind === 'clone' && !fired.has('first-clone')) return 'first-clone';
  if (kind === 'dub' && s.dubCount >= 10 && !fired.has('tenth-dub')) return 'tenth-dub';
  if (
    !fired.has('sustained-30d') &&
    s.firstSuccessAt != null &&
    daysBetween(now, s.firstSuccessAt) >= 30
  ) {
    return 'sustained-30d';
  }
  return null;
}

export const createDonationSlice: StateCreator<DonationSlice, [], [], DonationSlice> = (
  set,
  get,
) => ({
  ...INITIAL_DONATION,

  recordDonationSuccess: (kind, now = Date.now()) => {
    // 1. Update counters FIRST (so milestones see the new totals).
    let snapshot: DonationState = get();
    const next: DonationState = {
      ...snapshot,
      successCount: snapshot.successCount + 1,
      dubCount: snapshot.dubCount + (kind === 'dub' ? 1 : 0),
      firstSuccessAt: snapshot.firstSuccessAt ?? now,
    };
    set(next);
    snapshot = next;

    // 2. Gate on opt-out / session-cap / grace / cooldown.
    const blocked = donationBlockReason(snapshot, now);
    if (blocked) return { show: false, reason: blocked, milestone: null };

    // 3. A prompt is eligible. Prefer a milestone trigger if one fits, else a
    //    plain "you just succeeded" prompt.
    const milestone = pickMilestone(snapshot, kind, now);
    return { show: true, reason: milestone ? `milestone:${milestone}` : 'success', milestone };
  },

  markDonationShown: (milestone = null, now = Date.now()) =>
    set((s) => ({
      lastShownAt: now,
      shownCount: s.shownCount + 1,
      shownThisSession: true,
      firedMilestones:
        milestone && !s.firedMilestones.includes(milestone)
          ? [...s.firedMilestones, milestone]
          : s.firedMilestones,
    })),

  optOutOfDonation: () => set({ optedOut: true }),

  resetDonationSession: () => set({ shownThisSession: false }),
});
