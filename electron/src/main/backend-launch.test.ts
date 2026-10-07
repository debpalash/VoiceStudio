// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { join, resolve } from 'node:path';
const state = vi.hoisted(() => ({ installed: true, packaged: false, ready: true }));
vi.mock('electron', () => ({
  app: {
    get isPackaged() {
      return state.packaged;
    },
    getAppPath: () => resolve('/repo/electron'),
    getPath: () => '/user-data',
  },
}));
vi.mock('node:fs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:fs')>()),
  existsSync: (path: string) =>
    path === join(resolve('/repo'), 'backend', 'main.py') ||
    path === join(resolve('/repo'), 'pyproject.toml') ||
    (path.includes('.venv') ? state.installed : path.includes('uv')),
  statSync: () => ({ isFile: () => true, size: 1 }),
  accessSync: () => undefined,
}));
vi.mock('./runtime-project', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./runtime-project')>()),
  runtimeDependenciesReady: vi.fn(async (_root: string, onFailure?: (detail: string) => void) => {
    if (!state.ready) onFailure?.("ModuleNotFoundError: No module named 'sentencepiece'");
    return state.ready;
  }),
}));
vi.mock('./legacy-storage', () => ({
  legacyStorageEnv: () => ({
    OMNIVOICE_DATA_DIR: '/legacy/data',
    OMNIVOICE_CACHE_DIR: '/legacy/models',
  }),
}));
import {
  devRendererPort,
  resolveSpawnPlan,
  managedBackendSpawnOptions,
  spawnFailureMessage,
  startupTimeoutMessage,
} from './backend';
afterEach(() => {
  state.installed = true;
  state.ready = true;
  state.packaged = false;
  vi.unstubAllEnvs();
});
it('launches the prepared source interpreter without syncing or downloading dependencies', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  const plan = await resolveSpawnPlan(3912);
  expect(plan).toHaveProperty('argv');
  if ('error' in plan) throw new Error(plan.error);
  expect(plan.argv[0]).toBe(
    join(
      resolve('/repo'),
      '.venv',
      process.platform === 'win32' ? 'Scripts' : 'bin',
      process.platform === 'win32' ? 'python.exe' : 'python',
    ),
  );
  expect(plan.argv.slice(1, 3)).toEqual(['-m', 'uvicorn']);
  expect(plan.argv.slice(-2)).toEqual(['--port', '3912']);
});
it('requires explicit source setup when no environment exists, even with uv installed', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  state.installed = false;
  expect(await resolveSpawnPlan(3900)).toEqual({
    error: expect.stringContaining('bun run setup:api'),
  });
});
it('preserves an explicitly configured command', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '["custom-python", "-m", "uvicorn"]');
  expect(await resolveSpawnPlan(3900)).toHaveProperty('argv', ['custom-python', '-m', 'uvicorn']);
});

it('requires setup when an interpreter exists but required imports fail', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  state.ready = false;
  expect(await resolveSpawnPlan(3900)).toEqual({
    error: expect.stringContaining('bun run setup:api'),
  });
});

it('names the failing import or the absent interpreter instead of one vague message (#2555)', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  state.ready = false;
  const incomplete = await resolveSpawnPlan(3900);
  if (!('error' in incomplete)) throw new Error('expected a setup error');
  expect(incomplete.error).toContain(
    "is incomplete: ModuleNotFoundError: No module named 'sentencepiece'.",
  );

  state.installed = false;
  const missing = await resolveSpawnPlan(3900);
  if (!('error' in missing)) throw new Error('expected a setup error');
  expect(missing.error).toContain('is missing (no .venv');
  expect(missing.error).not.toContain('incomplete');
});

it('keeps native fault frames when the production log ring overflows', async () => {
  const { BackendSupervisor } = await import('./backend');
  const supervisor = new BackendSupervisor();
  const internals = supervisor as unknown as {
    pushLog(stream: 'err', line: string): void;
    log: string[];
    crashes: import('./crash-journal').CrashJournal;
  };
  const output = vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    for (const line of [
      'Fatal Python error: Segmentation fault',
      'Thread 0x111 (most recent call first):',
      ...Array(300).fill('  File "threading.py", line 10 in wait'),
      'Current thread 0x222 (most recent call first):',
      '  File "native_fault.py", line 42 in load',
      ...Array(300).fill('  File "runpy.py", line 198 in _run_module_as_main'),
      `Extension modules: ${'torch._C, '.repeat(600)}`,
    ])
      internals.pushLog('err', line);
    expect(internals.log.length).toBeLessThanOrEqual(200);
    expect(internals.log.join('\n')).not.toContain('native_fault.py');
    internals.crashes.record(null, 'SIGSEGV', 100, internals.log);
    const kept = internals.crashes.latest()!.logTail.join('\n');
    expect(kept).toContain('Fatal Python error: Segmentation fault');
    expect(kept).toContain('native_fault.py');
  } finally {
    output.mockRestore();
  }
});

it('passes legacy storage to packaged backends while preserving explicit overrides', () => {
  state.packaged = true;
  vi.stubEnv('OMNIVOICE_DATA_DIR', undefined);
  vi.stubEnv('OMNIVOICE_CACHE_DIR', undefined);
  expect(managedBackendSpawnOptions(3900).env).toMatchObject({
    OMNIVOICE_DATA_DIR: '/legacy/data',
    OMNIVOICE_CACHE_DIR: '/legacy/models',
  });
  vi.stubEnv('OMNIVOICE_DATA_DIR', '/chosen/data');
  vi.stubEnv('OMNIVOICE_CACHE_DIR', '/chosen/models');
  expect(managedBackendSpawnOptions(3900).env).toMatchObject({
    OMNIVOICE_DATA_DIR: '/chosen/data',
    OMNIVOICE_CACHE_DIR: '/chosen/models',
  });
  state.packaged = false;
  vi.stubEnv('OMNIVOICE_DATA_DIR', undefined);
  vi.stubEnv('OMNIVOICE_CACHE_DIR', undefined);
  expect(managedBackendSpawnOptions(3900).env.OMNIVOICE_DATA_DIR).toBeUndefined();
});

it('names the program and runtime repair when the OS rejects a spawn', () => {
  const python = String.raw`C:\Users\u\VoiceStudio\.venv\Scripts\python.exe`;
  const message = spawnFailureMessage(
    python,
    Object.assign(new Error('spawn UNKNOWN'), { code: 'UNKNOWN' }),
  );
  expect(message).toContain(python);
  expect(message).toContain('spawn UNKNOWN');
  expect(message).toContain('repair the local runtime');
});

it('points a custom backend command at its own executable', () => {
  const message = spawnFailureMessage(
    '/opt/custom/python',
    Object.assign(new Error('spawn ENOENT'), { code: 'ENOENT' }),
    { runtimeOwned: false },
  );
  expect(message).toContain('can be launched');
  expect(message).not.toContain('local runtime');
});

it('keeps a plain spawn failure message free of install advice', () => {
  expect(spawnFailureMessage('uv', new Error('Runtime setup exited with code 1'))).toBe(
    'Could not start uv: Runtime setup exited with code 1',
  );
});

it('says what the launch did in a startup-budget failure', () => {
  const budget = 'Backend did not answer on port 3900 within 300 s (OMNIVOICE_STARTUP_BUDGET_S).';
  expect(
    startupTimeoutMessage(3900, 300_000, { owned: true, lastOutput: 'INFO: loading model' }),
  ).toBe(`${budget} Last output: INFO: loading model`);
  expect(startupTimeoutMessage(3900, 300_000, { owned: true })).toBe(
    `${budget} It printed no output.`,
  );
  expect(startupTimeoutMessage(3900, 300_000, { owned: false })).toBe(
    `${budget} Nothing was spawned for this attempt.`,
  );
});

type TimeoutInternals = {
  generation: number;
  stage: string;
  log: string[];
  childLog: string[];
  child: unknown;
  probe(): Promise<boolean>;
  killChild(): Promise<void>;
  waitUntilReady(gen: number, budgetMs: number): Promise<void>;
};

it('keeps the startup output when the timeout teardown logs shutdown chatter', async () => {
  const { BackendSupervisor } = await import('./backend');
  const supervisor = new BackendSupervisor();
  const internals = supervisor as unknown as TimeoutInternals;
  internals.stage = 'starting';
  internals.child = {};
  internals.childLog.push('INFO: loading model weights');
  internals.probe = async () => false;
  internals.killChild = async () => {
    internals.child = null;
    internals.childLog.push('INFO: uvicorn shutting down');
  };

  await internals.waitUntilReady(internals.generation, 0);

  expect(supervisor.status.stage).toBe('failed');
  expect(supervisor.status.message).toContain('Last output: INFO: loading model weights');
  expect(supervisor.status.message).not.toContain('shutting down');
});

it('ignores supervisor log lines when this launch printed nothing', async () => {
  const { BackendSupervisor } = await import('./backend');
  const supervisor = new BackendSupervisor();
  const internals = supervisor as unknown as TimeoutInternals;
  internals.stage = 'starting';
  internals.child = {};
  internals.log.push('Reusing compatible Tauri runtime: /old/setup');
  internals.probe = async () => false;
  internals.killChild = async () => {
    internals.child = null;
  };

  await internals.waitUntilReady(internals.generation, 0);

  expect(supervisor.status.stage).toBe('failed');
  expect(supervisor.status.message).toContain('It printed no output.');
  expect(supervisor.status.message).not.toContain('Reusing compatible');
});

it('tells the backend the dev renderer port so Sharing reports the real UI port', () => {
  expect(devRendererPort('http://localhost:3902')).toBe('3902');
  expect(devRendererPort('app://voicestudio/index.html')).toBeNull();
  expect(devRendererPort(undefined)).toBeNull();
  vi.stubEnv('OMNIVOICE_UI_PORT', '');
  vi.stubEnv('VOICESTUDIO_UI_PORT', '');
  vi.stubEnv('ELECTRON_RENDERER_URL', 'http://localhost:4102');
  expect(managedBackendSpawnOptions(3900).env.OMNIVOICE_UI_PORT).toBe('4102');
  vi.stubEnv('OMNIVOICE_UI_PORT', '4200');
  expect(managedBackendSpawnOptions(3900).env.OMNIVOICE_UI_PORT).toBe('4200');
  vi.stubEnv('OMNIVOICE_UI_PORT', '');
  vi.stubEnv('ELECTRON_RENDERER_URL', '');
  expect(managedBackendSpawnOptions(3900).env.OMNIVOICE_UI_PORT).toBe('');
});
