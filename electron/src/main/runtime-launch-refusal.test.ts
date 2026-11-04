// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { execFile, spawn, spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const host = vi.hoisted(() => ({ data: '' }));
const originalResources = Object.getOwnPropertyDescriptor(process, 'resourcesPath');
vi.mock('electron', () => ({ app: { isPackaged: true, getPath: () => host.data } }));
vi.mock('node:child_process', async (original) => ({
  ...(await original<typeof import('node:child_process')>()),
  execFile: vi.fn(),
  spawn: vi.fn(),
  spawnSync: vi.fn(),
}));
vi.mock('./runtime-project', async (original) => ({
  ...(await original<typeof import('./runtime-project')>()),
  // Structural metadata is intact; run the real dependency probe beneath it.
  runtimeReady: vi.fn(async () => true),
  runtimeCompatible: vi.fn(async () => false),
  stageRuntimeSources: vi.fn(async () => undefined),
}));
import {
  runtimePython,
  runtimeReady,
  runtimeCompatible,
  type TorchChoice,
} from './runtime-project';
import { BackendSupervisor } from './backend';
import { runtimeLaunchRefusal } from './runtime-launch-refusal';

beforeEach(async () => {
  host.data = await mkdtemp(join(tmpdir(), 'vs-launch-refusal-'));
  await mkdir(join(host.data, 'runtime', 'project', '.venv'), { recursive: true });
  Object.defineProperty(process, 'resourcesPath', { configurable: true, value: host.data });
  vi.spyOn(process, 'platform', 'get').mockReturnValue('win32');
  vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  vi.stubEnv('LOCALAPPDATA', join(host.data, 'legacy'));
  vi.stubEnv('APPDATA', '');
  vi.stubEnv('OMNIVOICE_PORT', '18234');
  vi.mocked(runtimeReady).mockResolvedValue(true);
  vi.mocked(runtimeCompatible).mockResolvedValue(false);
});
afterEach(async () => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  if (originalResources) Object.defineProperty(process, 'resourcesPath', originalResources);
  else delete (process as { resourcesPath?: string }).resourcesPath;
  await rm(host.data, { recursive: true, force: true });
});

function supervisor() {
  const value = new BackendSupervisor();
  vi.spyOn(value as unknown as { probe: () => Promise<boolean> }, 'probe').mockResolvedValue(false);
  const repair = vi.spyOn(value, 'setupRuntime').mockResolvedValue(undefined);
  return { value, repair };
}

function failProbe(mode: 'sync' | 'callback', code: string | number, stderr = '') {
  vi.mocked(execFile).mockImplementation(((
    _command: unknown,
    _args: unknown,
    _options: unknown,
    callback: (error: Error, stdout: string, stderr: string) => void,
  ) => {
    const error = Object.assign(new Error(code === 'UNKNOWN' ? 'spawn UNKNOWN' : 'launch failed'), {
      code,
    });
    if (mode === 'sync') throw error;
    callback(error, '', stderr);
  }) as never);
}

it.each([
  ['sync', 'UNKNOWN', 1, true],
  ['callback', 'UNKNOWN', 1, true],
  ['sync', 'UNKNOWN', 2, true],
  ['callback', 'UNKNOWN', 2, true],
  ['sync', 'UNKNOWN', 0, false],
  ['callback', 'UNKNOWN', 0, false],
  ['sync', 'EACCES', 0, false],
  ['callback', 'EACCES', 0, false],
  ['sync', 'EPERM', 0, false],
  ['callback', 'EPERM', 0, false],
] as const)(
  'reports %s %s refusal with SAC state %i without reinstalling',
  async (mode, code, sac, namesSac) => {
    failProbe(mode, code);
    vi.mocked(spawnSync).mockReturnValue({
      stdout: `VerifiedAndReputablePolicyState REG_DWORD 0x${sac}`,
    } as ReturnType<typeof spawnSync>);
    const { value, repair } = supervisor();

    await value.start();

    expect(value.status.stage).toBe('failed');
    expect(value.status.managed).toBe(false);
    expect(value.status.message).toContain(runtimePython(join(host.data, 'runtime', 'project')));
    expect(value.status.message?.includes('Windows Smart App Control blocked it.')).toBe(namesSac);
    expect(value.status.message).not.toContain('repair the local runtime');
    expect(repair).not.toHaveBeenCalled();
    expect(spawn).not.toHaveBeenCalled();
  },
);

it.each(['sync', 'callback'] as const)(
  'still repairs a missing interpreter reported by %s ENOENT',
  async (mode) => {
    failProbe(mode, 'ENOENT');
    const { value, repair } = supervisor();
    await value.start();
    expect(repair).toHaveBeenCalledOnce();
    expect(value.status.stage).toBe('setup_required');
  },
);

it('still repairs a genuine missing-module failure', async () => {
  failProbe('callback', 1, "ModuleNotFoundError: No module named 'uvicorn'");
  const { value, repair } = supervisor();
  await value.start();
  expect(repair).toHaveBeenCalledOnce();
  expect(value.status.stage).toBe('setup_required');
});

it('keeps kill-related EPERM on the ordinary runtime repair path', async () => {
  const failure = Object.assign(new Error('kill EPERM'), { code: 'EPERM', syscall: 'kill' });
  expect(
    runtimeLaunchRefusal(runtimePython(join(host.data, 'runtime', 'project')), failure),
  ).toBeNull();
  vi.mocked(execFile).mockImplementation(((
    _command: unknown,
    _args: unknown,
    _options: unknown,
    callback: (error: Error, stdout: string, stderr: string) => void,
  ) => callback(failure, '', '')) as never);
  const { value, repair } = supervisor();
  await value.start();
  expect(repair).toHaveBeenCalledOnce();
  expect(value.status.stage).toBe('setup_required');
  expect(spawn).not.toHaveBeenCalled();
});

it.each(['darwin', 'linux'] as const)(
  'reports a refused Python launch on %s without reinstalling',
  async (platform) => {
    vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
    vi.spyOn(process, 'arch', 'get').mockReturnValue(platform === 'darwin' ? 'arm64' : 'x64');
    failProbe('callback', 'EACCES');
    const { value, repair } = supervisor();
    await value.start();
    expect(value.status.stage).toBe('failed');
    expect(value.status.message).toContain(runtimePython(join(host.data, 'runtime', 'project')));
    expect(value.status.message).not.toContain('Smart App Control');
    expect(repair).not.toHaveBeenCalled();
  },
);

function candidateProbes(ready: string[], refused: string[]) {
  vi.mocked(runtimeReady).mockImplementation(async (_bundle, project) => ready.includes(project));
  vi.mocked(execFile).mockImplementation(((
    command: string,
    _args: unknown,
    _options: unknown,
    callback: (error: Error | null, stdout: string, stderr: string) => void,
  ) => {
    if (refused.some((project) => runtimePython(project) === command)) {
      callback(Object.assign(new Error('spawn UNKNOWN'), { code: 'UNKNOWN' }), '', '');
    } else callback(null, '', '');
  }) as never);
}

function runtimeCandidates() {
  return {
    own: join(host.data, 'runtime', 'project'),
    legacy: join(host.data, 'legacy', 'com.debpalash.omnivoice-studio', 'project'),
  };
}

it('offers setup for a missing default runtime despite a refused legacy candidate', async () => {
  const { own, legacy } = runtimeCandidates();
  await rm(join(own, '.venv'), { recursive: true });
  candidateProbes([legacy], [legacy]);
  const { value, repair } = supervisor();
  await value.start();
  expect(value.status.stage).toBe('setup_required');
  expect(value.status.runtimePath).toBe(join(host.data, 'runtime'));
  expect(repair).not.toHaveBeenCalled();
  expect(spawn).not.toHaveBeenCalled();
});

it('reuses a healthy legacy runtime after the default interpreter refuses to launch', async () => {
  const { own, legacy } = runtimeCandidates();
  candidateProbes([own, legacy], [own]);
  const { value, repair } = supervisor();
  const launch = vi
    .spyOn(
      value as unknown as {
        spawnChild: (
          plan: { argv: string[]; cwd: string },
          gen: number,
          choice?: TorchChoice,
        ) => void;
      },
      'spawnChild',
    )
    .mockImplementation(() => undefined);
  await value.start();
  expect(launch).toHaveBeenCalledWith(
    expect.objectContaining({
      cwd: legacy,
      argv: expect.arrayContaining([runtimePython(legacy)]),
      managedRuntime: false,
    }),
    expect.any(Number),
    { variant: 'cpu', explicit: true },
  );
  expect(value.status.runtimePath).toBe(
    join(host.data, 'legacy', 'com.debpalash.omnivoice-studio'),
  );
  expect(repair).not.toHaveBeenCalled();
});

it('keeps a refused configured runtime authoritative over a healthy default', async () => {
  const { own } = runtimeCandidates();
  const configuredRoot = join(host.data, 'chosen');
  const configured = join(configuredRoot, 'project');
  await writeFile(
    join(host.data, 'runtime-location.json'),
    JSON.stringify({ root: configuredRoot, owned: true }),
  );
  candidateProbes([configured, own], [configured]);
  const { value, repair } = supervisor();
  await value.start();
  expect(value.status.stage).toBe('failed');
  expect(value.status.runtimePath).toBe(configuredRoot);
  expect(value.status.message).toContain(runtimePython(configured));
  expect(vi.mocked(execFile).mock.calls.map(([command]) => command)).not.toContain(
    runtimePython(own),
  );
  expect(repair).not.toHaveBeenCalled();
  expect(spawn).not.toHaveBeenCalled();
});

it('preserves refusal of the selected default when there is no healthy alternative', async () => {
  const { own } = runtimeCandidates();
  candidateProbes([own], [own]);
  const { value, repair } = supervisor();
  await value.start();
  expect(value.status.stage).toBe('failed');
  expect(value.status.runtimePath).toBe(join(host.data, 'runtime'));
  expect(value.status.message).toContain(runtimePython(own));
  expect(repair).not.toHaveBeenCalled();
  expect(spawn).not.toHaveBeenCalled();
});
