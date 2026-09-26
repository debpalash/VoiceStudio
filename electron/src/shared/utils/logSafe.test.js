import { describe, expect, it } from 'vitest';
import { logSafe } from './logSafe';

describe('logSafe', () => {
  it.each([
    'x\nFORGED',
    'x\rFORGED',
    'x\x1b[31mFORGED',
    'x\u2028FORGED',
    'x\u2029FORGED',
    'x\u202eFORGED',
  ])('escapes controls in %j', (value) => {
    const safe = logSafe(value);
    expect(
      [...safe].every((char) => {
        const code = char.codePointAt(0);
        return code > 0x1f && !(code >= 0x7f && code <= 0x9f);
      }),
    ).toBe(true);
    expect(safe).toContain('FORGED');
    expect(safe).not.toContain(value.slice(1, 2));
  });

  it('bounds oversized values', () => {
    expect(logSafe('x'.repeat(10_000)).length).toBeLessThanOrEqual(512);
  });
});
