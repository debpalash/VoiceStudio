import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import { CloudSwitchStore } from './cloud-switches';

const file = () => join(mkdtempSync(join(tmpdir(), 'vs-switch-')), 'cloud-switches.json');

it('starts with everything off and unprompted', () => {
  expect(new CloudSwitchStore(file()).get()).toEqual({
    accountEnabled: false,
    syncEnabled: false,
    prompted: false,
  });
});

it('a corrupt file reads as all off', () => {
  const path = file();
  writeFileSync(path, '{"accountEnabled": tr');
  expect(new CloudSwitchStore(path).get().accountEnabled).toBe(false);
});

it('persists across instances (restart)', () => {
  const path = file();
  new CloudSwitchStore(path).set({ accountEnabled: true, syncEnabled: true, prompted: true });
  expect(new CloudSwitchStore(path).get()).toEqual({
    accountEnabled: true,
    syncEnabled: true,
    prompted: true,
  });
  expect(JSON.parse(readFileSync(path, 'utf8')).syncEnabled).toBe(true);
});

it('sync cannot be on while account features are off', () => {
  const store = new CloudSwitchStore(file());
  expect(store.set({ syncEnabled: true }).syncEnabled).toBe(false);
  store.set({ accountEnabled: true, syncEnabled: true });
  expect(store.set({ accountEnabled: false })).toMatchObject({
    accountEnabled: false,
    syncEnabled: false,
  });
});

it('notifies subscribers with the previous value', () => {
  const store = new CloudSwitchStore(file());
  const cb = vi.fn();
  store.subscribe(cb);
  store.set({ accountEnabled: true });
  expect(cb).toHaveBeenCalledWith(
    expect.objectContaining({ accountEnabled: true }),
    expect.objectContaining({ accountEnabled: false }),
  );
});

it('a failed write still applies the change for this run, notifies, then reports the failure', () => {
  const dir = mkdtempSync(join(tmpdir(), 'vs-switch-'));
  const blocker = join(dir, 'not-a-dir');
  writeFileSync(blocker, '');
  const store = new CloudSwitchStore(join(blocker, 'cloud-switches.json'));
  // The first write already fails (ENOTDIR), like a full disk would.
  const cb = vi.fn();
  store.subscribe(cb);
  expect(() => store.set({ accountEnabled: true })).toThrow();
  expect(store.get().accountEnabled).toBe(true);
  expect(cb).toHaveBeenCalledOnce();
});
