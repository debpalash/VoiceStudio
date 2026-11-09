import { expect, it } from 'vitest';
import { formatHostSnapshot, withDeadline } from './hostSnapshot';

it('gives up on a stalled snapshot after the deadline', async () => {
  expect(await withDeadline(new Promise(() => {}), 20)).toBeNull();
  expect(await withDeadline(Promise.resolve(5), 20)).toBe(5);
  expect(await withDeadline(Promise.reject(new Error('x')), 20)).toBeNull();
});

const snapshot = {
  os: 'win32 Windows 11 Pro (10.0.26100)',
  arch: 'x64',
  cpu: 'Ryzen 9',
  ramGb: 32,
  diskFreeGb: 12.5,
  gpu: 'vendor 0x1002',
  runtime: { pythonPresent: false, uvPresent: true, pathFlags: ['onedrive'] },
};

it('adds machine facts only when the backend could not supply them', () => {
  const down = formatHostSnapshot(snapshot, { machine: true });
  expect(down).toContain('os (app): win32 Windows 11 Pro (10.0.26100)');
  expect(down).toContain('runtime python: MISSING');
  expect(down).toContain('runtime path flags: onedrive');
  const up = formatHostSnapshot(snapshot, { machine: false });
  expect(up.some((line) => line.startsWith('os (app)'))).toBe(false);
  expect(up).toContain('runtime python: MISSING');
});

it('is empty without a snapshot and says unknown for unresolved files', () => {
  expect(formatHostSnapshot(null, { machine: true })).toEqual([]);
  expect(
    formatHostSnapshot({ runtime: { pythonPresent: null, uvPresent: null } }, { machine: false }),
  ).toEqual(['runtime python: unknown', 'runtime uv: unknown', 'runtime path flags: none']);
});
