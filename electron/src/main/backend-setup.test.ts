// @vitest-environment node
import { beforeEach, afterEach, expect, it, onTestFinished, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { chmodSync, existsSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const mocks = vi.hoisted(() => ({
  runtimeConfig: null as { root: string; owned: boolean } | null,
  existingProject: false,
  existingRoot: false,
  dependencies: vi.fn(async (_project?: string) => true),
  ready: vi.fn(async () => false),
  compatible: vi.fn(async () => false),
  interrupted: vi.fn(async () => false),
  install: vi.fn(),
  promoteCaches: vi.fn(),
  rm: vi.fn(),
  stage: vi.fn(),
  spawn: vi.fn(),
}));
vi.mock('electron', () => ({ app: { isPackaged: true, getPath: () => '/private/voicestudio' } }));
vi.mock('node:child_process', () => ({ spawn: mocks.spawn, spawnSync: vi.fn() }));
vi.mock('node:fs', async (importOriginal) => {
  const original = await importOriginal<typeof import('node:fs')>();
  return {
    ...original,
    readFileSync: (...args: Parameters<typeof original.readFileSync>) =>
      String(args[0]).endsWith('runtime-location.json') && mocks.runtimeConfig
        ? JSON.stringify(mocks.runtimeConfig)
        : original.readFileSync(...args),
    writeFileSync: (...args: Parameters<typeof original.writeFileSync>) => {
      if (String(args[0]).endsWith('runtime-location.json')) {
        mocks.runtimeConfig = JSON.parse(String(args[1]));
        return;
      }
      return original.writeFileSync(...args);
    },
    mkdirSync: (...args: Parameters<typeof original.mkdirSync>) =>
      String(args[0]).includes('private') ? undefined : original.mkdirSync(...args),
    existsSync: (path: Parameters<typeof original.existsSync>[0]) =>
      String(path).includes('selected')
        ? String(path).endsWith('project')
          ? mocks.existingProject
          : mocks.existingRoot
        : original.existsSync(path),
  };
});
vi.mock('node:fs/promises', () => ({ rm: mocks.rm }));
vi.mock('./runtime-project', () => ({
  runtimeDependenciesReady: mocks.dependencies,
  runtimeReady: mocks.ready,
  runtimeCompatible: mocks.compatible,
  runtimeInstallInterrupted: mocks.interrupted,
  installRuntime: mocks.install,
  promoteLegacyRuntimeCaches: mocks.promoteCaches,
  stageRuntimeSources: mocks.stage,
  runtimePython: (root: string) => root + '/.venv/bin/python',
}));
import {
  BackendSupervisor,
  bundledUvPath,
  bundledWebUiPath,
  isExpectedPipeClose,
  isUnsupportedPlatform,
  managedBackendSpawnOptions,
  platformSetupIssue,
} from './backend';
import { app } from 'electron';

beforeEach(() => {
  // Generic installation fixtures need a supported host. Intel cases override it.
  if (process.platform === 'darwin') vi.spyOn(process, 'arch', 'get').mockReturnValue('arm64');
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  mocks.runtimeConfig = null;
  mocks.existingProject = false;
  mocks.existingRoot = false;
  mocks.dependencies.mockResolvedValue(true);
  mocks.ready.mockResolvedValue(false);
  mocks.compatible.mockResolvedValue(false);
  mocks.interrupted.mockResolvedValue(false);
  mocks.promoteCaches.mockResolvedValue(undefined);
});

it('resolves the packaged uv executable for each desktop platform', () => {
  const normalized = (value: string) => value.replaceAll('\\', '/');
  expect(normalized(bundledUvPath('/resources', 'win32'))).toBe('/resources/tools/uv.exe');
  expect(normalized(bundledUvPath('/resources', 'darwin'))).toBe('/resources/tools/uv');
  expect(normalized(bundledUvPath('/resources', 'linux'))).toBe('/resources/tools/uv');
});

it('reports a resumable runtime when startup finds an interrupted install marker', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  mocks.interrupted.mockResolvedValueOnce(true);
  const supervisor = new BackendSupervisor();

  await supervisor.start();

  expect(supervisor.status.stage).toBe('setup_required');
  expect(supervisor.status.runtimeInterrupted).toBe(true);
  expect(mocks.install).not.toHaveBeenCalled();
  await supervisor.shutdown();
});

it('publishes packaged startup preflight failures instead of rejecting startup', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  mocks.ready.mockRejectedValueOnce(new Error('runtime metadata unreadable'));
  const supervisor = new BackendSupervisor();

  await expect(supervisor.start()).resolves.toBeUndefined();

  expect(supervisor.status.stage).toBe('failed');
  expect(supervisor.status.message).toBe('runtime metadata unreadable');
  expect(supervisor.status.logTail).toContain('runtime metadata unreadable');
  await supervisor.shutdown();
});

it('passes a live nested-operation drain descriptor to POSIX managed backends', () => {
  const linux = managedBackendSpawnOptions(3900, 'auto', 'linux');
  expect(linux.drainFd).toBe(3);
  expect(linux.stdio).toEqual(['pipe', 'pipe', 'pipe', 'pipe']);
  expect(linux.env.OMNIVOICE_DESKTOP_CONTAINED).toBe('1');
  expect(linux.env.OMNIVOICE_DESKTOP_DRAIN_FD).toBe('3');

  const windows = managedBackendSpawnOptions(3900, 'auto', 'win32');
  expect(windows.drainFd).toBeNull();
  expect(windows.stdio).toEqual(['pipe', 'pipe', 'pipe']);
  expect(windows.env.OMNIVOICE_DESKTOP_DRAIN_FD).toBeUndefined();
});

it('passes the packaged analytics destination to its managed backend', () => {
  vi.stubGlobal('__POSTHOG_PROJECT_TOKEN__', 'publishable-test-key');
  vi.stubGlobal('__POSTHOG_HOST__', 'https://us.i.posthog.com');
  const { env } = managedBackendSpawnOptions(3900);
  expect(env.POSTHOG_PROJECT_TOKEN).toBe('publishable-test-key');
  expect(env.POSTHOG_HOST).toBe('https://us.i.posthog.com');
});

it('recognizes only expected child-pipe close errors', () => {
  expect(isExpectedPipeClose(Object.assign(new Error('closed'), { code: 'EPIPE' }))).toBe(true);
  expect(isExpectedPipeClose(Object.assign(new Error('reset'), { code: 'ECONNRESET' }))).toBe(true);
  expect(
    isExpectedPipeClose(
      Object.assign(new Error('premature'), { code: 'ERR_STREAM_PREMATURE_CLOSE' }),
    ),
  ).toBe(true);
  expect(isExpectedPipeClose(Object.assign(new Error('disk'), { code: 'EIO' }))).toBe(false);
  expect(isExpectedPipeClose(new Error('broken pipe text without a pipe code'))).toBe(false);
});

it('drains partial output without crashing when a child output pipe closes', () => {
  const supervisor = new BackendSupervisor();
  const stream = Object.assign(new EventEmitter(), { setEncoding: vi.fn() });
  const internal = supervisor as unknown as {
    attachLineReader: (readable: NodeJS.ReadableStream, kind: 'out' | 'err') => void;
  };

  internal.attachLineReader(stream as unknown as NodeJS.ReadableStream, 'out');
  stream.emit('data', 'last useful line');
  expect(() =>
    stream.emit('error', Object.assign(new Error('pipe closed'), { code: 'EPIPE' })),
  ).not.toThrow();
  expect(supervisor.status.logTail).toContain('last useful line');
  expect(supervisor.status.logTail).not.toContain('pipe closed');
});

it('uses the lightweight health contract for recurring liveness probes', async () => {
  const fetchMock = vi.fn(
    async (_input: string | URL | Request) =>
      new Response(JSON.stringify({ status: 'ok', version: '0.5.2' }), {
        status: 200,
        headers: { 'content-type': 'application/json', 'x-omnivoice-backend': '0.5.2' },
      }),
  );
  vi.stubGlobal('fetch', fetchMock);
  const supervisor = new BackendSupervisor();

  const ready = await (supervisor as unknown as { probe: () => Promise<boolean> }).probe();

  expect(ready).toBe(true);
  expect(fetchMock).toHaveBeenCalledOnce();
  expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/health$/);
  expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain('/system/info');
  await supervisor.shutdown();
});

it('attaches to a healthy replacement instead of reporting its exited child as crashed', async () => {
  vi.useFakeTimers();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok', version: '0.5.2' }), {
        status: 200,
        headers: { 'content-type': 'application/json', 'x-omnivoice-backend': '0.5.2' },
      }),
    );
  vi.stubGlobal('fetch', fetchMock);
  const child = Object.assign(new EventEmitter(), {
    stdin: null,
    stdout: null,
    stderr: null,
    exitCode: null,
    signalCode: null,
    pid: 123,
  });
  mocks.spawn.mockReturnValueOnce(child);
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as {
    generation: number;
    spawnChild: (plan: { argv: string[]; cwd: string }, generation: number) => void;
  };
  internal.generation = 1;
  internal.spawnChild({ argv: ['python'], cwd: '/project' }, 1);

  child.emit('exit', 0, null);
  await vi.advanceTimersByTimeAsync(500);

  expect(supervisor.status.stage).toBe('ready');
  expect(supervisor.status.managed).toBe(false);
  expect(supervisor.status.exitCode).toBeUndefined();
  expect(supervisor.status.logTail).toContain('Attached to the replacement VoiceStudio backend.');
  await supervisor.shutdown();
  vi.useRealTimers();
});
it.each([
  {
    label: 'managed',
    backendCmd: '',
    advice: 'repair the local runtime',
    forbidden: 'can be launched',
  },
  {
    label: 'custom command',
    backendCmd: '["custom-python"]',
    advice: 'can be launched',
    forbidden: 'local runtime',
  },
])('scopes spawn-failure advice to the $label launch', ({ backendCmd, advice, forbidden }) => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', backendCmd);
  const child = Object.assign(new EventEmitter(), {
    stdin: null,
    stdout: null,
    stderr: null,
    stdio: [],
    pid: 4242,
  });
  mocks.spawn.mockReturnValueOnce(child);
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as {
    generation: number;
    spawnChild: (plan: { argv: string[]; cwd: string }, generation: number) => void;
  };
  internal.generation = 1;
  internal.spawnChild({ argv: ['missing-backend'], cwd: '/project' }, 1);

  child.emit('error', Object.assign(new Error('spawn ENOENT'), { code: 'ENOENT' }));

  expect(supervisor.status.stage).toBe('failed');
  expect(supervisor.status.message).toContain('missing-backend');
  expect(supervisor.status.message).toContain('spawn ENOENT');
  expect(supervisor.status.message).toContain(advice);
  expect(supervisor.status.message).not.toContain(forbidden);
});
it('startup and retry await explicit setup without staging, spawning or installing', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  expect(supervisor.status.stage).toBe('setup_required');
  await supervisor.restart();
  expect(supervisor.status.stage).toBe('setup_required');
  expect(mocks.spawn).not.toHaveBeenCalled();
  expect(mocks.stage).not.toHaveBeenCalled();
  expect(mocks.install).not.toHaveBeenCalled();
  await supervisor.shutdown();
});
it('a failed explicit installation returns to setup and allows another attempt', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  mocks.install.mockRejectedValue(new Error('offline'));
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  await supervisor.setupRuntime();
  expect(supervisor.status.stage).toBe('setup_required');
  await supervisor.setupRuntime();
  expect(mocks.install).toHaveBeenCalledTimes(2);
  await supervisor.shutdown();
});

it('clean retry removes only the owned default project before reinstalling', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  mocks.install.mockRejectedValue(new Error('offline'));
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  await supervisor.setupRuntime();
  await supervisor.cleanSetupRuntime();
  expect(mocks.rm).toHaveBeenCalledOnce();
  expect(mocks.rm.mock.calls[0]?.[0]).toMatch(/[\\/]runtime[\\/]project$/);
  expect(mocks.rm.mock.calls[0]?.[1]).toEqual({ recursive: true, force: true });
  expect(mocks.promoteCaches).toHaveBeenCalledWith(expect.stringMatching(/[\\/]project$/));
  expect(mocks.install).toHaveBeenCalledTimes(2);
  await supervisor.shutdown();
});

it('clean retry refuses an environment the Electron app does not own', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  (supervisor as unknown as { runtimeProject: string }).runtimeProject = '/shared/tauri/project';
  await expect(supervisor.cleanSetupRuntime()).rejects.toThrow('unowned');
  expect(mocks.rm).not.toHaveBeenCalled();
  await supervisor.shutdown();
});

it('exposes actionable storage failures without requiring log parsing', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  mocks.install.mockRejectedValue(
    Object.assign(new Error('full'), { code: 'ENOSPC', requiredGib: 5 }),
  );
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  await supervisor.setupRuntime();
  expect(supervisor.status.setupIssue).toBe('space');
  expect(supervisor.status.setupRequiredGib).toBe(5);
  mocks.install.mockRejectedValue(Object.assign(new Error('denied'), { code: 'EACCES' }));
  await supervisor.setupRuntime();
  expect(supervisor.status.setupIssue).toBe('access');
  await supervisor.shutdown();
});

it('serializes cleanup and does not reinstall after shutdown during cleanup', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  let finish!: () => void;
  mocks.rm.mockImplementationOnce(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  const cleaning = supervisor.cleanSetupRuntime();
  await supervisor.cleanSetupRuntime();
  await supervisor.setupRuntime();
  expect(mocks.rm).toHaveBeenCalledOnce();
  expect(mocks.install).not.toHaveBeenCalled();
  expect(() => supervisor.setRuntimeLocation(null)).toThrow('runtime_location_unavailable');
  await supervisor.shutdown();
  finish();
  await cleaning;
  expect(mocks.install).not.toHaveBeenCalled();
  expect(supervisor.status.stage).toBe('idle');
});

it('publishes cleanup permission failures and allows retry', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  mocks.rm.mockRejectedValueOnce(Object.assign(new Error('runtime is locked'), { code: 'EPERM' }));
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  await supervisor.cleanSetupRuntime();
  expect(supervisor.status.setupIssue).toBe('access');
  expect(supervisor.status.message).toBe('runtime is locked');
  expect(supervisor.status.logTail).toContain('runtime is locked');
  expect(mocks.install).not.toHaveBeenCalled();
  mocks.install.mockRejectedValueOnce(new Error('offline'));
  await supervisor.setupRuntime();
  expect(mocks.install).toHaveBeenCalledOnce();
  await supervisor.shutdown();
});

it('reserves setup before asynchronous checks and cancels stale preflight', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  let finish!: (ready: boolean) => void;
  mocks.ready.mockImplementationOnce(
    () =>
      new Promise<boolean>((resolve) => {
        finish = resolve;
      }),
  );
  const first = supervisor.setupRuntime();
  await supervisor.setupRuntime();
  expect(supervisor.status.stage).toBe('installing');
  expect(() => supervisor.setRuntimeLocation(null)).toThrow('runtime_location_unavailable');
  await supervisor.shutdown();
  finish(false);
  await first;
  expect(mocks.install).not.toHaveBeenCalled();
  expect(supervisor.status.stage).toBe('idle');
});

it.each(['ready', 'compatible'] as const)(
  'offers setup instead of spawning a %s runtime with missing dependencies',
  async (kind) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('no backend');
      }),
    );
    vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
    vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
    mocks[kind].mockResolvedValue(true);
    mocks.dependencies.mockResolvedValue(false);
    const supervisor = new BackendSupervisor();
    await supervisor.start();
    expect(supervisor.status.stage).toBe('setup_required');
    expect(mocks.spawn).not.toHaveBeenCalled();
    expect(mocks.install).not.toHaveBeenCalled();
    expect(mocks.stage).not.toHaveBeenCalled();
    await supervisor.shutdown();
  },
);

it.each([true, false])(
  'preserves a selected unowned runtime (project exists: %s)',
  async (projectExists) => {
    const { resolve, join } = await import('node:path');
    const selected = resolve('/selected/VoiceStudio');
    mocks.runtimeConfig = { root: selected, owned: false };
    mocks.existingProject = projectExists;
    mocks.existingRoot = true;
    mocks.ready.mockResolvedValue(true);
    mocks.dependencies.mockResolvedValue(false);
    mocks.install.mockRejectedValue(new Error('offline'));
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('no backend');
      }),
    );
    vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
    vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
    const supervisor = new BackendSupervisor();
    await supervisor.start();
    expect(supervisor.status.stage).toBe('setup_required');
    expect(
      mocks.dependencies.mock.calls.every(([project]) => String(project).includes('selected')),
    ).toBe(true);
    expect(mocks.install).not.toHaveBeenCalled();
    await supervisor.setupRuntime();
    expect(mocks.install.mock.calls[0][1]).toBe(join('/private/voicestudio', 'runtime', 'project'));
    expect(mocks.runtimeConfig?.root).not.toBe(selected);
    expect(mocks.rm).not.toHaveBeenCalled();
    expect(mocks.stage).not.toHaveBeenCalled();
    await supervisor.shutdown();
  },
);

it('installs into a newly selected custom destination that does not yet exist', async () => {
  const { resolve, join } = await import('node:path');
  const selected = resolve('/selected/VoiceStudio');
  mocks.runtimeConfig = { root: selected, owned: false };
  mocks.install.mockRejectedValue(new Error('offline'));
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  await supervisor.setupRuntime();
  expect(mocks.install.mock.calls[0][1]).toBe(join(selected, 'project'));
  expect(mocks.runtimeConfig).toEqual({ root: selected, owned: true });
  await supervisor.shutdown();
});

it('reuses a healthy default runtime after explicit setup leaves an unowned environment', async () => {
  const { resolve, join } = await import('node:path');
  mocks.runtimeConfig = { root: resolve('/selected/VoiceStudio'), owned: false };
  mocks.existingRoot = true;
  mocks.ready.mockResolvedValue(true);
  mocks.dependencies.mockImplementation(async (project?: string) => !project?.includes('selected'));
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  expect(supervisor.status.stage).toBe('setup_required');
  const restart = vi.spyOn(supervisor, 'start').mockResolvedValue();
  await supervisor.setupRuntime();
  expect(mocks.install).not.toHaveBeenCalled();
  expect(mocks.dependencies).toHaveBeenCalledWith(
    join('/private/voicestudio', 'runtime', 'project'),
  );
  expect(restart).toHaveBeenCalledOnce();
  restart.mockRestore();
  await supervisor.shutdown();
});

// ── #2215: the packaged uv has to reach the backend ────────────────────────
//
// The backend resolves uv as OMNIVOICE_BUNDLED_UV first and `shutil.which`
// second. The packaged uv lives in resources/tools — on nobody's PATH — and a
// GUI launch inherits no shell PATH additions either, so `which` found nothing
// and every one-click sidecar installer (sidecar_install plus the IndexTTS,
// Confucius4, dots.tts and MOSS-TTS bootstraps, all five reading the same
// variable) died at preflight with "uv was not found" while the binary sat in
// the app bundle.

function stubUvOnPath(): string {
  const dir = mkdtempSync(join(tmpdir(), 'vs-uv-'));
  const uv = join(dir, process.platform === 'win32' ? 'uv.exe' : 'uv');
  writeFileSync(uv, '#!/bin/sh\n');
  chmodSync(uv, 0o700);
  vi.stubEnv('PATH', dir);
  return uv;
}

it('hands the located uv to the backend so sidecar preflight can find it', () => {
  vi.stubEnv('OMNIVOICE_BUNDLED_UV', '');
  const uv = stubUvOnPath();

  const { env } = managedBackendSpawnOptions(3900);

  expect(env.OMNIVOICE_BUNDLED_UV).toBe(uv);
});

it.runIf(process.platform !== 'win32')(
  'does not hand a non-executable uv candidate to the backend',
  () => {
    vi.stubEnv('OMNIVOICE_BUNDLED_UV', '');
    const uv = stubUvOnPath();
    chmodSync(uv, 0o600);

    const { env } = managedBackendSpawnOptions(3900);

    expect(env.OMNIVOICE_BUNDLED_UV).not.toBe(uv);
  },
);

it('never overrides a uv the user pinned themselves', () => {
  vi.stubEnv('OMNIVOICE_BUNDLED_UV', '/pinned/uv');
  stubUvOnPath();

  const { env } = managedBackendSpawnOptions(3900);

  expect(env.OMNIVOICE_BUNDLED_UV).toBe('/pinned/uv');
});

it('only ever names a uv that is really there', () => {
  // The backend gates on `if bundled and Path(bundled).is_file()`, so a blank
  // or stale value reads as "the shell tried and failed" rather than "the
  // shell had nothing to say". Whether this host has a uv at all is not the
  // point — that it never names one it cannot stand behind, is.
  vi.stubEnv('OMNIVOICE_BUNDLED_UV', '');

  const { env } = managedBackendSpawnOptions(3900);

  if (env.OMNIVOICE_BUNDLED_UV !== undefined) {
    expect(env.OMNIVOICE_BUNDLED_UV).not.toBe('');
    expect(existsSync(env.OMNIVOICE_BUNDLED_UV)).toBe(true);
  }
});

// ── #2599: LAN devices need the web UI this app version ships ──────────────
//
// The runtime project holds only the Python sources, so a backend left to
// find the web build beside itself had none and redirected LAN devices to
// their own localhost. The managed launch must point it at the packaged build.

function stubResourcesPath(path: string): void {
  const previous = Object.getOwnPropertyDescriptor(process, 'resourcesPath');
  Object.defineProperty(process, 'resourcesPath', { value: path, configurable: true });
  onTestFinished(() => {
    if (previous) Object.defineProperty(process, 'resourcesPath', previous);
    else delete (process as { resourcesPath?: string }).resourcesPath;
  });
}

it('serves LAN devices the web UI packaged with this app version', () => {
  vi.stubEnv('OMNIVOICE_FRONTEND_DIST', '');
  stubResourcesPath(join('/opt', 'VoiceStudio', 'resources'));

  const { env } = managedBackendSpawnOptions(3900);

  expect(env.OMNIVOICE_FRONTEND_DIST).toBe(
    join('/opt', 'VoiceStudio', 'resources', 'frontend', 'dist'),
  );
  expect(bundledWebUiPath()).toBe(env.OMNIVOICE_FRONTEND_DIST);
});

it('keeps a web UI directory the user pinned themselves', () => {
  vi.stubEnv('OMNIVOICE_FRONTEND_DIST', '/custom/web');
  stubResourcesPath(join('/opt', 'VoiceStudio', 'resources'));

  expect(managedBackendSpawnOptions(3900).env.OMNIVOICE_FRONTEND_DIST).toBe('/custom/web');
});

it('exposes the current process signal separately from the durable crash journal', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as {
    generation: number;
    recoverAfterChildExit: (
      gen: number,
      code: number | null,
      signal: NodeJS.Signals | null,
    ) => Promise<void>;
  };
  internal.generation = 1;
  const recovery = internal.recoverAfterChildExit(1, null, 'SIGSEGV');
  await vi.advanceTimersByTimeAsync(10500);
  await recovery;
  expect(supervisor.status.exitSignal).toBe('SIGSEGV');
  await supervisor.shutdown();
  expect(supervisor.status.exitSignal).toBeUndefined();
  vi.useRealTimers();
});

// ── #2365: Intel Macs can never resolve the runtime ─────────────────────────
//
// PyTorch ships no macOS x86_64 wheels, so offering a local install there
// burns gigabytes before a certain resolver failure. The supervisor parks
// in setup_required with an unsupported_platform issue (the setup screen
// shows remote-backend guidance instead of an install CTA), and setupRuntime
// refuses even direct IPC.

function stubIntelMac(): () => void {
  const platform = Object.getOwnPropertyDescriptor(process, 'platform');
  const arch = Object.getOwnPropertyDescriptor(process, 'arch');
  Object.defineProperty(process, 'platform', { value: 'darwin', configurable: true });
  Object.defineProperty(process, 'arch', { value: 'x64', configurable: true });
  return () => {
    if (platform) Object.defineProperty(process, 'platform', platform);
    if (arch) Object.defineProperty(process, 'arch', arch);
  };
}

it('parks Intel Macs in setup_required with an unsupported-platform issue', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  const restore = stubIntelMac();
  try {
    expect(isUnsupportedPlatform()).toBe(true);
    const supervisor = new BackendSupervisor();
    await supervisor.start();
    expect(supervisor.status.stage).toBe('setup_required');
    expect(supervisor.status.setupIssue).toBe('unsupported_platform');
    await supervisor.setupRuntime();
    expect(mocks.install).not.toHaveBeenCalled();
    await supervisor.shutdown();
  } finally {
    restore();
  }
});

it('does not gate Apple Silicon or other platforms', () => {
  expect(isUnsupportedPlatform('darwin', 'arm64')).toBe(false);
  expect(isUnsupportedPlatform('win32', 'x64')).toBe(false);
  expect(isUnsupportedPlatform('linux', 'x64')).toBe(false);
});

it('tells Apple Silicon users running the Intel build to install the arm64 build (#2598)', async () => {
  expect(platformSetupIssue('darwin', 'x64', true)).toBe('wrong_architecture');
  expect(platformSetupIssue('darwin', 'x64', false)).toBe('unsupported_platform');
  expect(platformSetupIssue('darwin', 'arm64', false)).toBeUndefined();
  // Windows on ARM emulating the x64 build is a different, supported path.
  expect(platformSetupIssue('win32', 'x64', true)).toBeUndefined();

  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no backend');
    }),
  );
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  const restore = stubIntelMac();
  const electronApp = app as { runningUnderARM64Translation?: boolean };
  electronApp.runningUnderARM64Translation = true;
  try {
    const supervisor = new BackendSupervisor();
    await supervisor.start();
    expect(supervisor.status.stage).toBe('setup_required');
    expect(supervisor.status.setupIssue).toBe('wrong_architecture');
    await supervisor.setupRuntime();
    expect(mocks.install).not.toHaveBeenCalled();
    await supervisor.shutdown();
  } finally {
    delete electronApp.runningUnderARM64Translation;
    restore();
  }
});
