// donationMoments — exhaustive gate coverage for the footer donation-prompt
// eligibility engine. New module, so no fail-before regression pair; instead
// every gate, boundary day, the opt-out paths (own + legacy postcard), the
// session cap, and the localStorage round-trip are pinned deterministically
// via the injected clock + RNG.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  recordValueMoment,
  optOutOfDonationMoments,
  isDonationOptedOut,
  _resetDonationSessionForTests,
  MIN_LIFETIME_MOMENTS,
  PROMPT_COOLDOWN_DAYS,
  FIRST_PROMPT_MIN_DAYS,
  PROMPT_PROBABILITY,
  DONATE_LINE_COUNT,
  DONATION_MOMENT_EVENT,
  LS_MOMENT_COUNT,
  LS_FIRST_MOMENT_AT,
  LS_LAST_PROMPT_AT,
  LS_PROMPT_COUNT,
  LS_OPT_OUT,
} from './donationMoments';
import { flushPendingWrites, queueJsonWrite } from './coalescedJsonStorage';

const DAY = 24 * 60 * 60 * 1000;
const T0 = Date.UTC(2026, 0, 1); // arbitrary fixed epoch
const win = () => 0; // always wins the 25% roll
const lose = () => 0.99; // always loses the roll

/** Record `n` silent moments at `now` (roll always lost → never prompts). */
function burnMoments(n, now = T0) {
  for (let i = 0; i < n; i++) recordValueMoment('export', { now, random: lose });
}

/** Storage state where the NEXT winning moment is fully eligible at `now`. */
function seedEligible(now) {
  localStorage.setItem(LS_MOMENT_COUNT, String(MIN_LIFETIME_MOMENTS));
  localStorage.setItem(LS_FIRST_MOMENT_AT, String(now - (FIRST_PROMPT_MIN_DAYS + 1) * DAY));
}

beforeEach(() => {
  localStorage.clear();
  _resetDonationSessionForTests();
});

describe('gate (a) — lifetime value-moment minimum', () => {
  it('never prompts brand-new users, even on a winning roll', () => {
    const res = recordValueMoment('export', { now: T0, random: win });
    expect(res).toMatchObject({ show: false, reason: 'too-few-moments' });
  });

  it(`stays silent until ${MIN_LIFETIME_MOMENTS} lifetime moments are recorded`, () => {
    for (let i = 1; i < MIN_LIFETIME_MOMENTS; i++) {
      // Spread over many days so no OTHER gate can be the blocker.
      const res = recordValueMoment('export', { now: T0 + i * 5 * DAY, random: win });
      expect(res).toMatchObject({ show: false, reason: 'too-few-moments' });
    }
    // Moment #MIN is the first that can prompt (first-moment delay long past).
    const res = recordValueMoment('export', {
      now: T0 + MIN_LIFETIME_MOMENTS * 5 * DAY,
      random: win,
    });
    expect(res.show).toBe(true);
  });
});

describe('gate (b) — first-prompt delay and prompt cooldown', () => {
  it(`first prompt waits ≥${FIRST_PROMPT_MIN_DAYS} days after the first value-moment`, () => {
    burnMoments(MIN_LIFETIME_MOMENTS, T0); // all in one sitting
    const early = recordValueMoment('export', {
      now: T0 + FIRST_PROMPT_MIN_DAYS * DAY - 1,
      random: win,
    });
    expect(early).toMatchObject({ show: false, reason: 'first-prompt-delay' });
    // Boundary: exactly N days qualifies.
    const onTime = recordValueMoment('export', {
      now: T0 + FIRST_PROMPT_MIN_DAYS * DAY,
      random: win,
    });
    expect(onTime.show).toBe(true);
  });

  it(`re-prompts only ≥${PROMPT_COOLDOWN_DAYS} days after the last prompt`, () => {
    seedEligible(T0);
    expect(recordValueMoment('export', { now: T0, random: win }).show).toBe(true);
    _resetDonationSessionForTests(); // fresh launch → only the cooldown gates

    const tooSoon = recordValueMoment('export', {
      now: T0 + PROMPT_COOLDOWN_DAYS * DAY - 1,
      random: win,
    });
    expect(tooSoon).toMatchObject({ show: false, reason: 'cooldown' });

    const onTime = recordValueMoment('export', {
      now: T0 + PROMPT_COOLDOWN_DAYS * DAY,
      random: win,
    });
    expect(onTime.show).toBe(true);
  });
});

describe('gate (c) — permanent opt-out', () => {
  it('"Don\'t ask again" is terminal, across sessions', () => {
    optOutOfDonationMoments();
    expect(localStorage.getItem(LS_OPT_OUT)).toBe('1');
    expect(isDonationOptedOut()).toBe(true);

    seedEligible(T0);
    _resetDonationSessionForTests(); // even a brand-new session
    const res = recordValueMoment('export', { now: T0, random: win });
    expect(res).toMatchObject({ show: false, reason: 'opted-out' });
  });

  it('honors the legacy postcard opt-out persisted in the omnivoice.app blob', () => {
    localStorage.setItem('omnivoice.app', JSON.stringify({ state: { optedOut: true } }));
    expect(isDonationOptedOut()).toBe(true);
    seedEligible(T0);
    const res = recordValueMoment('export', { now: T0, random: win });
    expect(res).toMatchObject({ show: false, reason: 'opted-out' });
  });

  it('honors the unchanged v7 legacy envelope after a deferred flush', () => {
    queueJsonWrite('omnivoice.app', () => ({ state: { optedOut: true }, version: 7 }));
    flushPendingWrites();

    expect(isDonationOptedOut()).toBe(true);
  });

  it('a corrupt legacy blob is ignored (not treated as opted out)', () => {
    localStorage.setItem('omnivoice.app', '{definitely not json');
    expect(isDonationOptedOut()).toBe(false);
    seedEligible(T0);
    expect(recordValueMoment('export', { now: T0, random: win }).show).toBe(true);
  });
});

describe('gate (d) — once per app session', () => {
  it('never prompts twice in one session, even past the cooldown', () => {
    seedEligible(T0);
    expect(recordValueMoment('export', { now: T0, random: win }).show).toBe(true);
    // Same session, clock pushed WAY past the cooldown: still capped.
    const res = recordValueMoment('export', {
      now: T0 + 10 * PROMPT_COOLDOWN_DAYS * DAY,
      random: win,
    });
    expect(res).toMatchObject({ show: false, reason: 'session-cap' });
    // A fresh session (plus elapsed cooldown) is eligible again.
    _resetDonationSessionForTests();
    expect(
      recordValueMoment('export', { now: T0 + 10 * PROMPT_COOLDOWN_DAYS * DAY, random: win }).show,
    ).toBe(true);
  });
});

describe('gate (e) — the random roll', () => {
  it(`shows only when the roll lands strictly under ${PROMPT_PROBABILITY}`, () => {
    seedEligible(T0);
    const lost = recordValueMoment('export', { now: T0, random: () => PROMPT_PROBABILITY });
    expect(lost).toMatchObject({ show: false, reason: 'lost-roll' });
    const won = recordValueMoment('export', {
      now: T0,
      random: () => PROMPT_PROBABILITY - 0.001,
    });
    expect(won.show).toBe(true);
  });

  it('a lost roll does not burn the session cap or set a cooldown', () => {
    seedEligible(T0);
    recordValueMoment('export', { now: T0, random: lose });
    expect(localStorage.getItem(LS_LAST_PROMPT_AT)).toBeNull();
    expect(recordValueMoment('export', { now: T0, random: win }).show).toBe(true);
  });
});

describe('state persistence (localStorage round-trip)', () => {
  it('moment counters survive a "restart" (module session state cleared)', () => {
    burnMoments(MIN_LIFETIME_MOMENTS - 1, T0);
    expect(localStorage.getItem(LS_MOMENT_COUNT)).toBe(String(MIN_LIFETIME_MOMENTS - 1));
    expect(localStorage.getItem(LS_FIRST_MOMENT_AT)).toBe(String(T0));
    _resetDonationSessionForTests(); // simulate a fresh launch, same storage
    // One more moment, days later → all gates pass off the PERSISTED history.
    const res = recordValueMoment('export', {
      now: T0 + (FIRST_PROMPT_MIN_DAYS + 1) * DAY,
      random: win,
    });
    expect(res.show).toBe(true);
    expect(localStorage.getItem(LS_MOMENT_COUNT)).toBe(String(MIN_LIFETIME_MOMENTS));
  });

  it('a shown prompt commits the cooldown anchor and prompt counter', () => {
    seedEligible(T0);
    recordValueMoment('export', { now: T0, random: win });
    expect(localStorage.getItem(LS_LAST_PROMPT_AT)).toBe(String(T0));
    expect(localStorage.getItem(LS_PROMPT_COUNT)).toBe('1');
  });

  it('corrupt counters degrade to zero instead of throwing', () => {
    localStorage.setItem(LS_MOMENT_COUNT, 'garbage');
    localStorage.setItem(LS_FIRST_MOMENT_AT, 'NaN');
    const res = recordValueMoment('export', { now: T0, random: win });
    expect(res).toMatchObject({ show: false, reason: 'too-few-moments' });
    expect(localStorage.getItem(LS_MOMENT_COUNT)).toBe('1'); // repaired
    expect(localStorage.getItem(LS_FIRST_MOMENT_AT)).toBe(String(T0));
  });
});

describe('prompt event + line rotation', () => {
  it(`dispatches ${DONATION_MOMENT_EVENT} with kind + rotating line on show only`, () => {
    const seen = [];
    const onMoment = (e) => seen.push(e.detail);
    window.addEventListener(DONATION_MOMENT_EVENT, onMoment);
    try {
      recordValueMoment('export', { now: T0, random: win }); // blocked (grace)
      expect(seen).toHaveLength(0);

      let now = T0;
      for (let i = 0; i < DONATE_LINE_COUNT + 1; i++) {
        seedEligible(now);
        _resetDonationSessionForTests();
        const res = recordValueMoment('dub', { now, random: win });
        expect(res.show).toBe(true);
        expect(res.line).toBe(i % DONATE_LINE_COUNT); // 0,1,2,3,0…
        now += (PROMPT_COOLDOWN_DAYS + 1) * DAY;
      }
      expect(seen).toHaveLength(DONATE_LINE_COUNT + 1);
      expect(seen[0]).toEqual({ kind: 'dub', line: 0 });
      expect(seen[DONATE_LINE_COUNT].line).toBe(0); // wrapped around
    } finally {
      window.removeEventListener(DONATION_MOMENT_EVENT, onMoment);
    }
  });

  it('returns a bare {show:false} decision without dispatching when capped', () => {
    const spy = vi.fn();
    window.addEventListener(DONATION_MOMENT_EVENT, spy);
    try {
      seedEligible(T0);
      recordValueMoment('export', { now: T0, random: win });
      recordValueMoment('export', { now: T0, random: win }); // session-capped
      expect(spy).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(DONATION_MOMENT_EVENT, spy);
    }
  });
});
