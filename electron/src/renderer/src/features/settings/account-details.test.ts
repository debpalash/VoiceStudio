import { expect, it } from 'vitest';
import type { AccountPublicState } from '../../../../preload/index.d';
import { DISPLAY_NAME_MAX, cleanDisplayName, initials, isProPlan } from './account-details';

it('cleans a display name: trims, collapses spaces, refuses empty or too long', () => {
  expect(cleanDisplayName('  Ana   Lima ')).toBe('Ana Lima');
  expect(cleanDisplayName('   ')).toBeNull();
  expect(cleanDisplayName('x'.repeat(DISPLAY_NAME_MAX))).toHaveLength(DISPLAY_NAME_MAX);
  expect(cleanDisplayName('x'.repeat(DISPLAY_NAME_MAX + 1))).toBeNull();
});

it('initials use two words, or the first letter of an email', () => {
  expect(initials('Ana Lima Souza')).toBe('AL');
  expect(initials('ana@example.com')).toBe('A');
});

it('plan badge falls back to the plan code when entitlements are unreadable', () => {
  const plan = (planCode: string, proDesktop: boolean | null) =>
    ({ planCode, proDesktop }) as unknown as AccountPublicState['plan'];
  expect(isProPlan(plan('pro', null))).toBe(true);
  expect(isProPlan(plan('free', null))).toBe(false);
  expect(isProPlan(plan('pro', false))).toBe(false);
  expect(isProPlan(plan('free', true))).toBe(true);
  expect(isProPlan(null)).toBe(false);
});
