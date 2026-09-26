import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { expect, it } from 'vitest';
import { isImeComposing } from './ime';

it('flags keydowns that belong to an IME composition', () => {
  expect(isImeComposing(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true }))).toBe(
    true,
  );
  expect(isImeComposing(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 229 }))).toBe(true);
  expect(isImeComposing(new KeyboardEvent('keydown', { key: 'Enter' }))).toBe(false);
});

it('reads the native event behind a React keyboard event', () => {
  const nativeEvent = new KeyboardEvent('keydown', { key: 'Enter', isComposing: true });
  expect(isImeComposing({ nativeEvent } as unknown as ReactKeyboardEvent)).toBe(true);
});
