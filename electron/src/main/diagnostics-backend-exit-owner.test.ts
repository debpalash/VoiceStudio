// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const fixture = vi.hoisted(() => ({ root: '/nonexistent-diagnostic-exit-owner', spawn: vi.fn() }));
vi.mock('electron', () => ({
  app: { isPackaged: false, getPath: () => fixture.root },
}));
vi.mock('node:child_process', async (original) => ({
  ...(await original<typeof import('node:child_process')>()),
  spawn: fixture.spawn,
}));
import { BackendSupervisor } from './backend';
import { DiagnosticReports } from './diagnostics';
import { DiagnosticStatusObserver } from './diagnostics-capture';

it.each(['installing', 'starting'] as const)(
  'retains the original %s attempt permission through progress, stalls and terminal failure',
  async (initialStage) => {
    directory = await mkdtemp(join(tmpdir(), 'voice-diagnostic-attempt-owner-'));
    fixture.root = directory;
    const send = vi.fn(
      async (_url: unknown, _options?: RequestInit) => new Response('', { status: 202 }),
    );
    reports = new DiagnosticReports({
      directory: join(directory, 'diagnostics'),
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
    supervisor = new BackendSupervisor();
    const observer = new DiagnosticStatusObserver(reports);
    supervisor.subscribe((status) => observer.observe(status));
    const owner = supervisor as unknown as {
      setStage(stage: string, patch?: { message?: string }): void;
    };
    for (const control of ['off-to-on', 'a-to-b', 'same-grant'] as const) {
      if (control !== 'off-to-on') {
        await reports.setConsent(false);
        await reports.setConsent(true);
      }
      owner.setStage(initialStage);
      if (control === 'off-to-on') await reports.setConsent(true);
      if (control === 'a-to-b') {
        await reports.setConsent(false);
        await reports.setConsent(true);
      }
      owner.setStage(initialStage);
      if (initialStage === 'installing') {
        observer.observe({
          ...supervisor.status,
          setupProgress: { completedDownloads: 0, stalled: true, stalledSeconds: 700 },
        });
        owner.setStage('starting');
      }
      owner.setStage('setup_required', {
        message: 'Failed to download a package: connection reset; PRIVATE_KEY',
      });
      await reports.flush();
      expect(send).toHaveBeenCalledTimes(
        control === 'same-grant' ? (initialStage === 'installing' ? 2 : 1) : 0,
      );
      owner.setStage('idle');
    }
    expect(String(send.mock.calls[0][1]?.body)).not.toContain('PRIVATE_KEY');
  },
);

let reports: DiagnosticReports | undefined;
let supervisor: BackendSupervisor | undefined;
let directory: string | undefined;
afterEach(async () => {
  await supervisor?.shutdown();
  await reports?.close();
  vi.useRealTimers();
  vi.restoreAllMocks();
  if (directory) await rm(directory, { recursive: true, force: true });
});

it.each(['off-to-on', 'a-to-b', 'same-grant', 'on-to-off', 'replacement'] as const)(
  'binds a child exit to its original permission across a held replacement probe: %s',
  async (control) => {
    directory = await mkdtemp(join(tmpdir(), 'voice-diagnostic-exit-owner-'));
    fixture.root = directory;
    const send = vi.fn(
      async (_url: unknown, _options?: RequestInit) => new Response('', { status: 202 }),
    );
    reports = new DiagnosticReports({
      directory: join(directory, 'diagnostics'),
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
    if (control !== 'off-to-on') await reports.setConsent(true);
    const originalEpoch = reports.status().grant_epoch;
    supervisor = new BackendSupervisor();
    const observer = new DiagnosticStatusObserver(reports);
    supervisor.subscribe((status) => observer.observe(status)); // Actual index.ts wiring.
    const child = Object.assign(new EventEmitter(), {
      pid: 1234567,
      stdin: new PassThrough(),
      stdout: new PassThrough(),
      stderr: new PassThrough(),
    });
    fixture.spawn.mockReturnValue(child);
    const owner = supervisor as unknown as {
      generation: number;
      spawnChild(plan: { argv: string[]; cwd: string }, generation: number): void;
      probe(): Promise<boolean>;
      setStage(stage: 'attaching', patch: { message?: string }): void;
    };
    let release!: (found: boolean) => void;
    const held = new Promise<boolean>((resolve) => {
      release = resolve;
    });
    vi.spyOn(owner, 'probe')
      .mockImplementationOnce(() => held)
      .mockResolvedValue(control === 'replacement');
    owner.generation = 1;
    vi.useFakeTimers();
    owner.spawnChild({ argv: ['fixture-python'], cwd: directory }, 1);
    child.emit('exit', null, 'SIGSEGV');
    expect(supervisor.status.stage).toBe('attaching');
    if (control === 'off-to-on') await reports.setConsent(true);
    if (control === 'a-to-b') {
      await reports.setConsent(false);
      await reports.setConsent(true);
    }
    if (control === 'on-to-off') await reports.setConsent(false);
    // A delayed/repeated publication during the same held probe is not a new
    // lifecycle unit and cannot refresh the original permission observation.
    owner.setStage('attaching', { message: 'Still checking replacement' });
    release(control === 'replacement');
    await vi.advanceTimersByTimeAsync(11_000);
    if (control === 'replacement') expect(supervisor.status.stage).toBe('ready');
    else {
      expect(supervisor.status.stage).toBe('crashed');
      expect(supervisor.status.failure?.code).toMatch(/^NATIVE_/);
    }
    await reports.flush();
    expect(send).toHaveBeenCalledTimes(control === 'same-grant' ? 1 : 0);
    if (control === 'same-grant') {
      expect(reports.status().grant_epoch).toBe(originalEpoch);
      expect(JSON.parse(String(send.mock.calls[0][1]?.body)).props).toMatchObject({
        category: 'backend_crash',
        reason: 'crashed',
        signal: 'SIGSEGV',
      });
    }
    if (control === 'off-to-on' || control === 'a-to-b') {
      const next = Object.assign(new EventEmitter(), {
        pid: 1234568,
        stdin: new PassThrough(),
        stdout: new PassThrough(),
        stderr: new PassThrough(),
      });
      fixture.spawn.mockReturnValue(next);
      owner.spawnChild({ argv: ['fixture-python'], cwd: directory }, owner.generation);
      next.emit('exit', null, 'SIGSEGV');
      await vi.advanceTimersByTimeAsync(11_000);
      expect(supervisor.status.stage).toBe('crashed');
      await reports.flush();
      expect(send).toHaveBeenCalledTimes(1); // Fresh later unit owns its current grant.
    }
  },
);

it.each(['failed', 'setup_required'] as const)(
  'keeps immediate %s failures eligible under current consent',
  async (stage) => {
    directory = await mkdtemp(join(tmpdir(), 'voice-diagnostic-immediate-owner-'));
    fixture.root = directory;
    const send = vi.fn(
      async (_url: unknown, _options?: RequestInit) => new Response('', { status: 202 }),
    );
    reports = new DiagnosticReports({
      directory: join(directory, 'diagnostics'),
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
    supervisor = new BackendSupervisor();
    const observer = new DiagnosticStatusObserver(reports);
    supervisor.subscribe((status) => observer.observe(status));
    const owner = supervisor as unknown as {
      setStage(stage: string, patch: { message: string }): void;
    };
    owner.setStage(stage, {
      message: 'Failed to download a package: connection reset; PRIVATE_USER_KEY',
    });
    await reports.flush();
    expect(send).toHaveBeenCalledTimes(1);
    expect(String(send.mock.calls[0][1]?.body)).not.toContain('PRIVATE_USER_KEY');
  },
);
