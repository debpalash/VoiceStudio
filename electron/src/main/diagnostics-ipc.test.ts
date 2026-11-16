import { afterEach, expect, it, vi } from 'vitest';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { IpcMainInvokeEvent } from 'electron';
import type { VoiceStudioBridge } from '../preload/index.d';
const bridgeMock = vi.hoisted(() => ({
  bridge: null as VoiceStudioBridge | null,
  invoke: vi.fn(),
  handlers: new Map<
    string,
    (event: IpcMainInvokeEvent, value?: unknown, expectedEpoch?: unknown) => unknown
  >(),
}));
vi.mock('electron', () => ({
  contextBridge: {
    exposeInMainWorld: (_key: string, bridge: VoiceStudioBridge) => {
      bridgeMock.bridge = bridge;
    },
  },
  ipcRenderer: { invoke: bridgeMock.invoke, on: vi.fn(), send: vi.fn(), removeListener: vi.fn() },
  ipcMain: {
    handle: (
      channel: string,
      handler: (event: IpcMainInvokeEvent, value?: unknown, expectedEpoch?: unknown) => unknown,
    ) => bridgeMock.handlers.set(channel, handler),
    removeHandler: (channel: string) => bridgeMock.handlers.delete(channel),
  },
}));
import { DiagnosticReports } from './diagnostics';
import { registerDiagnosticIpc } from './diagnostics-ipc';
import { DIAGNOSTIC_CHANNELS } from '../preload/diagnostics';
import '../preload/index';

let directory: string | undefined;
let reports: DiagnosticReports | undefined;
afterEach(async () => {
  await reports?.close();
  if (directory) await rm(directory, { recursive: true, force: true });
  bridgeMock.handlers.clear();
  vi.clearAllMocks();
});

it('exposes exactly the declared diagnostics namespace through the actual preload bridge', async () => {
  const diagnostics = bridgeMock.bridge!.diagnostics;
  await diagnostics.getStatus();
  await diagnostics.setConsent(false);
  await diagnostics.capture(
    { category: 'operation_failed', source: 'renderer', stage: 'other' },
    'a'.repeat(32),
  );
  expect(bridgeMock.invoke.mock.calls).toEqual([
    [DIAGNOSTIC_CHANNELS.getStatus],
    [DIAGNOSTIC_CHANNELS.setConsent, false],
    [
      DIAGNOSTIC_CHANNELS.capture,
      { category: 'operation_failed', source: 'renderer', stage: 'other' },
      'a'.repeat(32),
    ],
  ]);
});

it('binds renderer capture atomically to the owner grant across another window OFF then ON', async () => {
  directory = await mkdtemp(join(tmpdir(), 'voice-diagnostic-owner-'));
  const send = vi.fn(async () => new Response('', { status: 202 }));
  reports = new DiagnosticReports({
    directory,
    identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' },
    env: {},
    destination: {
      site: 'fixture',
      host: 'https://fixture.invalid',
      appUrl: 'https://fixture-app.invalid',
    },
    send,
    automatic: false,
  });
  await reports.initialize();
  await reports.setConsent(true);
  const oldEpoch = JSON.parse(await readFile(join(directory, 'consent.json'), 'utf8')).epoch;
  const trusted = {} as IpcMainInvokeEvent;
  registerDiagnosticIpc(reports, (event) => {
    if (event !== trusted) throw new Error('Untrusted');
  }, Promise.resolve());
  await reports.setConsent(false);
  await reports.setConsent(true);
  const currentEpoch = JSON.parse(await readFile(join(directory, 'consent.json'), 'utf8')).epoch;
  expect(currentEpoch).not.toBe(oldEpoch);
  const capture = bridgeMock.handlers.get(DIAGNOSTIC_CHANNELS.capture)! as (
    ...args: unknown[]
  ) => unknown;
  capture(trusted, { category: 'operation_failed', source: 'renderer' }, oldEpoch);
  capture(trusted, { category: 'operation_failed', source: 'renderer' });
  capture(trusted, { category: 'operation_failed', source: 'renderer' }, 'malformed');
  await reports.flush();
  expect(send).not.toHaveBeenCalled();
  capture(trusted, { category: 'operation_failed', source: 'renderer' }, currentEpoch);
  await reports.flush();
  expect(send).toHaveBeenCalledOnce();
  expect(JSON.stringify(send.mock.calls)).not.toContain(currentEpoch);
});

it('gates all real handlers before reads/writes and rejects raw/arbitrary transport input', async () => {
  directory = await mkdtemp(join(tmpdir(), 'voice-diagnostic-ipc-'));
  const send = vi.fn(async () => new Response('', { status: 202 }));
  reports = new DiagnosticReports({
    directory,
    identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' },
    env: {},
    destination: {
      site: 'fixture',
      host: 'https://fixture.invalid',
      appUrl: 'https://fixture-app.invalid',
    },
    send,
    automatic: false,
  });
  const trusted = {} as IpcMainInvokeEvent;
  const assertTrusted = (event: IpcMainInvokeEvent) => {
    if (event !== trusted) throw new Error('Untrusted main-frame request');
  };
  const stop = registerDiagnosticIpc(reports, assertTrusted, reports.initialize());
  for (const channel of Object.values(DIAGNOSTIC_CHANNELS)) {
    await expect(
      Promise.resolve().then(() =>
        bridgeMock.handlers.get(channel)!({} as IpcMainInvokeEvent, true),
      ),
    ).rejects.toThrow('Untrusted');
  }
  await expect(
    bridgeMock.handlers.get(DIAGNOSTIC_CHANNELS.setConsent)!(trusted, 'true'),
  ).rejects.toThrow('Invalid');
  await bridgeMock.handlers.get(DIAGNOSTIC_CHANNELS.setConsent)!(trusted, true);
  for (const raw of [
    { category: 'app_crash', message: 'PRIVATE_TOKEN' },
    { category: 'app_crash', url: 'https://private.invalid' },
    { category: 'app_crash', source: 'backend' },
  ]) {
    expect(() => bridgeMock.handlers.get(DIAGNOSTIC_CHANNELS.capture)!(trusted, raw)).toThrow(
      'Invalid',
    );
  }
  await bridgeMock.handlers.get(DIAGNOSTIC_CHANNELS.capture)!(
    trusted,
    {
      category: 'operation_failed',
      source: 'renderer',
      error_type: 'TypeError',
      frames: undefined,
    },
    reports.status().grant_epoch,
  );
  await reports.flush();
  expect(send).toHaveBeenCalledTimes(1);
  stop();
  expect(bridgeMock.handlers.size).toBe(0);
});
