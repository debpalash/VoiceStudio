import { expect, it } from 'vitest';
import { dubPreservesBackground } from './dub-flow-state';
import type { DubSession } from './dub-session';
it('cannot request missing background even when an old export preference asks for it', () => {
  expect(
    dubPreservesBackground({
      separateBackground: false,
      exportOptions: { preserveBg: true },
    } as DubSession),
  ).toBe(false);
  expect(
    dubPreservesBackground({
      subtitleOnly: true,
      exportOptions: { preserveBg: true },
    } as DubSession),
  ).toBe(false);
});
it('retains legacy separated projects and respects an explicit voice-only mix', () => {
  expect(dubPreservesBackground({} as DubSession)).toBe(true);
  expect(
    dubPreservesBackground({
      separateBackground: true,
      exportOptions: { preserveBg: false },
    } as DubSession),
  ).toBe(false);
});
