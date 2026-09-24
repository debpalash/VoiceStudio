import { expect, it } from 'vitest';
import { isE164, normalizePhone, phoneCountry, phoneProblem, phoneRegion } from './phone';

it('accepts formatted international numbers and normalizes them to E.164', () => {
  expect(normalizePhone('+1 (555) 010-0199')).toBe('+15550100199');
  expect(normalizePhone('0044 20 7946 0958')).toBe('+442079460958');
  expect(isE164('+1 (555) 010-0199')).toBe(true);
  expect(isE164('+44 20 7946 0958')).toBe(true);
  expect(isE164('+91 98765 43210')).toBe(true);
});

it('explains what is wrong with a number', () => {
  expect(phoneProblem('')).toBe('empty');
  expect(phoneProblem('   ')).toBe('empty');
  expect(phoneProblem('5550100199')).toBe('missing_plus');
  expect(phoneProblem('+0 555 010 0199')).toBe('invalid');
  expect(phoneProblem('+1234')).toBe('invalid');
  expect(phoneProblem('+1234567890123456')).toBe('invalid');
  expect(phoneProblem('+1 555 CALL NOW')).toBe('invalid');
});

it('hints the country from the calling code, preferring the longest code', () => {
  expect(phoneRegion('+15550100199')).toBeNull();
  expect(phoneRegion('+14165550199')).toBeNull();
  expect(phoneRegion('+77012345678')).toBeNull();
  expect(phoneRegion('+442079460958')).toBe('GB');
  expect(phoneRegion('+353 1 234 5678')).toBe('IE');
  expect(phoneRegion('+999 1234 5678')).toBeNull();
  expect(phoneRegion('5550100199')).toBeNull();
  expect(phoneCountry('+49 30 123456', 'en')).toBe('Germany');
});
