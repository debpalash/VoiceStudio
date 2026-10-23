import { expect, it } from 'vitest';
import { formatSegmentTime, parseSegmentTimeField } from './segment-time-field';

it('commits nothing for a field left showing the rounded current time', () => {
  expect(parseSegmentTimeField(formatSegmentTime(1.234), 1.234)).toBeNull();
  expect(parseSegmentTimeField('2.00', 2)).toBeNull();
});

it('commits nothing for a blank or non-numeric field', () => {
  expect(parseSegmentTimeField('', 1.234)).toBeNull();
  expect(parseSegmentTimeField('  ', 1.234)).toBeNull();
  expect(parseSegmentTimeField('abc', 1.234)).toBeNull();
});

it('commits a typed time, including zero', () => {
  expect(parseSegmentTimeField('1.5', 1.234)).toBe(1.5);
  expect(parseSegmentTimeField(' 0 ', 1.234)).toBe(0);
  expect(parseSegmentTimeField('1.236', 1.234)).toBe(1.236);
});
