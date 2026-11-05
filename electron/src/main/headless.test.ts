// @vitest-environment node
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import type { BackendStage } from '../preload/index.d';
import {
  clearDiscovery,
  DEFAULT_IDLE_SHUTDOWN_S,
  DISCOVERY_FILE,
  IdleShutdown,
  idleShutdownMs,
  parseLaunchIntent,
  relaunchArguments,
  writeDiscovery,
  type BackendActivity,
} from './headless';

const MINUTE = 60_000;

function harness(initial: { stage?: BackendStage; activity?: BackendActivity | null } = {}) {
  let now = 0;
  const state = {
    window: false,
    stage: initial.stage ?? ('ready' as BackendStage),
    activity:
      initial.activity === undefined
        ? ({ inflight: 0, busy: [], idle_seconds: 0 } as BackendActivity | null)
        : initial.activity,
  };
  const onIdle = vi.fn();
  const idle = new IdleShutdown({
    idleMs: 10 * MINUTE,
    hasWindow: () => state.window,
    stage: () => state.stage,
    readActivity: async () => state.activity,
    onIdle,
    now: () => now,
  });
  const advance = (ms: number) => {
    now += ms;
    if (state.activity) state.activity.idle_seconds += ms / 1000;
  };
  return { idle, state, onIdle, advance };
}

test('stops a quiet headless app after the idle period, once', async () => {
  const { idle, onIdle, advance } = harness();
  advance(9 * MINUTE);
  expect(await idle.tick()).toBe(false);
  advance(MINUTE);
  expect(await idle.tick()).toBe(true);
  expect(await idle.tick()).toBe(true);
  expect(onIdle).toHaveBeenCalledTimes(1);
});

test('an open window keeps the app and restarts the idle period when it closes', async () => {
  const { idle, state, onIdle, advance } = harness();
  state.window = true;
  advance(30 * MINUTE);
  expect(await idle.tick()).toBe(false);
  state.window = false;
  advance(9 * MINUTE);
  expect(await idle.tick()).toBe(false);
  advance(MINUTE);
  expect(await idle.tick()).toBe(true);
  expect(onIdle).toHaveBeenCalledOnce();
});

test('running work and open requests are never idle, however long they take', async () => {
  const { idle, state, onIdle, advance } = harness();
  state.activity = { inflight: 0, busy: ['jobs'], idle_seconds: 0 };
  for (let i = 0; i < 12; i++) {
    advance(10 * MINUTE);
    state.activity.idle_seconds = 0;
    expect(await idle.tick()).toBe(false);
  }
  state.activity = { inflight: 1, busy: [], idle_seconds: 0 };
  advance(20 * MINUTE);
  expect(await idle.tick()).toBe(false);
  expect(onIdle).not.toHaveBeenCalled();
});

test('the backend clock wins when a terminal request arrived recently', async () => {
  const { idle, state, advance } = harness();
  advance(15 * MINUTE);
  state.activity = { inflight: 0, busy: [], idle_seconds: 60 };
  expect(await idle.tick()).toBe(false);
});

test('unknown activity on a live backend, and any startup, keep the app alive', async () => {
  const unknown = harness({ activity: null });
  unknown.advance(60 * MINUTE);
  expect(await unknown.idle.tick()).toBe(false);
  unknown.state.stage = 'unresponsive';
  unknown.advance(60 * MINUTE);
  expect(await unknown.idle.tick()).toBe(false);
  for (const stage of ['attaching', 'starting', 'installing'] as const) {
    const starting = harness({ stage, activity: null });
    starting.advance(60 * MINUTE);
    expect(await starting.idle.tick()).toBe(false);
  }
});

test('a failed backend is judged by the clock, so a broken start does not linger', async () => {
  const { idle, onIdle, advance } = harness({ stage: 'failed', activity: null });
  advance(10 * MINUTE);
  expect(await idle.tick()).toBe(true);
  expect(onIdle).toHaveBeenCalledOnce();
});

test('the idle period is configurable and rejects nonsense', () => {
  expect(idleShutdownMs('30')).toBe(30_000);
  for (const raw of [undefined, '', '0', '-5', 'soon'])
    expect(idleShutdownMs(raw)).toBe(DEFAULT_IDLE_SHUTDOWN_S * 1000);
});

test('launch intent survives a relaunch without Chromium switches', () => {
  const argv = [
    '/opt/VoiceStudio/voicestudio-desktop',
    '--voicestudio-background',
    '--original-process-start-time=1',
  ];
  const intent = parseLaunchIntent(argv);
  expect(intent).toEqual({ headless: true, stop: false, checkUpdates: false });
  expect(relaunchArguments(intent)).toEqual(['--voicestudio-background']);
  expect(relaunchArguments(parseLaunchIntent(['x', '--voicestudio-check-updates']))).toEqual([
    '--voicestudio-check-updates',
  ]);
  expect(relaunchArguments(parseLaunchIntent(['x']))).toEqual([]);
  expect(
    relaunchArguments(parseLaunchIntent(['x', '--voicestudio-stop', '--voicestudio-background'])),
  ).toEqual([]);
});

const directories: string[] = [];
afterEach(() => {
  for (const directory of directories.splice(0))
    rmSync(directory, { recursive: true, force: true });
});

test('discovery is replaced atomically and cleared only by its owner', () => {
  const directory = mkdtempSync(join(tmpdir(), 'vs-discovery-'));
  directories.push(directory);
  const record = {
    schema: 1 as const,
    stage: 'ready' as const,
    url: 'http://127.0.0.1:4900',
    pid: 100,
    version: '1.0.0',
    headless: true,
    window: false,
    remote: false,
  };
  writeDiscovery(directory, record);
  writeDiscovery(directory, { ...record, window: true });
  expect(JSON.parse(readFileSync(join(directory, DISCOVERY_FILE), 'utf8'))).toEqual({
    ...record,
    window: true,
  });
  clearDiscovery(directory, 200);
  expect(JSON.parse(readFileSync(join(directory, DISCOVERY_FILE), 'utf8')).pid).toBe(100);
  clearDiscovery(directory, 100);
  expect(() => readFileSync(join(directory, DISCOVERY_FILE))).toThrow();
  writeFileSync(join(directory, DISCOVERY_FILE), 'not json');
  clearDiscovery(directory, 100);
  expect(readFileSync(join(directory, DISCOVERY_FILE), 'utf8')).toBe('not json');
});
