import { expect, it } from 'vitest';
import { DISPLAY_NAME_MAX, cleanDisplayName, initials } from './account-details';

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
