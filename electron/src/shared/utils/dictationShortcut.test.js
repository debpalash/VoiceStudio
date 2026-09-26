import { describe, expect, it } from 'vitest';
import {
  eventMatchesShortcut,
  formatShortcut,
  isShortcutRelease,
  parseShortcut,
} from './dictationShortcut';

describe('dictation shortcut parsing', () => {
  it('resolves CmdOrCtrl to the current platform', () => {
    expect(parseShortcut('CmdOrCtrl+Shift+Space', 'mac')).toMatchObject({
      code: 'Space',
      meta: true,
      ctrl: false,
      shift: true,
    });
    expect(parseShortcut('CmdOrCtrl+Shift+Space', 'windows')).toMatchObject({
      code: 'Space',
      meta: false,
      ctrl: true,
      shift: true,
    });
  });

  it('matches the configured chord exactly', () => {
    expect(
      eventMatchesShortcut(
        { code: 'KeyK', ctrlKey: true, altKey: true, shiftKey: false, metaKey: false },
        'Ctrl+Alt+K',
        'linux',
      ),
    ).toBe(true);
    expect(
      eventMatchesShortcut(
        { code: 'KeyK', ctrlKey: true, altKey: true, shiftKey: true, metaKey: false },
        'Ctrl+Alt+K',
        'linux',
      ),
    ).toBe(false);
  });

  it('disarms hold-to-talk when a chord modifier is released first', () => {
    const shortcut = parseShortcut('Ctrl+Shift+Space', 'linux');
    expect(isShortcutRelease({ code: 'ControlLeft' }, shortcut)).toBe(true);
    expect(isShortcutRelease({ code: 'KeyA' }, shortcut)).toBe(false);
  });

  it('formats platform-native hints', () => {
    expect(formatShortcut('CmdOrCtrl+Shift+Space', 'mac')).toBe('⌘⇧Space');
    expect(formatShortcut('CmdOrCtrl+Shift+Space', 'windows')).toBe('Ctrl+Shift+Space');
    expect(formatShortcut('Cmd+Option+K', 'linux')).toBe('Super+Alt+K');
  });
});
