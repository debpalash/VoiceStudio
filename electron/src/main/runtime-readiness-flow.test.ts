// @vitest-environment node
import { fixture, runner, report, recipe } from './runtime-windows-rocm.fixture';
import { execFile } from 'node:child_process';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import {
  installRuntime,
  runtimeDependenciesReady,
  runtimeReady,
  runtimeCompatible,
  runtimePython,
} from './runtime-project';
import type { TorchChoice } from './runtime-project';
import { runtimeProjectReady } from './runtime-readiness';
import {
  RUNTIME_TORCH_MARKER,
  windowsRocmReady,
  withWindowsRocmReadinessDecision,
} from './runtime-windows-rocm';
import { RuntimeLaunchRefusedError } from './runtime-launch-refusal';
const host = vi.hoisted(() => ({ data: '' }));
const originalResources = Object.getOwnPropertyDescriptor(process, 'resourcesPath');
afterEach(() => {
  if (originalResources) Object.defineProperty(process, 'resourcesPath', originalResources);
  else delete (process as { resourcesPath?: string }).resourcesPath;
});
vi.mock('electron', () => ({ app: { isPackaged: true, getPath: () => host.data } }));
vi.mock('./distribution', () => ({ runtimeSetupSupported: () => true }));
import { BackendSupervisor } from './backend';

async function prepared() {
  const { bundle, project } = await fixture();
  host.data = join(dirname(project), 'user');
  await mkdir(host.data);
  await writeFile(
    join(host.data, 'runtime-location.json'),
    JSON.stringify({
      root: dirname(project),
      owned: true,
    }),
  );
  // The production resolver appends project to the configured root.
  const target = join(dirname(project), 'project');
  Object.defineProperty(process, 'resourcesPath', { configurable: true, value: bundle });
  await installRuntime(
    bundle,
    target,
    'uv',
    runner(target),
    new AbortController().signal,
    undefined,
    'global',
  );
  vi.mocked(execFile).mockClear();
  const supervisor = new BackendSupervisor();
  const internal = supervisor as unknown as {
    resolveRuntimeProject: (choice?: TorchChoice) => Promise<{ project: string; ready: boolean }>;
    probe: () => Promise<boolean>;
    runtimeProject: string;
    stage: string;
  };
  return { bundle, target, supervisor, internal };
}
function nativeCalls() {
  return vi
    .mocked(execFile)
    .mock.calls.filter(([, args]) => (args as string[]).includes('--verify-only'));
}

it.each([true, false])(
  'SDK isolation propagates configured ownership=%s through readiness and imports',
  async (owned) => {
    const { target, internal } = await prepared();
    await writeFile(
      join(host.data, 'runtime-location.json'),
      JSON.stringify({ root: dirname(target), owned }),
    );
    vi.stubEnv('HIP_PATH', 'parent-sdk');
    const choice: TorchChoice = { variant: 'rocm', explicit: true };
    expect((await internal.resolveRuntimeProject(choice)).ready).toBe(true);
    expect(nativeCalls()).toHaveLength(1);
    expect(vi.mocked(execFile).mock.calls).toHaveLength(2);
    for (const [, , options] of vi.mocked(execFile).mock.calls) {
      expect((options as { env: NodeJS.ProcessEnv }).env.HIP_PATH).toBe(
        owned ? undefined : 'parent-sdk',
      );
    }
    expect(process.env.HIP_PATH).toBe('parent-sdk');
  },
);

it('SDK isolation keeps managed and unmanaged native results separate within one readiness decision', async () => {
  const { target } = await prepared();
  vi.stubEnv('HIP_PATH', 'parent-sdk');
  const ready = await withWindowsRocmReadinessDecision(async () => {
    const actual = [
      await windowsRocmReady(target, undefined, true),
      await windowsRocmReady(target, undefined, false),
      await windowsRocmReady(target, undefined, true),
    ];
    expect(actual).toEqual([true, true, true]);
    return actual.every(Boolean);
  });
  expect(ready).toBe(true);
  expect(nativeCalls()).toHaveLength(2);
  expect((nativeCalls()[0][2] as { env: NodeJS.ProcessEnv }).env.HIP_PATH).toBeUndefined();
  expect((nativeCalls()[1][2] as { env: NodeJS.ProcessEnv }).env.HIP_PATH).toBe('parent-sdk');
  expect(process.env.HIP_PATH).toBe('parent-sdk');
});

it.each(['sync', 'callback'] as const)(
  'reports an early Windows ROCm %s launch refusal without reinstalling',
  async (mode) => {
    const { supervisor, target } = await prepared();
    vi.spyOn(supervisor as unknown as { probe: () => Promise<boolean> }, 'probe').mockResolvedValue(
      false,
    );
    const repair = vi.spyOn(supervisor, 'setupRuntime').mockResolvedValue(undefined);
    vi.mocked(execFile).mockImplementation(((
      _c: unknown,
      _a: unknown,
      _o: unknown,
      callback: (error: Error, out: string, err: string) => void,
    ) => {
      const error = Object.assign(new Error('spawn UNKNOWN'), { code: 'UNKNOWN' });
      if (mode === 'sync') throw error;
      callback(error, '', '');
    }) as never);

    await supervisor.start();

    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain(runtimePython(target));
    expect(repair).not.toHaveBeenCalled();
    expect(nativeCalls()).toHaveLength(1);
  },
);

it.each(['ready', 'compatible', 'dependencies'] as const)(
  'propagates Windows ROCm launch refusal through %s checks',
  async (check) => {
    const { bundle, target } = await prepared();
    vi.mocked(execFile).mockImplementation(((
      _c: unknown,
      _a: unknown,
      _o: unknown,
      callback: (error: Error, out: string, err: string) => void,
    ) =>
      callback(Object.assign(new Error('spawn UNKNOWN'), { code: 'UNKNOWN' }), '', '')) as never);
    const result =
      check === 'ready'
        ? runtimeReady(bundle, target)
        : check === 'compatible'
          ? runtimeCompatible(bundle, target)
          : runtimeDependenciesReady(target);
    await expect(result).rejects.toBeInstanceOf(RuntimeLaunchRefusedError);
  },
);

it('still repairs a genuinely failed model-free Windows ROCm verification', async () => {
  const { supervisor } = await prepared();
  vi.spyOn(supervisor as unknown as { probe: () => Promise<boolean> }, 'probe').mockResolvedValue(
    false,
  );
  const repair = vi.spyOn(supervisor, 'setupRuntime').mockResolvedValue(undefined);
  vi.mocked(execFile).mockImplementation(((
    _c: unknown,
    _a: unknown,
    _o: unknown,
    callback: (error: Error, out: string, err: string) => void,
  ) =>
    callback(
      new Error('verification failed'),
      JSON.stringify({ ready: false, scope: 'model-free' }),
      '',
    )) as never);
  await supervisor.start();
  expect(repair).toHaveBeenCalledOnce();
  expect(supervisor.status.stage).toBe('setup_required');
});

it.each(['rocm', 'cpu'])(
  'resolves a healthy ROCm project with one native verification and ambient %s',
  async (ambient) => {
    const { internal, target } = await prepared();
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', ambient);
    const choice: TorchChoice | undefined =
      ambient === 'cpu' ? { variant: 'rocm', explicit: true } : undefined;
    expect((await internal.resolveRuntimeProject(choice)).ready).toBe(true);
    expect(nativeCalls()).toHaveLength(1);
    expect(await runtimeDependenciesReady(target, undefined, choice)).toBe(true);
    expect(nativeCalls()).toHaveLength(2);
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe(ambient);
  },
);

it('isolates native verification across concurrent explicit readiness decisions', async () => {
  const { bundle, target } = await prepared();
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
  const choice: TorchChoice = { variant: 'rocm', explicit: true };
  expect(
    await Promise.all([
      runtimeProjectReady(bundle, target, choice),
      runtimeProjectReady(bundle, target, choice),
    ]),
  ).toEqual([true, true]);
  expect(nativeCalls()).toHaveLength(2);
  expect(await runtimeProjectReady(bundle, target, choice)).toBe(true);
  expect(nativeCalls()).toHaveLength(3);
  expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('cpu');
});

it('setup reuse makes one native verification in its readiness decision', async () => {
  vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
  const { internal, supervisor, target } = await prepared();
  internal.runtimeProject = target;
  internal.stage = 'setup_required';
  vi.spyOn(supervisor, 'start').mockResolvedValue(undefined);
  await supervisor.setupRuntime();
  expect(nativeCalls()).toHaveLength(1);
  expect(supervisor.start).toHaveBeenCalledOnce();
});

it.each(['rocm', 'cpu'])(
  'shares a failed ready/compatible decision and rechecks later with ambient %s',
  async (ambient) => {
    const { internal } = await prepared();
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', ambient);
    const choice: TorchChoice | undefined =
      ambient === 'cpu' ? { variant: 'rocm', explicit: true } : undefined;
    vi.mocked(execFile).mockImplementationOnce(((
      _c: unknown,
      _a: unknown,
      _o: unknown,
      callback: (error: Error | null, out: string, err: string) => void,
    ) => callback(Error('driver'), '', '')) as never);
    expect((await internal.resolveRuntimeProject(choice)).ready).toBe(false);
    expect(nativeCalls()).toHaveLength(1);
    expect((await internal.resolveRuntimeProject(choice)).ready).toBe(true);
    expect(nativeCalls()).toHaveLength(2);
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe(ambient);
  },
);

it.each([
  { installed: null, variant: 'rocm', repair: false },
  { installed: 'cpu', variant: 'rocm', repair: false },
  { installed: 'windows-rocm', variant: 'rocm', repair: true },
  { installed: 'cpu', variant: 'cpu', repair: true },
] as const)(
  'requires first ROCm install consent for marker $installed and choice $variant',
  async ({ installed, variant, repair }) => {
    const { internal, supervisor, target } = await prepared();
    const marker = join(target, RUNTIME_TORCH_MARKER);
    if (installed === null) await rm(marker);
    else await writeFile(marker, installed);
    vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
    vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
    vi.spyOn(internal, 'probe').mockResolvedValue(false);
    const resolveProject = vi.spyOn(internal, 'resolveRuntimeProject').mockResolvedValue({
      project: target,
      ready: false,
    });
    const setup = vi.spyOn(supervisor, 'setupRuntime').mockResolvedValue(undefined);
    const choice: TorchChoice = { variant, explicit: true };
    await supervisor.start(choice);
    expect(resolveProject).toHaveBeenCalledExactlyOnceWith(choice);
    expect(supervisor.status.stage).toBe('setup_required');
    expect(setup).toHaveBeenCalledTimes(repair ? 1 : 0);
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('rocm');
  },
);

it('changed recipe cannot reuse earlier success and repair requires fresh verification', async () => {
  const { internal, bundle, target } = await prepared();
  expect((await internal.resolveRuntimeProject()).ready).toBe(true);
  await writeFile(join(bundle, 'scripts/windows-rocm-recipe.json'), recipe + '\n');
  expect((await internal.resolveRuntimeProject()).ready).toBe(false);
  expect(nativeCalls()).toHaveLength(1);
  await installRuntime(
    bundle,
    target,
    'uv',
    runner(target),
    new AbortController().signal,
    undefined,
    'global',
  );
  vi.mocked(execFile).mockImplementationOnce(((
    _c: unknown,
    _a: unknown,
    _o: unknown,
    callback: (error: Error | null, out: string, err: string) => void,
  ) => callback(Error('still broken after repair'), report, '')) as never);
  expect((await internal.resolveRuntimeProject()).ready).toBe(false);
  expect(nativeCalls()).toHaveLength(2);
});
