/**
 * Donation goal data — "Support VoiceStudio development" goal bar (spec 007, Option B).
 *
 * Source-of-truth strategy (Option B):
 *   1. A bundled, committed snapshot ships with the app so the goal bar ALWAYS
 *      renders something sane offline (local-first guarantee — no network needed).
 *   2. At runtime we best-effort `fetch()` a fresher copy of the same file from
 *      `public/donation_progress.json`. If that fails (offline, file moved,
 *      malformed JSON) we silently fall back to the bundled snapshot.
 *
 * Nothing here ever throws — a donation widget must never break the app.
 */

export interface DonationProgress {
  /** Amount raised so far, in `currency`. */
  raised: number;
  /** Monthly goal (Claude Max ≈ $200/mo). */
  goal: number;
  /** ISO-4217 currency code, e.g. "USD". */
  currency: string;
  /** Number of distinct supporters (for "Join {n} supporters" social proof). */
  sponsorCount: number;
  /** YYYY-MM-DD the snapshot was last refreshed. */
  updated: string;
}

/**
 * Bundled offline fallback. Kept in lockstep with
 * `frontend/public/donation_progress.json` (the fetched copy). If you bump one,
 * bump both — a test asserts the bundled fallback is well-formed.
 */
export const BUNDLED_PROGRESS: DonationProgress = {
  raised: 10,
  goal: 200,
  currency: 'USD',
  sponsorCount: 1,
  updated: '2026-06-17',
};

/** Where the runtime-refreshable copy lives (served from `public/`). */
const PROGRESS_URL = '/donation_progress.json';

/** Coerce an unknown parsed value into a safe DonationProgress, or null. */
export function normalizeProgress(raw: unknown): DonationProgress | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const raised = Number(r.raised);
  const goal = Number(r.goal);
  if (!Number.isFinite(raised) || !Number.isFinite(goal) || goal <= 0) return null;
  const sponsorCount = Number(r.sponsorCount);
  return {
    raised: Math.max(0, raised),
    goal,
    currency: typeof r.currency === 'string' && r.currency ? r.currency : 'USD',
    sponsorCount: Number.isFinite(sponsorCount) ? Math.max(0, Math.round(sponsorCount)) : 0,
    updated: typeof r.updated === 'string' ? r.updated : '',
  };
}

/** 0..1 fill fraction (clamped — a goal-met snapshot renders a full, capped bar). */
export function progressPct(p: Pick<DonationProgress, 'raised' | 'goal'>): number {
  if (!(p.goal > 0)) return 0;
  return Math.max(0, Math.min(1, p.raised / p.goal));
}

/** True once raised ≥ goal (drives the "goal met" celebratory copy/state). */
export function isGoalMet(p: Pick<DonationProgress, 'raised' | 'goal'>): boolean {
  return p.goal > 0 && p.raised >= p.goal;
}

/**
 * Best-effort fetch of a fresher snapshot. Resolves to the bundled fallback on
 * ANY failure (network, non-2xx, bad JSON). Injectable `fetcher` for tests.
 */
export async function loadDonationProgress(
  fetcher: typeof fetch = fetch,
): Promise<DonationProgress> {
  try {
    const res = await fetcher(PROGRESS_URL, { cache: 'no-cache' });
    if (!res.ok) return BUNDLED_PROGRESS;
    const json = await res.json();
    return normalizeProgress(json) ?? BUNDLED_PROGRESS;
  } catch {
    return BUNDLED_PROGRESS;
  }
}

export function formatMoney(amount: number, currency: string) {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: amount % 1 === 0 ? 0 : 2,
    }).format(amount);
  } catch {
    return `$${Math.round(amount)}`;
  }
}
