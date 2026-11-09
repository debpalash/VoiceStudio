// @vitest-environment node
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import {
  devInstanceFile,
  processAlive,
  recordDevInstance,
  waitForPreviousDevInstance,
} from './dev-lock-handoff';

const file = () => devInstanceFile(mkdtempSync(join(tmpdir(), 'vs-dev-lock-')));

it('waits for the previous dev instance to exit before asking for the lock (#3813)', () => {
  const path = file();
  recordDevInstance(path, 4242);
  expect(readFileSync(path, 'utf8')).toBe('4242');
  let clock = 0;
  const alive = vi.fn((pid: number) => pid === 4242 && clock < 1_500);
  const sleep = vi.fn((ms: number) => void (clock += ms));
  expect(waitForPreviousDevInstance({ file: path, self: 1, alive, sleep, now: () => clock })).toBe(
    true,
  );
  expect(clock).toBe(1_500);
});

it('gives up after the timeout, so a stuck instance cannot hang the next one forever', () => {
  const path = file();
  recordDevInstance(path, 4242);
  let clock = 0;
  expect(
    waitForPreviousDevInstance({
      file: path,
      self: 1,
      timeoutMs: 20_000,
      alive: () => true,
      sleep: (ms) => void (clock += ms),
      now: () => clock,
    }),
  ).toBe(false);
  expect(clock).toBe(20_000);
});

it('never waits without a live, foreign, well-formed record', () => {
  const sleep = vi.fn();
  const alive = vi.fn(() => true);
  const missing = file();
  expect(waitForPreviousDevInstance({ file: missing, alive, sleep })).toBe(true);
  const self = file();
  recordDevInstance(self, 77);
  expect(waitForPreviousDevInstance({ file: self, self: 77, alive, sleep })).toBe(true);
  const junk = file();
  writeFileSync(junk, 'not a pid');
  expect(waitForPreviousDevInstance({ file: junk, alive, sleep })).toBe(true);
  const gone = file();
  recordDevInstance(gone, 4242);
  expect(waitForPreviousDevInstance({ file: gone, alive: () => false, sleep })).toBe(true);
  expect(sleep).not.toHaveBeenCalled();
});

it('detects whether a pid is running', () => {
  expect(processAlive(process.pid)).toBe(true);
  expect(processAlive(2 ** 30)).toBe(false);
});
