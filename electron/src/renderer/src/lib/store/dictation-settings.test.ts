import { beforeEach, expect, it, vi } from 'vitest';

const KEY = 'voicestudio.dictation-settings.v1';

beforeEach(() => {
  localStorage.clear();
  vi.resetModules();
});

it('keeps the full recorder as the default', async () => {
  const store = await import('./dictation-settings');
  expect(store.getCompactOverlay()).toBe(false);
});

it('persists the compact overlay without losing the echo-cancellation flag', async () => {
  const store = await import('./dictation-settings');
  store.setAecEnabled(true);
  store.setCompactOverlay(true);
  expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({
    aecEnabled: true,
    compactOverlay: true,
  });
  store.setAecEnabled(false);
  expect(store.getCompactOverlay()).toBe(true);
  store.setCompactOverlay(false);
  expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({ aecEnabled: false });
});

it('reads a stored value written by an older build', async () => {
  localStorage.setItem(KEY, JSON.stringify({ aecEnabled: true }));
  const store = await import('./dictation-settings');
  expect(store.getAecEnabled()).toBe(true);
  expect(store.getCompactOverlay()).toBe(false);
});

it('keeps the last confirmed value and allows a retry when storage fails', async () => {
  const store = await import('./dictation-settings');
  const failing = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota');
  });
  expect(store.setCompactOverlay(true)).toBe(false);
  expect(store.getCompactOverlay()).toBe(false);
  failing.mockRestore();
  expect(store.setCompactOverlay(true)).toBe(true);
  expect(store.getCompactOverlay()).toBe(true);
  expect(JSON.parse(localStorage.getItem(KEY)!)).toMatchObject({ compactOverlay: true });
});
