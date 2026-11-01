// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { join, resolve } from 'node:path';
const state = vi.hoisted(() => ({ installed: true, packaged: false, ready: true, checkout: true, selected: null as string | null }));
const validateSelected = vi.hoisted(() => vi.fn(async (pythonPath: string, _cwd: string) => ({ ok: true, pythonPath })));
vi.mock('./runtime-python-selection', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./runtime-python-selection')>()),
  selectedPythonPath: () => state.selected,
  validatePythonRuntime: validateSelected,
}));
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
    (path === join(resolve('/repo'), 'pyproject.toml') && state.checkout) ||
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
  loadBlockMessage,
  resolveSpawnPlan,
  managedBackendSpawnOptions,
  setBackendOnlyEnv,
  smartAppControlFromRegistry,
  spawnFailureMessage,
  startupTimeoutMessage,
} from './backend';
afterEach(() => {
  state.installed = true;
  state.ready = true;
  state.packaged = false;
  state.checkout = true;
  state.selected = null;
  validateSelected.mockClear();
  vi.unstubAllEnvs();
});
it('runs a selected interpreter against bundled resources, never the absent managed project', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  state.packaged = true;
  state.selected = '/existing/bin/python';
  const original = process.resourcesPath;
  Object.defineProperty(process, 'resourcesPath', { configurable: true, value: '/packaged/resources' });
  try {
    const plan = await resolveSpawnPlan(3912, '/missing/managed/project');
    expect(plan).toMatchObject({ cwd: '/packaged/resources' });
    expect(validateSelected).toHaveBeenCalledWith('/existing/bin/python', '/packaged/resources');
  } finally {
    Object.defineProperty(process, 'resourcesPath', { configurable: true, value: original });
  }
});
it.each(['darwin', 'win32', 'linux'] as const)(
  'leaves China downloads eligible for automatic failover on %s',
  (platform) => {
    vi.stubEnv('HF_ENDPOINT', undefined);
    expect(managedBackendSpawnOptions(3900, 'china', platform).env.HF_ENDPOINT).toBeUndefined();
    vi.stubEnv('HF_ENDPOINT', 'https://custom.example');
    expect(managedBackendSpawnOptions(3900, 'china', platform).env.HF_ENDPOINT).toBe(
      'https://custom.example',
    );
  },
);
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

it('never gives an installed app the source-checkout setup command (#3480)', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  state.packaged = true;
  state.installed = false;
  state.ready = false;
  const plan = await resolveSpawnPlan(3900, '/managed/runtime/project');
  expect(plan).not.toHaveProperty('error');
  expect(JSON.stringify(plan)).not.toContain('setup:api');
});

it('tells a source checkout to press Retry after setup, since Retry re-checks the environment', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  state.ready = false;
  const plan = await resolveSpawnPlan(3900);
  if (!('error' in plan)) throw new Error('expected a setup error');
  expect(plan.error).toContain('then press Retry.');
});

it('names the folder to open and refuses setup advice outside a checkout (#2988)', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  state.installed = false;
  state.checkout = false;
  const plan = await resolveSpawnPlan(3900);
  if (!('error' in plan)) throw new Error('expected a setup error');
  expect(plan.error).toContain('not a VoiceStudio source checkout');
  expect(plan.error).toContain('release installer');
});

it('sends an Intel Mac checkout to a remote backend instead of setup:api (#3584)', async () => {
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  state.ready = false;
  const plan = await resolveSpawnPlan(3900, undefined, 'unsupported_platform');
  if (!('error' in plan)) throw new Error('expected a host error');
  expect(plan.setupIssue).toBe('unsupported_platform');
  expect(plan.error).toContain('backend on another computer');
  expect(plan.error).not.toContain('incomplete');

  const rosetta = await resolveSpawnPlan(3900, undefined, 'wrong_architecture');
  if (!('error' in rosetta)) throw new Error('expected a host error');
  expect(rosetta.error).toContain('arm64');
  expect(rosetta).not.toHaveProperty('setupIssue');

  // An explicit backend command still wins on any host.
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '["custom-python"]');
  expect(await resolveSpawnPlan(3900, undefined, 'unsupported_platform')).toHaveProperty('argv');
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

it('names the refused program without recommending runtime repair', () => {
  const python = String.raw`C:\Users\u\VoiceStudio\.venv\Scripts\python.exe`;
  const message = spawnFailureMessage(
    python,
    Object.assign(new Error('spawn UNKNOWN'), { code: 'UNKNOWN' }),
  );
  expect(message).toContain(python);
  expect(message).toContain('spawn UNKNOWN');
  expect(message).not.toContain('repair the local runtime');
});

it('reads Smart App Control from the registry output', () => {
  const reg = (value: string) =>
    `\r\nHKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy\r\n    VerifiedAndReputablePolicyState    REG_DWORD    ${value}\r\n`;
  expect(smartAppControlFromRegistry(reg('0x1'))).toBe('on');
  expect(smartAppControlFromRegistry(reg('0x2'))).toBe('evaluation');
  expect(smartAppControlFromRegistry(reg('0x0'))).toBe('off');
  expect(smartAppControlFromRegistry(reg('0x9'))).toBe('unknown');
  expect(
    smartAppControlFromRegistry('ERROR: The system was unable to find the specified registry key'),
  ).toBe('unknown');
});

it('names Smart App Control when it blocked the runtime (#3224)', () => {
  const python = String.raw`C:\Users\u\VoiceStudio\.venv\Scripts\python.exe`;
  const blocked = Object.assign(new Error('spawn UNKNOWN'), { code: 'UNKNOWN' });
  for (const smartAppControl of ['on', 'evaluation'] as const) {
    const message = spawnFailureMessage(python, blocked, { smartAppControl });
    expect(message).toContain(python);
    expect(message).toContain('spawn UNKNOWN');
    expect(message).toContain('Windows Smart App Control blocked it');
    expect(message).not.toContain('repair the local runtime');
  }
  // Off or unreadable: keep the OS refusal without recommending reinstallation.
  for (const smartAppControl of ['off', 'unknown'] as const) {
    expect(spawnFailureMessage(python, blocked, { smartAppControl })).not.toContain(
      'repair the local runtime',
    );
  }
  // Only the bare Windows UNKNOWN is attributed to it, never a missing file.
  const missing = Object.assign(new Error('spawn ENOENT'), { code: 'ENOENT' });
  expect(spawnFailureMessage(python, missing)).toContain('repair the local runtime');
  expect(spawnFailureMessage(python, missing, { smartAppControl: 'on' })).not.toContain(
    'Smart App Control',
  );
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
  ).toContain(`${budget} Last output: INFO: loading model`);
  expect(startupTimeoutMessage(3900, 300_000, { owned: true })).toContain(
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

it('hands backend-only capabilities to the managed backend alone, never to process.env', () => {
  setBackendOnlyEnv({
    VOICESTUDIO_ACCOUNT_BRIDGE_URL: 'http://127.0.0.1:1',
    VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN: 'cap',
  });
  try {
    expect(managedBackendSpawnOptions(3900).env).toMatchObject({
      VOICESTUDIO_ACCOUNT_BRIDGE_URL: 'http://127.0.0.1:1',
      VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN: 'cap',
    });
    expect(process.env.VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN).toBeUndefined();
  } finally {
    setBackendOnlyEnv({
      VOICESTUDIO_ACCOUNT_BRIDGE_URL: undefined,
      VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN: undefined,
    });
  }
  expect(managedBackendSpawnOptions(3900).env.VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN).toBeUndefined();
});

it('tells the backend which commit the app was built from (#3293)', () => {
  vi.stubEnv('OMNIVOICE_BUILD_COMMIT', undefined);
  expect(managedBackendSpawnOptions(3900).env.OMNIVOICE_BUILD_COMMIT).toBe('000000000');
  vi.stubEnv('OMNIVOICE_BUILD_COMMIT', 'abcdef123');
  expect(managedBackendSpawnOptions(3900).env.OMNIVOICE_BUILD_COMMIT).toBe('abcdef123');
});

it('names Smart App Control when it refused a runtime DLL, and other blockers otherwise (#3209)', () => {
  const last =
    'ImportError: DLL load failed while importing _sqlite3: An Application Control policy has blocked this file.';
  expect(loadBlockMessage('exit code 1', last, 'application_control', 'on')).toBe(
    "Backend exited (exit code 1): Windows refused to load one of the engine's files. " +
      `Windows Smart App Control blocked it. Last output: ${last}`,
  );
  expect(loadBlockMessage('exit code 1', last, 'application_control', 'off')).toContain(
    'A Windows application control policy blocked it.',
  );
  expect(loadBlockMessage('exit code 1', '', 'group_policy', 'on')).toContain(
    'A Windows application control policy blocked it.',
  );
  expect(loadBlockMessage('exit code 1', '', 'antivirus')).toBe(
    "Backend exited (exit code 1): Windows refused to load one of the engine's files. Your antivirus blocked it.",
  );
});
