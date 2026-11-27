import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CompactOverlaySettings } from './compact-overlay-settings';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('voicestudio', { capture: {} });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('shows a retryable error when the choice cannot be saved, then clears it', () => {
  const failing = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota');
  });
  render(<CompactOverlaySettings />);
  const toggle = screen.getByRole('switch');
  fireEvent.click(toggle);
  expect(screen.getByRole('alert').textContent).toBe('dictation.compact_save_failed');
  expect(toggle.getAttribute('aria-checked')).toBe('false');
  failing.mockRestore();
  fireEvent.click(toggle);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(toggle.getAttribute('aria-checked')).toBe('true');
});
