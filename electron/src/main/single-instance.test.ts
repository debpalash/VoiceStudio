import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { recordDevInstance, waitForPreviousDevInstance } from './single-instance';

const pidFile = () =>
  join(mkdtempSync(join(tmpdir(), 'vs-dev-pid-')), 'nested', 'dev-instance.pid');

function clock() {
  let t = 0;
  return { now: () => t, sleep: async (ms: number) => void (t += ms) };
}

it('records the running development instance', () => {
  const file = pidFile();
  recordDevInstance(file, 4242);
  expect(readFileSync(file, 'utf8')).toBe('4242');
});

it('waits for the previous instance to exit before asking for the lock', async () => {
  const file = pidFile();
  recordDevInstance(file, 100);
  let checks = 0;
  const c = clock();
  await waitForPreviousDevInstance(file, { ownPid: 200, isAlive: () => ++checks < 4, ...c });
  expect(checks).toBe(4);
  expect(c.now()).toBe(300);
});

it('stops waiting after the timeout when the previous instance hangs', async () => {
  const file = pidFile();
  recordDevInstance(file, 100);
  const c = clock();
  await waitForPreviousDevInstance(file, {
    ownPid: 200,
    isAlive: () => true,
    timeoutMs: 1_000,
    ...c,
  });
  expect(c.now()).toBe(1_000);
});

it('does not wait without a usable record, or for itself', async () => {
  const isAlive = () => {
    throw new Error('should not be asked');
  };
  const file = pidFile();
  await waitForPreviousDevInstance(file, { ownPid: 200, isAlive });
  recordDevInstance(file, 200);
  await waitForPreviousDevInstance(file, { ownPid: 200, isAlive });
  writeFileSync(file, 'not-a-pid');
  await waitForPreviousDevInstance(file, { ownPid: 200, isAlive });
});

it('treats a pid that no longer exists as gone', async () => {
  const file = pidFile();
  recordDevInstance(file, 2 ** 22 + 12345); // Above macOS and Linux pid limits.
  const c = clock();
  await waitForPreviousDevInstance(file, { ownPid: 200, ...c });
  expect(c.now()).toBe(0);
});
