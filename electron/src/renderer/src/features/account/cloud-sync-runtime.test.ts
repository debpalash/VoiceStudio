import { afterEach, expect, it, vi } from 'vitest';
import { startCloudSyncRuntime } from './cloud-sync-runtime';

const SHORTCUT = 'voicestudio.shortcut.dictation';
const LOCALE = 'voicestudio.locale';

type Pull = { seq: number; values: Array<{ key: string; value: unknown; raw: boolean }> };
function fakeApi(pulls: Pull[]) {
  const calls: Array<[string, RequestInit | undefined]> = [];
  const api = vi.fn(async (path: string, init?: RequestInit) => {
    calls.push([path, init]);
    if (path === '/api/cloud-sync/local/keys')
      return { keys: [LOCALE, SHORTCUT], raw: [LOCALE, SHORTCUT] };
    if (path.startsWith('/api/cloud-sync/local?since='))
      return pulls.shift() ?? { seq: 9, values: [] };
    return { applied: true };
  });
  const puts = () =>
    calls
      .filter(([p, i]) => p === '/api/cloud-sync/local' && i?.method === 'PUT')
      .map(([, i]) => JSON.parse(String(i!.body)));
  return { api, calls, puts };
}

let stop: (() => void) | undefined;
afterEach(() => {
  stop?.();
  stop = undefined;
  localStorage.clear();
  vi.useRealTimers();
});

it('applies what the backend pulled before mirroring local values, so a stale value never wins', async () => {
  vi.useFakeTimers();
  localStorage.setItem(LOCALE, 'en'); // stale: another device changed it to de
  const { api, calls, puts } = fakeApi([
    { seq: 3, values: [{ key: LOCALE, value: 'de', raw: true }] },
  ]);
  stop = await startCloudSyncRuntime({ api: api as never, intervalMs: 1000 });
  const pulledAt = calls.findIndex(([p]) => p.startsWith('/api/cloud-sync/local?since='));
  const firstPut = calls.findIndex(
    ([p, i]) => p === '/api/cloud-sync/local' && i?.method === 'PUT',
  );
  expect(pulledAt).toBeGreaterThan(-1);
  expect(firstPut === -1 || firstPut > pulledAt).toBe(true);
  expect(localStorage.getItem(LOCALE)).toBe('de');
  expect(puts().every((body) => body.value !== 'en')).toBe(true);
  expect(puts().every((body) => body.seen_seq === 3)).toBe(true);
});

it('mirrors each later change and never echoes a pulled value', async () => {
  vi.useFakeTimers();
  const { api, puts } = fakeApi([
    { seq: 1, values: [] },
    { seq: 4, values: [{ key: LOCALE, value: 'fr', raw: true }] },
  ]);
  stop = await startCloudSyncRuntime({ api: api as never, intervalMs: 1000 });
  await vi.advanceTimersByTimeAsync(1000);
  expect(localStorage.getItem(LOCALE)).toBe('fr');
  expect(puts()).toEqual([]);
  localStorage.setItem(LOCALE, 'it');
  expect(puts().at(-1)).toEqual({ key: LOCALE, value: 'it', seen_seq: 4 });
});

it('applies a pulled shortcut through the capture bridge and keeps it when the OS refuses', async () => {
  vi.useFakeTimers();
  const { api, puts } = fakeApi([
    { seq: 1, values: [{ key: SHORTCUT, value: 'CmdOrCtrl+Alt+D', raw: true }] },
  ]);
  const capture = {
    getShortcut: vi.fn(async () => ({ accelerator: 'CmdOrCtrl+Shift+Space', active: true })),
    setShortcut: vi.fn(async () => ({
      accelerator: 'CmdOrCtrl+Shift+Space',
      active: false,
      error: 'in use',
    })),
  };
  stop = await startCloudSyncRuntime({ api: api as never, capture, intervalMs: 1000 });
  expect(capture.setShortcut).toHaveBeenCalledWith('CmdOrCtrl+Alt+D');
  await vi.advanceTimersByTimeAsync(2000);
  // The refused synced value stays synced: the old local shortcut is never pushed over it.
  expect(puts().filter((body) => body.key === SHORTCUT)).toEqual([]);
});

it('a shortcut the user changes here is mirrored', async () => {
  vi.useFakeTimers();
  const { api, puts } = fakeApi([{ seq: 1, values: [] }]);
  let accelerator = 'CmdOrCtrl+Shift+Space';
  const capture = {
    getShortcut: vi.fn(async () => ({ accelerator, active: true })),
    setShortcut: vi.fn(),
  };
  stop = await startCloudSyncRuntime({ api: api as never, capture, intervalMs: 1000 });
  expect(puts()).toEqual([{ key: SHORTCUT, value: 'CmdOrCtrl+Shift+Space', seen_seq: 1 }]);
  accelerator = 'CmdOrCtrl+Alt+K';
  await vi.advanceTimersByTimeAsync(1000);
  expect(puts().at(-1)).toEqual({ key: SHORTCUT, value: 'CmdOrCtrl+Alt+K', seen_seq: 9 });
});

it('window focus asks for a sync round and stop removes everything', async () => {
  const { api, calls } = fakeApi([]);
  stop = await startCloudSyncRuntime({ api: api as never, intervalMs: 60_000 });
  window.dispatchEvent(new Event('focus'));
  expect(calls.some(([p]) => p === '/api/cloud-sync/run')).toBe(true);
  stop();
  stop = undefined;
  const count = calls.length;
  localStorage.setItem(LOCALE, 'de');
  window.dispatchEvent(new Event('focus'));
  expect(calls.length).toBe(count);
});
