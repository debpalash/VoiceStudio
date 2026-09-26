import { describe, it, expect } from 'vitest';
import {
  createDonationSlice,
  donationBlockReason,
  INITIAL_DONATION,
  COOLDOWN_DAYS,
  GRACE_SUCCESSES,
  type DonationState,
  type DonationSlice,
} from './donationSlice';

const DAY = 24 * 60 * 60 * 1000;

/** Minimal store harness mirroring releasesSlice.test.ts. */
function harness() {
  let state: DonationSlice;
  const set = (p: any) => {
    state = { ...state, ...(typeof p === 'function' ? p(state) : p) };
  };
  state = createDonationSlice(set as any, (() => state) as any, {} as any);
  return { get: () => state };
}

/** Build a DonationState with overrides, defaults from INITIAL_DONATION. */
function st(over: Partial<DonationState> = {}): DonationState {
  return { ...INITIAL_DONATION, ...over };
}

describe('donationBlockReason — pure cooldown gate (truth table)', () => {
  const T0 = 1_700_000_000_000;

  it('grace: blocks the first GRACE_SUCCESSES successes', () => {
    // successCount reflects the count AFTER the just-recorded success, so the
    // first GRACE_SUCCESSES recorded successes (counts 1..GRACE_SUCCESSES) are
    // grace-blocked; the next one (count GRACE_SUCCESSES+1) is eligible.
    for (let n = 1; n <= GRACE_SUCCESSES; n++) {
      expect(donationBlockReason(st({ successCount: n }), T0)).toBe('grace');
    }
    expect(donationBlockReason(st({ successCount: GRACE_SUCCESSES + 1 }), T0)).toBeNull();
  });

  it('opted-out is terminal — beats everything', () => {
    expect(donationBlockReason(st({ optedOut: true, successCount: 99 }), T0)).toBe('opted-out');
    // even mid-cooldown / mid-session
    expect(donationBlockReason(st({ optedOut: true, shownThisSession: true }), T0)).toBe(
      'opted-out',
    );
  });

  it('session cap: blocks once a prompt was shown this session', () => {
    expect(donationBlockReason(st({ successCount: 5, shownThisSession: true }), T0)).toBe(
      'session-cap',
    );
  });

  it('cooldown ladder escalates 7 → 14 → 30 → 75 days', () => {
    // shownCount=1 → 7-day wait
    expect(
      donationBlockReason(st({ successCount: 5, shownCount: 1, lastShownAt: T0 }), T0 + 6 * DAY),
    ).toBe('cooldown');
    expect(
      donationBlockReason(st({ successCount: 5, shownCount: 1, lastShownAt: T0 }), T0 + 7 * DAY),
    ).toBeNull();
    // shownCount=2 → 14-day wait
    expect(
      donationBlockReason(st({ successCount: 5, shownCount: 2, lastShownAt: T0 }), T0 + 13 * DAY),
    ).toBe('cooldown');
    expect(
      donationBlockReason(st({ successCount: 5, shownCount: 2, lastShownAt: T0 }), T0 + 14 * DAY),
    ).toBeNull();
    // shownCount=3 → 30-day wait
    expect(
      donationBlockReason(st({ successCount: 5, shownCount: 3, lastShownAt: T0 }), T0 + 29 * DAY),
    ).toBe('cooldown');
    expect(
      donationBlockReason(st({ successCount: 5, shownCount: 3, lastShownAt: T0 }), T0 + 30 * DAY),
    ).toBeNull();
    // shownCount>=4 → clamps to the last (75-day) rung
    expect(
      donationBlockReason(st({ successCount: 5, shownCount: 9, lastShownAt: T0 }), T0 + 74 * DAY),
    ).toBe('cooldown');
    expect(
      donationBlockReason(st({ successCount: 5, shownCount: 9, lastShownAt: T0 }), T0 + 75 * DAY),
    ).toBeNull();
  });

  it('cooldown ladder constant is the documented sequence', () => {
    expect([...COOLDOWN_DAYS]).toEqual([7, 14, 30, 75]);
  });
});

describe('recordDonationSuccess — decisions via the slice', () => {
  const T0 = 1_700_000_000_000;

  it('does not show during the grace window, but still counts successes', () => {
    const { get } = harness();
    let d;
    for (let i = 0; i < GRACE_SUCCESSES; i++) d = get().recordDonationSuccess('generic', T0);
    expect(d!.show).toBe(false);
    expect(d!.reason).toBe('grace');
    expect(get().successCount).toBe(GRACE_SUCCESSES);
  });

  it('shows on the first eligible success after grace', () => {
    const { get } = harness();
    for (let i = 0; i < GRACE_SUCCESSES; i++) get().recordDonationSuccess('generic', T0);
    const d = get().recordDonationSuccess('generic', T0);
    expect(d.show).toBe(true);
    expect(d.reason).toBe('success');
  });

  it('first-clone milestone fires on a clone success past grace', () => {
    const { get } = harness();
    // burn grace with non-clone successes
    for (let i = 0; i < GRACE_SUCCESSES; i++) get().recordDonationSuccess('generic', T0);
    const d = get().recordDonationSuccess('clone', T0);
    expect(d.show).toBe(true);
    expect(d.milestone).toBe('first-clone');
  });

  it('session cap: only one show per session even with many successes', () => {
    const { get } = harness();
    for (let i = 0; i < GRACE_SUCCESSES; i++) get().recordDonationSuccess('generic', T0);
    const first = get().recordDonationSuccess('generic', T0);
    expect(first.show).toBe(true);
    // The shared evaluator marks shown; simulate that here.
    get().markDonationShown(first.milestone, T0);
    const second = get().recordDonationSuccess('generic', T0);
    expect(second.show).toBe(false);
    expect(second.reason).toBe('session-cap');
  });

  it('opt-out is terminal across sessions', () => {
    const { get } = harness();
    for (let i = 0; i < GRACE_SUCCESSES; i++) get().recordDonationSuccess('generic', T0);
    get().optOutOfDonation();
    get().resetDonationSession(); // new launch
    const d = get().recordDonationSuccess('generic', T0 + 200 * DAY);
    expect(d.show).toBe(false);
    expect(d.reason).toBe('opted-out');
  });

  it('after a show + cooldown, a new session prompts again', () => {
    const { get } = harness();
    for (let i = 0; i < GRACE_SUCCESSES; i++) get().recordDonationSuccess('generic', T0);
    const first = get().recordDonationSuccess('generic', T0);
    get().markDonationShown(first.milestone, T0);
    // New launch, but still inside the 7-day cooldown → blocked.
    get().resetDonationSession();
    expect(get().recordDonationSuccess('generic', T0 + 3 * DAY).reason).toBe('cooldown');
    // New launch past the 7-day cooldown → eligible again.
    get().resetDonationSession();
    expect(get().recordDonationSuccess('generic', T0 + 8 * DAY).show).toBe(true);
  });
});
