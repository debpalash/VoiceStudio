import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { TorchChoice } from './runtime-project';

const fixture = vi.hoisted(() => ({
  profile: '',
  packaged: true,
  distribution: true,
  failWrite: false,
  failRename: false,
  ready: vi.fn(async (_bundle?: string, _project?: string, _choice?: TorchChoice) => false),
  compatible: vi.fn(async (_bundle?: string, _project?: string, _choice?: TorchChoice) => false),
  dependencies: vi.fn(async () => true),
  install: vi.fn(),
  hosts: vi.fn(async () => new Map<string, string>()),
  spawn: vi.fn(),
  kill: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('electron', () => ({
  app: {
    get isPackaged() {
      return fixture.packaged;
    },
    getPath: () => fixture.profile,
    getAppPath: () => join(fixture.profile, 'electron'),
  },
}));
vi.mock('./distribution', () => ({ runtimeSetupSupported: () => fixture.distribution }));
vi.mock('node:fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('node:fs')>();
  return {
    ...actual,
    renameSync: (...args: Parameters<typeof actual.renameSync>) => {
      if (fixture.failRename) throw Object.assign(new Error('rename denied'), { code: 'EPERM' });
      return actual.renameSync(...args);
    },
    writeFileSync: (...args: Parameters<typeof actual.writeFileSync>) => {
      if (fixture.failWrite && String(args[0]).includes('runtime-preferences.json.')) {
        actual.writeFileSync(args[0], '{partial');
        throw Object.assign(new Error('disk full'), { code: 'ENOSPC' });
      }
      return actual.writeFileSync(...args);
    },
  };
});
vi.mock('node:fs/promises', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:fs/promises')>()),
  rm: fixture.remove,
}));
vi.mock('node:child_process', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:child_process')>()),
  spawn: fixture.spawn,
  spawnSync: fixture.kill,
}));
vi.mock('./backend-port', () => ({ availableBackendPort: async (port: number) => port }));
vi.mock('./runtime-project', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./runtime-project')>()),
  runtimeReady: fixture.ready,
  runtimeCompatible: fixture.compatible,
  runtimeDependenciesReady: fixture.dependencies,
  runtimeInstallInterrupted: async () => false,
  stageRuntimeSources: async () => {},
  installRuntime: fixture.install,
  runtimeDownloadHosts: fixture.hosts,
}));

import { BackendSupervisor, resolveSpawnPlan } from './backend';

const supervisors: BackendSupervisor[] = [];

it('SDK ownership refuses Clean and Retry when the exact default root is explicitly unowned', async () => {
  const root = join(fixture.profile, 'runtime');
  writeFileSync(
    join(fixture.profile, 'runtime-location.json'),
    JSON.stringify({ root, owned: false }),
  );
  const instance = supervisor();
  Object.assign(instance, { stage: 'setup_required', runtimeProject: join(root, 'project') });
  const failure = await instance.cleanSetupRuntime().then(
    () => null,
    (error: Error) => error,
  );
  expect(fixture.remove).not.toHaveBeenCalled();
  expect(fixture.install).not.toHaveBeenCalled();
  expect(failure).toMatchObject({
    message: 'Runtime cleanup refused for an unowned environment',
  });
});

it('SDK ownership prevents a ready child changing Torch when the exact default root is explicitly unowned', async () => {
  const root = join(fixture.profile, 'runtime');
  writeFileSync(
    join(fixture.profile, 'runtime-location.json'),
    JSON.stringify({ root, owned: false }),
  );
  seedPreferences({ region: 'global', torchPreference: 'cpu' });
  const before = readFileSync(join(fixture.profile, 'runtime-preferences.json'), 'utf8');
  const instance = supervisor();
  const child = ownedReady(instance);
  const failure = await instance.beginRuntimeTorchSetup('rocm').then(
    () => null,
    (error: Error) => error,
  );
  expect(readFileSync(join(fixture.profile, 'runtime-preferences.json'), 'utf8')).toBe(before);
  expect(instance.status.runtimeTorchSwitchable).toBe(false);
  expect(failure).toMatchObject({ message: 'runtime_torch_not_owned' });
  expect((instance as unknown as { child: unknown }).child).toBe(child);
  expect(fixture.kill).not.toHaveBeenCalled();
  expect(fixture.remove).not.toHaveBeenCalled();
  expect(fixture.install).not.toHaveBeenCalled();
});

it.each([
  { location: 'none', project: 'default', owned: false, managed: true },
  { location: 'custom', project: 'custom', owned: true, managed: true },
  { location: 'custom', project: 'custom', owned: false, managed: false },
  { location: 'default', project: 'default', owned: false, managed: false },
  { location: 'custom', project: 'other', owned: true, managed: false },
] as const)(
  'SDK isolation binds plan ownership for location=$location project=$project owned=$owned',
  async ({ location, project, owned, managed }) => {
    const roots = {
      default: join(fixture.profile, 'runtime'),
      custom: join(fixture.profile, 'chosen'),
      other: join(fixture.profile, 'external'),
    };
    if (location !== 'none')
      writeFileSync(
        join(fixture.profile, 'runtime-location.json'),
        JSON.stringify({ root: roots[location], owned }),
      );
    const plan = await resolveSpawnPlan(3900, join(roots[project], 'project'));
    expect(plan).toHaveProperty('argv');
    expect(('managedRuntime' in plan && plan.managedRuntime) === true).toBe(managed);
  },
);

it.each([true, false])(
  'SDK isolation reaches the real supervisor spawn for owned=%s',
  async (owned) => {
    const root = join(fixture.profile, 'chosen');
    writeFileSync(join(fixture.profile, 'runtime-location.json'), JSON.stringify({ root, owned }));
    seedPreferences({ region: 'global', torchPreference: 'rocm' });
    vi.stubEnv('HIP_PATH', 'parent-sdk');
    fixture.ready.mockResolvedValue(true);
    const instance = supervisor();
    await instance.start();
    const choice = { variant: 'rocm', explicit: true };
    expect(fixture.dependencies).toHaveBeenCalledWith(
      join(root, 'project'),
      undefined,
      choice,
      owned,
    );
    expect(fixture.spawn).toHaveBeenCalledOnce();
    const env = fixture.spawn.mock.calls[0][2].env;
    expect(env.HIP_PATH).toBe(owned ? undefined : 'parent-sdk');
    expect(process.env.HIP_PATH).toBe('parent-sdk');
  },
);

it.each([false, true])(
  'SDK isolation sanitizes final installer overrides after owned fallback, timed=%s',
  async (timed) => {
    const external = join(fixture.profile, 'external');
    mkdirSync(external);
    writeFileSync(join(external, 'personal.txt'), 'retain');
    writeFileSync(
      join(fixture.profile, 'runtime-location.json'),
      JSON.stringify({ root: external, owned: false }),
    );
    seedPreferences({ region: 'global', torchPreference: 'rocm' });
    vi.stubEnv('HIP_PATH', 'parent-sdk');
    const instance = supervisor();
    await instance.start();
    vi.spyOn(instance, 'start').mockResolvedValue(undefined);
    fixture.install.mockImplementation(async (_bundle, project, _uv, run) => {
      await run(
        'fixture-python',
        ['-c', 'pass'],
        project,
        {
          HIP_PATH: 'late-sdk',
          ROCM_HOME: 'late-sdk',
          ROCM_PATH: 'late-sdk',
          HIP_VISIBLE_DEVICES: '0',
          HSA_OVERRIDE_GFX_VERSION: 'unqualified',
          MIOPEN_DISABLE_CACHE: '1',
        },
        timed ? { timeoutMs: 5000 } : undefined,
      );
    });
    await instance.setupRuntime();
    expect(fixture.install).toHaveBeenCalledOnce();
    const env = fixture.spawn.mock.calls[0][2].env;
    expect(
      Object.keys(env).filter((key) =>
        ['HIP_PATH', 'ROCM_HOME', 'ROCM_PATH'].includes(key.toUpperCase()),
      ),
    ).toEqual([]);
    expect(env).toMatchObject({
      HIP_VISIBLE_DEVICES: '0',
      HSA_OVERRIDE_GFX_VERSION: 'unqualified',
      MIOPEN_DISABLE_CACHE: '1',
    });
    expect(
      JSON.parse(readFileSync(join(fixture.profile, 'runtime-location.json'), 'utf8')),
    ).toMatchObject({ root: join(fixture.profile, 'runtime'), owned: true });
    expect(readFileSync(join(external, 'personal.txt'), 'utf8')).toBe('retain');
    expect(process.env.HIP_PATH).toBe('parent-sdk');
  },
);

const resourcesPathDescriptor = Object.getOwnPropertyDescriptor(process, 'resourcesPath');
function supervisor(): BackendSupervisor {
  const instance = new BackendSupervisor();
  supervisors.push(instance);
  return instance;
}
function preferences(): Record<string, unknown> {
  return JSON.parse(readFileSync(join(fixture.profile, 'runtime-preferences.json'), 'utf8'));
}
function seedPreferences(value: unknown): void {
  writeFileSync(join(fixture.profile, 'runtime-preferences.json'), JSON.stringify(value));
}
function ownedReady(instance: BackendSupervisor): EventEmitter {
  const child = Object.assign(new EventEmitter(), {
    pid: 4242,
    exitCode: null as number | null,
    signalCode: null,
    killed: false,
  });
  fixture.kill.mockImplementation(() => {
    child.exitCode = 0;
    child.emit('exit', 0, null);
    return { status: 0 };
  });
  if (process.platform !== 'win32') {
    vi.spyOn(process, 'kill').mockImplementation((pid, signal) => {
      expect(pid).toBe(-child.pid);
      expect(signal).toBe('SIGTERM');
      child.exitCode = 0;
      child.emit('exit', 0, null);
      return true;
    });
  }
  Object.assign(instance, {
    stage: 'ready',
    managed: true,
    runtimeProject: join(fixture.profile, 'runtime', 'project'),
    child,
  });
  return child;
}

beforeEach(() => {
  vi.spyOn(process, 'platform', 'get').mockReturnValue('win32');
  vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
  fixture.profile = mkdtempSync(join(tmpdir(), 'voicestudio-torch-preference-'));
  Object.defineProperty(process, 'resourcesPath', { value: fixture.profile, configurable: true });
  fixture.packaged = true;
  fixture.distribution = true;
  fixture.failWrite = false;
  fixture.failRename = false;
  fixture.ready.mockReset().mockResolvedValue(false);
  fixture.compatible.mockReset().mockResolvedValue(false);
  fixture.dependencies.mockReset().mockResolvedValue(true);
  fixture.install.mockReset().mockResolvedValue(undefined);
  fixture.hosts.mockClear();
  fixture.remove.mockClear();
  fixture.spawn.mockReset().mockImplementation(() => {
    const child = Object.assign(new EventEmitter(), {
      stdin: null,
      stdout: null,
      stderr: null,
      stdio: [],
      exitCode: null,
    });
    queueMicrotask(() => child.emit('close', 0));
    return child;
  });
  fixture.kill.mockReset();
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  vi.stubEnv('LOCALAPPDATA', fixture.profile);
  vi.stubEnv('APPDATA', fixture.profile);
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no listener');
    }),
  );
});

afterEach(async () => {
  for (const instance of supervisors.splice(0)) await instance.shutdown();
  rmSync(fixture.profile, { recursive: true, force: true });
  if (resourcesPathDescriptor)
    Object.defineProperty(process, 'resourcesPath', resourcesPathDescriptor);
  else Reflect.deleteProperty(process, 'resourcesPath');
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it.each(
  (['win32', 'linux'] as const).flatMap((platform) =>
    (['auto', 'cpu', 'cuda', 'rocm'] as const).map((preference) => ({ platform, preference })),
  ),
)(
  'saves $platform $preference without provisioning and preserves region across reloads and writes',
  async ({ platform, preference }) => {
    vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
    const instance = supervisor();
    await instance.start();
    instance.setRuntimeRegion('china');
    expect(instance.setRuntimeTorchPreference(preference)).toBe(preference);
    expect(preferences()).toEqual({
      region: 'china',
      torchPreference: preference,
      torchSetupPending: true,
    });
    instance.setRuntimeRegion('global');
    expect(preferences()).toEqual({
      region: 'global',
      torchPreference: preference,
      torchSetupPending: true,
    });
    expect(supervisor().status.runtimeTorchPreference).toBe(preference);
    expect(fixture.install).not.toHaveBeenCalled();
    expect(fixture.spawn).not.toHaveBeenCalled();
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('');
  },
);

it.each(['write', 'rename'])(
  'keeps both persisted fields and visible state when an atomic %s fails',
  async (failure) => {
    seedPreferences({ region: 'china', torchPreference: 'cpu' });
    const instance = supervisor();
    await instance.start();
    fixture.failWrite = failure === 'write';
    fixture.failRename = failure === 'rename';
    expect(() => instance.setRuntimeTorchPreference('rocm')).toThrow();
    expect(() => instance.setRuntimeRegion('global')).toThrow();
    expect(preferences()).toEqual({ region: 'china', torchPreference: 'cpu' });
    expect(instance.status.runtimeTorchPreference).toBe('cpu');
    expect(instance.status.runtimeRegion).toBe('china');
    expect(readdirSync(fixture.profile).filter((name) => name.endsWith('.tmp'))).toEqual([]);
  },
);

it.each([
  ['cpu', 'cpu'],
  ['cuda', 'cuda'],
  ['default', 'cuda'],
  [' ROCm ', 'rocm'],
] as const)('treats inherited %s as the authoritative %s pin', async (inherited, preference) => {
  seedPreferences({ region: 'china', torchPreference: 'cpu' });
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', inherited);
  const instance = supervisor();
  await instance.start();
  expect(instance.status).toMatchObject({
    runtimeTorchPreference: preference,
    runtimeTorchPinned: true,
  });
  expect(() => instance.setRuntimeTorchPreference('rocm')).toThrow('runtime_torch_pinned');
  expect(preferences()).toEqual({ region: 'china', torchPreference: 'cpu' });
});

it.each(['auto', 'bogus', ''])(
  'allows a saved choice over the unpinned inherited value %s',
  async (inherited) => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', inherited);
    const instance = supervisor();
    await instance.start();
    expect(instance.status.runtimeTorchPinned).toBe(false);
    instance.setRuntimeTorchPreference('rocm');
    expect(instance.status.runtimeTorchPreference).toBe('rocm');
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe(inherited);
  },
);

it.each(['win32', 'linux'] as const)(
  'keeps a pending %s choice explicit across restart even when an older runtime was installed',
  async (platform) => {
    vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
    const instance = supervisor();
    await instance.start();
    instance.setRuntimeTorchPreference('rocm');
    mkdirSync(join(fixture.profile, 'runtime', 'project', '.venv'), { recursive: true });
    const restarted = supervisor();
    await restarted.start();
    expect(restarted.status.stage).toBe('setup_required');
    expect(restarted.status.runtimeRepairFailed).toBeUndefined();
    expect(fixture.install).not.toHaveBeenCalled();
    expect(fixture.remove).not.toHaveBeenCalled();
    expect(preferences().torchSetupPending).toBe(true);
  },
);

it('retains the selected variant, retry state and scoped storage after installation failure', async () => {
  const root = join(fixture.profile, 'chosen-runtime');
  const location = { root, owned: true, scoped: true };
  writeFileSync(join(fixture.profile, 'runtime-location.json'), JSON.stringify(location));
  const instance = supervisor();
  await instance.start();
  instance.setRuntimeRegion('global');
  instance.setRuntimeTorchPreference('rocm');
  const before = instance.status;
  fixture.install.mockRejectedValueOnce(new Error('native verification failed'));
  await instance.setupRuntime();
  expect(instance.status).toMatchObject({
    stage: 'setup_required',
    runtimeTorchPreference: 'rocm',
    runtimePath: before.runtimePath,
    runtimeStorage: before.runtimeStorage,
    message: 'native verification failed',
  });
  expect(preferences()).toEqual({
    region: 'global',
    torchPreference: 'rocm',
    torchSetupPending: true,
  });
  expect(JSON.parse(readFileSync(join(fixture.profile, 'runtime-location.json'), 'utf8'))).toEqual(
    location,
  );
  expect(fixture.remove).not.toHaveBeenCalled();
  expect(fixture.spawn).not.toHaveBeenCalled();
});

it('only retires the pending preference after an explicit successful setup', async () => {
  const instance = supervisor();
  await instance.start();
  instance.setRuntimeTorchPreference('cpu');
  fixture.install.mockImplementationOnce(async () => {
    fixture.ready.mockResolvedValue(true);
  });
  await instance.setupRuntime();
  expect(fixture.install).toHaveBeenCalledOnce();
  expect(preferences()).toEqual({ region: 'auto', torchPreference: 'cpu' });
});

it.each(['installation', 'cleaningRuntime'])(
  'rejects preference changes while %s owns runtime files',
  async (operation) => {
    const instance = supervisor();
    await instance.start();
    Object.assign(instance, {
      [operation]: operation === 'installation' ? new AbortController() : true,
    });
    expect(() => instance.setRuntimeTorchPreference('rocm')).toThrow('runtime_torch_unavailable');
    expect(fixture.install).not.toHaveBeenCalled();
  },
);

it.each([
  null,
  { region: 'bogus', torchPreference: 'default', torchSetupPending: 'true' },
  ['cpu'],
])('ignores malformed saved preferences %j', (saved) => {
  seedPreferences(saved);
  expect(supervisor().status).toMatchObject({
    runtimeRegion: 'auto',
    runtimeTorchPreference: 'auto',
  });
});

it('loads each valid saved field independently', () => {
  seedPreferences({ region: 'china', torchPreference: 'bogus' });
  expect(supervisor().status).toMatchObject({
    runtimeRegion: 'china',
    runtimeTorchPreference: 'auto',
  });
  seedPreferences({ region: 'bogus', torchPreference: 'rocm' });
  expect(supervisor().status).toMatchObject({
    runtimeRegion: 'auto',
    runtimeTorchPreference: 'rocm',
  });
});

it.each(['default', 'ROCM', '', null, {}, 1])('rejects invalid GUI preference %j', async (raw) => {
  const instance = supervisor();
  await instance.start();
  expect(() => instance.setRuntimeTorchPreference(raw)).toThrow('invalid_runtime_torch_preference');
  await expect(instance.beginRuntimeTorchSetup(raw)).rejects.toThrow(
    'invalid_runtime_torch_preference',
  );
  expect(fixture.install).not.toHaveBeenCalled();
});

it.each([
  ['linux', 'arm64', true, true],
  ['darwin', 'arm64', true, true],
  ['win32', 'arm64', true, true],
  ['win32', 'x64', false, true],
  ['win32', 'x64', true, false],
] as const)(
  'refuses unsupported host %s/%s packaged=%s download=%s',
  async (platform, arch, packaged, download) => {
    vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
    vi.spyOn(process, 'arch', 'get').mockReturnValue(arch);
    fixture.packaged = packaged;
    fixture.distribution = download;
    const instance = supervisor();
    expect(instance.status.runtimeTorchSupported ?? false).toBe(false);
    expect(() => instance.setRuntimeTorchPreference('rocm')).toThrow('runtime_torch_unavailable');
    await expect(instance.beginRuntimeTorchSetup('rocm')).rejects.toThrow(
      'runtime_torch_unavailable',
    );
  },
);

it.each(['idle', 'ready', 'starting', 'installing', 'failed'])(
  'refuses a direct preference save in %s',
  (stage) => {
    const instance = supervisor();
    Object.assign(instance, { stage });
    expect(() => instance.setRuntimeTorchPreference('rocm')).toThrow('runtime_torch_unavailable');
    expect(fixture.install).not.toHaveBeenCalled();
  },
);

it.each(
  (['win32', 'linux'] as const).flatMap((platform) =>
    (['cpu', 'cuda', 'rocm'] as const).flatMap((preference) =>
      (['global', 'china'] as const).map((region) => ({ platform, preference, region })),
    ),
  ),
)(
  'carries $platform saved $preference through $region setup, host fallback and installer children',
  async ({ platform, preference, region }) => {
    vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
    seedPreferences({ region });
    vi.stubEnv('OMNIVOICE_DEVICE', 'cpu');
    vi.stubEnv('OMNIVOICE_DATA_DIR', join(fixture.profile, 'existing-data'));
    const instance = supervisor();
    await instance.start();
    instance.setRuntimeTorchPreference(preference);
    fixture.install.mockImplementation(
      async (_bundle, project, _uv, run, _signal, _phase, _region, _choice, downloads) => {
        await run('fixture-python', ['-c', 'pass'], project, {});
        downloads.pypiHost('files.pythonhosted.org');
      },
    );
    await instance.setupRuntime();
    const choice = fixture.install.mock.calls[0][7];
    expect(choice).toEqual({
      variant: preference === 'cuda' ? 'default' : preference,
      explicit: true,
    });
    expect(fixture.ready).toHaveBeenCalledWith(
      expect.any(String),
      expect.any(String),
      choice,
      true,
    );
    expect(fixture.compatible).toHaveBeenCalledWith(
      expect.any(String),
      expect.any(String),
      choice,
      true,
    );
    expect(fixture.hosts).toHaveBeenCalledWith(
      expect.any(String),
      platform,
      'x64',
      choice,
      region === 'china' ? 'mirrors.aliyun.com' : 'files.pythonhosted.org',
    );
    expect(fixture.hosts).toHaveBeenCalledWith(
      expect.any(String),
      platform,
      'x64',
      choice,
      'files.pythonhosted.org',
    );
    expect(fixture.spawn.mock.calls[0][2].env).toMatchObject({
      OMNIVOICE_TORCH_VARIANT: preference,
      OMNIVOICE_DEVICE: 'cpu',
      OMNIVOICE_DATA_DIR: join(fixture.profile, 'existing-data'),
    });
    expect(fixture.remove).not.toHaveBeenCalled();
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('');
  },
);

it('passes the same ROCm choice to dependency verification and the owned backend', async () => {
  vi.stubEnv('HF_ENDPOINT', undefined);
  seedPreferences({ region: 'china', torchPreference: 'rocm' });
  fixture.ready.mockResolvedValue(true);
  const instance = supervisor();
  await instance.start();
  const choice = fixture.ready.mock.calls[0][2];
  expect(choice).toEqual({ variant: 'rocm', explicit: true });
  expect(fixture.dependencies).toHaveBeenCalledWith(expect.any(String), undefined, choice, true);
  expect(fixture.spawn.mock.calls[0][2].env).toMatchObject({
    OMNIVOICE_TORCH_VARIANT: 'rocm',
  });
  expect(fixture.spawn.mock.calls[0][2].env.HF_ENDPOINT).toBeUndefined();
});

it.each([
  'remote',
  'override',
  'unowned',
  'external',
  'installing',
  'cleaning',
  'exited',
  'signalled',
  'no_pid',
  'pinned',
  'shutdown',
])(
  'refuses confirmed migration for %s without saving, stopping or installing',
  async (condition) => {
    const instance = supervisor();
    ownedReady(instance);
    if (condition === 'remote') Object.assign(instance, { remoteUrl: 'http://remote:8000' });
    if (condition === 'override') vi.stubEnv('OMNIVOICE_BACKEND_CMD', 'custom-python');
    if (condition === 'unowned')
      Object.assign(instance, { runtimeProject: join(fixture.profile, 'other', 'project') });
    if (condition === 'external') Object.assign(instance, { managed: false });
    if (condition === 'installing')
      Object.assign(instance, { installation: new AbortController() });
    if (condition === 'cleaning') Object.assign(instance, { cleaningRuntime: true });
    if (condition === 'exited')
      Object.assign((instance as unknown as { child: object }).child, { exitCode: 0 });
    if (condition === 'signalled')
      Object.assign((instance as unknown as { child: object }).child, { killed: true });
    if (condition === 'no_pid')
      Object.assign((instance as unknown as { child: object }).child, { pid: undefined });
    if (condition === 'pinned') vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
    if (condition === 'shutdown') Object.assign(instance, { shuttingDown: true });
    await expect(instance.beginRuntimeTorchSetup('rocm')).rejects.toThrow(/runtime_torch_/);
    expect(instance.status.stage).toBe('ready');
    expect(fixture.install).not.toHaveBeenCalled();
    expect(fixture.kill).not.toHaveBeenCalled();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  },
);

it.each(['win32', 'linux'] as const)(
  'schedules a confirmed %s choice without interrupting active jobs or its owned child',
  async (platform) => {
    vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
    seedPreferences({ region: 'global', torchPreference: 'cpu' });
    const instance = supervisor();
    const child = ownedReady(instance);
    const generation = (instance as unknown as { generation: number }).generation;
    const statusChanged = vi.fn();
    instance.subscribe(statusChanged);
    vi.stubEnv('OMNIVOICE_DEVICE', 'cpu');
    expect(instance.status.runtimeTorchSwitchable).toBe(true);
    await expect(instance.beginRuntimeTorchSetup('rocm')).resolves.toBeUndefined();
    expect(instance.status.stage).toBe('ready');
    expect(instance.status.managed).toBe(true);
    expect(instance.status.runtimeTorchSwitchable).toBe(true);
    expect(instance.status.runtimeTorchSetupPending).toBe(true);
    expect(instance.status.runtimeTorchPreference).toBe('rocm');
    expect((instance as unknown as { child: unknown }).child).toBe(child);
    expect((instance as unknown as { generation: number }).generation).toBe(generation);
    expect(preferences()).toEqual({
      region: 'global',
      torchPreference: 'rocm',
      torchSetupPending: true,
    });
    expect(statusChanged).toHaveBeenCalledWith(
      expect.objectContaining({
        stage: 'ready',
        runtimeTorchPreference: 'rocm',
        runtimeTorchSetupPending: true,
      }),
    );
    expect(fixture.install).not.toHaveBeenCalled();
    expect(fixture.remove).not.toHaveBeenCalled();
    expect(fixture.spawn).not.toHaveBeenCalled();
    expect(fixture.kill).not.toHaveBeenCalled();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('');
    expect(process.env.OMNIVOICE_DEVICE).toBe('cpu');
  },
);

it.each(['win32', 'linux'] as const)(
  'replaces a pending %s target and requires explicit setup after the app next starts',
  async (platform) => {
    vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
    seedPreferences({ region: 'china', torchPreference: 'cpu' });
    const instance = supervisor();
    ownedReady(instance);
    await instance.beginRuntimeTorchSetup('rocm');
    await instance.beginRuntimeTorchSetup('cuda');
    expect(preferences()).toEqual({
      region: 'china',
      torchPreference: 'cuda',
      torchSetupPending: true,
    });
    expect(fixture.kill).not.toHaveBeenCalled();
    await instance.shutdown();
    fixture.kill.mockClear();
    fixture.ready.mockResolvedValue(true);
    const restarted = supervisor();
    await restarted.start();
    expect(restarted.status).toMatchObject({
      stage: 'setup_required',
      runtimeTorchPreference: 'cuda',
      runtimeTorchSetupPending: true,
    });
    expect(fixture.install).not.toHaveBeenCalled();
    expect(fixture.spawn).not.toHaveBeenCalled();
    expect(fixture.kill).not.toHaveBeenCalled();
    await restarted.setupRuntime();
    expect(fixture.install).not.toHaveBeenCalled();
    expect(restarted.status.runtimeTorchSetupPending).toBe(false);
    expect(preferences()).toEqual({ region: 'china', torchPreference: 'cuda' });
    expect(fixture.spawn).toHaveBeenCalledOnce();
  },
);

it.each(['write', 'rename'])(
  'leaves a healthy runtime and its preferences unchanged on a failed migration %s',
  async (failure) => {
    seedPreferences({ region: 'china', torchPreference: 'cpu' });
    const instance = supervisor();
    const child = ownedReady(instance);
    fixture.failWrite = failure === 'write';
    fixture.failRename = failure === 'rename';
    await expect(instance.beginRuntimeTorchSetup('rocm')).rejects.toThrow();
    expect(preferences()).toEqual({ region: 'china', torchPreference: 'cpu' });
    expect(instance.status).toMatchObject({
      stage: 'ready',
      runtimeTorchPreference: 'cpu',
      runtimeTorchSetupPending: false,
    });
    expect((instance as unknown as { child: unknown }).child).toBe(child);
    expect(fixture.install).not.toHaveBeenCalled();
    expect(fixture.spawn).not.toHaveBeenCalled();
    expect(fixture.kill).not.toHaveBeenCalled();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  },
);
