import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

import GoalBar from '../components/donate/GoalBar';
import {
  loadDonationProgress,
  normalizeProgress,
  progressPct,
  isGoalMet,
  BUNDLED_PROGRESS,
} from '../api/donation';

function wrap(ui) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe('donation data module', () => {
  it('progressPct clamps to 0..1', () => {
    expect(progressPct({ raised: 0, goal: 200 })).toBe(0);
    expect(progressPct({ raised: 100, goal: 200 })).toBe(0.5);
    expect(progressPct({ raised: 500, goal: 200 })).toBe(1); // capped
    expect(progressPct({ raised: 5, goal: 0 })).toBe(0); // guard /0
  });

  it('isGoalMet flips at raised >= goal', () => {
    expect(isGoalMet({ raised: 199, goal: 200 })).toBe(false);
    expect(isGoalMet({ raised: 200, goal: 200 })).toBe(true);
    expect(isGoalMet({ raised: 250, goal: 200 })).toBe(true);
  });

  it('normalizeProgress rejects malformed input', () => {
    expect(normalizeProgress(null)).toBeNull();
    expect(normalizeProgress('nope')).toBeNull();
    expect(normalizeProgress({ raised: 'x', goal: 200 })).toBeNull();
    expect(normalizeProgress({ raised: 10, goal: 0 })).toBeNull();
    expect(normalizeProgress({ raised: 10, goal: 200 })).toMatchObject({
      raised: 10,
      goal: 200,
      currency: 'USD',
    });
  });

  it('bundled fallback is well-formed (keeps page lockstep w/ public JSON)', () => {
    expect(normalizeProgress(BUNDLED_PROGRESS)).not.toBeNull();
    expect(BUNDLED_PROGRESS.goal).toBe(200);
    expect(BUNDLED_PROGRESS.raised).toBeGreaterThan(0); // endowed baseline
    expect(BUNDLED_PROGRESS.raised).toBeLessThan(BUNDLED_PROGRESS.goal);
  });

  it('loadDonationProgress: returns fetched copy on success', async () => {
    const fresh = {
      raised: 88,
      goal: 200,
      currency: 'USD',
      sponsorCount: 12,
      updated: '2026-06-20',
    };
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => fresh });
    await expect(loadDonationProgress(fetcher)).resolves.toMatchObject({
      raised: 88,
      sponsorCount: 12,
    });
  });

  it('loadDonationProgress: falls back to bundled when offline (fetch rejects)', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('offline'));
    await expect(loadDonationProgress(fetcher)).resolves.toEqual(BUNDLED_PROGRESS);
  });

  it('loadDonationProgress: falls back to bundled on non-2xx', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) });
    await expect(loadDonationProgress(fetcher)).resolves.toEqual(BUNDLED_PROGRESS);
  });

  it('loadDonationProgress: falls back to bundled on malformed JSON', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ junk: true }) });
    await expect(loadDonationProgress(fetcher)).resolves.toEqual(BUNDLED_PROGRESS);
  });
});

describe('<GoalBar />', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders from injected progress (in-progress state)', () => {
    const p = { raised: 100, goal: 200, currency: 'USD', sponsorCount: 9, updated: '2026-06-16' };
    const { container } = wrap(<GoalBar progress={p} />);
    expect(screen.getByText('50%')).toBeInTheDocument();
    const track = container.querySelector('.goal__track');
    expect(track).toHaveAttribute('aria-valuenow', '50');
    // --goal-pct drives the fill
    expect(container.querySelector('.goal')).toHaveStyle({ '--goal-pct': '0.5' });
    // not goal-met
    expect(container.querySelector('.goal--met')).toBeNull();
  });

  it('renders the goal-met state when raised >= goal', () => {
    const p = { raised: 200, goal: 200, currency: 'USD', sponsorCount: 30, updated: '2026-06-16' };
    const { container } = wrap(<GoalBar progress={p} />);
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(container.querySelector('.goal--met')).not.toBeNull();
  });

  it('falls back to the bundled snapshot when the runtime fetch fails (offline)', async () => {
    // No injected progress → component calls loadDonationProgress() → fetch.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const { container } = wrap(<GoalBar />);
    const expectedPct = Math.round((BUNDLED_PROGRESS.raised / BUNDLED_PROGRESS.goal) * 100);
    await waitFor(() => {
      expect(screen.getByText(`${expectedPct}%`)).toBeInTheDocument();
    });
    expect(container.querySelector('.goal__track')).toHaveAttribute(
      'aria-valuenow',
      String(expectedPct),
    );
  });

  it('mini variant omits the Pip + percent header', () => {
    const p = { raised: 50, goal: 200, currency: 'USD', sponsorCount: 4, updated: '2026-06-16' };
    const { container } = wrap(<GoalBar mini progress={p} />);
    expect(container.querySelector('.goal--mini')).not.toBeNull();
    expect(container.querySelector('.goal__pip')).toBeNull();
    expect(container.querySelector('.goal__head')).toBeNull();
  });
});
