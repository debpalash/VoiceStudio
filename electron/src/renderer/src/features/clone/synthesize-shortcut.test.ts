import { describe, expect, it } from 'vitest';
import { isSynthesizeShortcut } from './synthesize-shortcut';

const key = (init: KeyboardEventInit & { keyCode?: number }) => {
  const event = new KeyboardEvent('keydown', init);
  if (init.keyCode !== undefined) Object.defineProperty(event, 'keyCode', { value: init.keyCode });
  return event;
};

describe('isSynthesizeShortcut', () => {
  it('accepts Ctrl+Enter and Cmd+Enter', () => {
    expect(isSynthesizeShortcut(key({ key: 'Enter', ctrlKey: true }))).toBe(true);
    expect(isSynthesizeShortcut(key({ key: 'Enter', metaKey: true }))).toBe(true);
  });

  it('ignores plain Enter, other keys and held repeats', () => {
    expect(isSynthesizeShortcut(key({ key: 'Enter' }))).toBe(false);
    expect(isSynthesizeShortcut(key({ key: 'a', ctrlKey: true }))).toBe(false);
    expect(isSynthesizeShortcut(key({ key: 'Enter', ctrlKey: true, repeat: true }))).toBe(false);
  });

  it('ignores Enter that confirms an IME composition', () => {
    expect(isSynthesizeShortcut(key({ key: 'Enter', ctrlKey: true, isComposing: true }))).toBe(
      false,
    );
    // Safari and some IMEs report the composing keystroke only as keyCode 229.
    expect(isSynthesizeShortcut(key({ key: 'Enter', metaKey: true, keyCode: 229 }))).toBe(false);
  });
});
