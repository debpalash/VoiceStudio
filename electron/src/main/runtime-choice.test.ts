import { fixture, interpreter, runner, report, recipe } from './runtime-windows-rocm.fixture';
import { execFile, execFileSync } from 'node:child_process';
import { readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import { downloadRuntimeInstaller } from './runtime-download';
import {
  CPU_TORCH_PINS,
  ROCM_TORCH_PINS,
  installRuntime,
  managedPythonRequest,
  runtimeCompatible,
  runtimeDependenciesReady,
  runtimeReady,
  type TorchChoice,
} from './runtime-project';
import { runtimeProjectReady } from './runtime-readiness';

vi.mock('./runtime-download', () => ({ downloadRuntimeInstaller: vi.fn(async () => '') }));
vi.mock('./proxy-env', () => ({ downloadProxyEnv: () => ({}) }));

const CPU: TorchChoice = { variant: 'cpu', explicit: true };
const CUDA: TorchChoice = { variant: 'default', explicit: true };
const HIP: TorchChoice = { variant: 'rocm', explicit: true };
const INFERRED_CPU: TorchChoice = { variant: 'cpu', explicit: false };

it.each(['ready', 'compatible', 'imports'] as const)(
  'SDK isolation reaches the actual managed %s child environment',
  async (operation) => {
    const { bundle, project } = await fixture();
    await installRuntime(
      bundle,
      project,
      'uv',
      runner(project),
      new AbortController().signal,
      undefined,
      'global',
      HIP,
    );
    const selectors = ['HIP_PATH', 'ROCM_HOME', 'ROCM_PATH'];
    for (const key of selectors) vi.stubEnv(key, 'parent-sdk');
    vi.stubEnv('HIP_VISIBLE_DEVICES', '0');
    vi.stubEnv('HSA_OVERRIDE_GFX_VERSION', 'unqualified');
    vi.mocked(execFile).mockClear();
    const ready =
      operation === 'ready'
        ? await runtimeReady(bundle, project, HIP, true)
        : operation === 'compatible'
          ? await runtimeCompatible(bundle, project, HIP, true)
          : await runtimeDependenciesReady(project, undefined, HIP, true);
    expect(ready).toBe(true);
    expect(vi.mocked(execFile).mock.calls).toHaveLength(operation === 'imports' ? 2 : 1);
    for (const [, args, options] of vi.mocked(execFile).mock.calls) {
      expect(['-m', '-c']).toContain((args as string[])[0]);
      const env = (options as { env: NodeJS.ProcessEnv }).env;
      expect(Object.keys(env).filter((key) => selectors.includes(key.toUpperCase()))).toEqual([]);
      expect(env.HIP_VISIBLE_DEVICES).toBe('0');
      expect(env.HSA_OVERRIDE_GFX_VERSION).toBe('unqualified');
    }
    expect(selectors.map((key) => process.env[key])).toEqual(selectors.map(() => 'parent-sdk'));
  },
);

it.each([
  { platform: 'win32', choice: HIP, managed: false },
  { platform: 'win32', choice: HIP, managed: undefined },
  { platform: 'win32', choice: CPU, managed: true },
  { platform: 'win32', choice: CUDA, managed: true },
  { platform: 'linux', choice: HIP, managed: true },
] as const)(
  'SDK isolation preserves user SDK for $platform/$choice.variant managed=$managed imports',
  async ({ platform, choice, managed }) => {
    const { project } = await fixture();
    vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
    vi.stubEnv('HIP_PATH', 'user-sdk');
    vi.stubEnv('ROCM_HOME', 'user-sdk');
    vi.stubEnv('ROCM_PATH', 'user-sdk');
    vi.mocked(execFile).mockClear();
    expect(await runtimeDependenciesReady(project, undefined, choice, managed)).toBe(true);
    expect(execFile).toHaveBeenCalled();
    for (const [, , options] of vi.mocked(execFile).mock.calls) {
      const env = (options as { env: NodeJS.ProcessEnv }).env;
      expect([env.HIP_PATH, env.ROCM_HOME, env.ROCM_PATH]).toEqual([
        'user-sdk',
        'user-sdk',
        'user-sdk',
      ]);
    }
    expect(process.env.HIP_PATH).toBe('user-sdk');
  },
);

const installs = [
  { platform: 'win32', arch: 'x64', choice: CPU, ambient: 'rocm', python: '3.11' },
  { platform: 'win32', arch: 'x64', choice: CUDA, ambient: 'cpu', python: '3.11' },
  {
    platform: 'win32',
    arch: 'x64',
    choice: HIP,
    ambient: 'cpu',
    python: 'cpython-3.12-windows-x86_64',
  },
  { platform: 'linux', arch: 'x64', choice: CPU, ambient: 'rocm', python: '3.11' },
  { platform: 'linux', arch: 'x64', choice: CUDA, ambient: 'cpu', python: '3.11' },
  { platform: 'linux', arch: 'x64', choice: HIP, ambient: 'cpu', python: '3.11' },
  { platform: 'darwin', arch: 'arm64', choice: CUDA, ambient: 'rocm', python: '3.11' },
  { platform: 'linux', arch: 'arm64', choice: CUDA, ambient: 'rocm', python: '3.11' },
  {
    platform: 'win32',
    arch: 'arm64',
    choice: CPU,
    ambient: 'rocm',
    python: 'cpython-3.11-windows-x86_64',
  },
] as const;

function selectHost(platform: NodeJS.Platform, arch: typeof process.arch) {
  vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
  vi.spyOn(process, 'arch', 'get').mockReturnValue(arch);
}

it.each([0, 1])(
  'preserves an explicit HIP choice through regional mirror fallback %s',
  async (failures) => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
    const { bundle, project } = await fixture();
    const lock = [
      '[[package]]',
      'name = "numpy"',
      'wheels = [{ url = "https://files.pythonhosted.org/packages/numpy.whl", hash = "sha256:abc" }]',
      '[[package]]',
      'name = "nvidia-cudnn-cu12"',
      '',
    ].join('\n');
    await writeFile(join(bundle, 'uv.lock'), lock);
    const seen: string[] = [];
    const run = runner(project);
    const successful = run.getMockImplementation()!;
    run.mockImplementation(async (...args) => {
      if (args[1][0] === 'sync') {
        seen.push(await readFile(join(project, 'uv.lock'), 'utf8'));
        if (seen.length <= failures) throw new Error('mirror unavailable');
      }
      return successful(...args);
    });
    const pypiHost = vi.fn();

    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'china',
      HIP,
      { pypiHost },
    );

    expect(seen).toHaveLength(failures + 1);
    expect(seen[0]).toContain('https://mirrors.aliyun.com/pypi/packages/numpy.whl');
    expect(seen[0]).toContain('sha256:abc');
    if (failures) {
      expect(seen[1]).toBe(lock);
      expect(pypiHost).toHaveBeenCalledWith('files.pythonhosted.org');
    }
    for (const [, args, , env] of run.mock.calls) {
      expect(env?.OMNIVOICE_TORCH_VARIANT).toBe('rocm');
      if (args[0] === 'sync') {
        expect(args).toContain('--inexact');
        expect(args).toContain('torch');
        expect(args).toContain('nvidia-cudnn-cu12');
      }
    }
    expect(await readFile(join(project, 'uv.lock'), 'utf8')).toBe(lock);
    expect(await runtimeReady(bundle, project, HIP)).toBe(true);
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('cpu');
  },
);

it.each(installs)(
  'keeps $platform/$arch $choice.variant installation and subprocesses independent of $ambient',
  async ({ platform, arch, choice, ambient, python }) => {
    selectHost(platform, arch);
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', ambient);
    const { bundle, project } = await fixture();
    const run = runner(project);
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
      choice,
    );
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync')![1];
    expect(sync[sync.indexOf('--python') + 1]).toBe(python);
    const normalized = choice.variant === 'default' ? 'cuda' : choice.variant;
    for (const [, , , env] of run.mock.calls) {
      expect({ ...process.env, ...env }.OMNIVOICE_TORCH_VARIANT).toBe(normalized);
    }
    const windowsHip = platform === 'win32' && choice.variant === 'rocm';
    expect(sync.includes('--inexact')).toBe(windowsHip);
    expect(
      run.mock.calls.some(([, args]) => args.includes('backend.core.windows_rocm.install')),
    ).toBe(windowsHip);
    const pins = run.mock.calls.flatMap(([, args]) => args);
    expect(pins.includes(CPU_TORCH_PINS[0])).toBe(choice.variant === 'cpu');
    expect(pins.includes(`${ROCM_TORCH_PINS[0]}+rocm6.4`)).toBe(
      platform === 'linux' && choice.variant === 'rocm',
    );
    expect(await runtimeReady(bundle, project, choice)).toBe(true);
    expect(await runtimeCompatible(bundle, project, choice)).toBe(true);
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe(ambient);
  },
);

it.each(installs)(
  'selects $platform/$arch $choice.variant Python without consulting the ambient variant',
  ({ platform, arch, choice, ambient, python }) => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', ambient);
    expect(managedPythonRequest(platform, arch, choice)).toBe(python);
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe(ambient);
  },
);

it('rejects an explicit Windows ARM64 HIP choice even when the environment requests CPU', () => {
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
  expect(() => managedPythonRequest('win32', 'arm64', HIP)).toThrow('cp312');
});

it('passes HIP to the uv bootstrap, existing interpreter probes and checked Python installer', async () => {
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
  const { bundle, project } = await fixture();
  await interpreter(project);
  const run = runner(project);
  await installRuntime(
    bundle,
    project,
    null,
    run,
    new AbortController().signal,
    undefined,
    'global',
    HIP,
  );
  expect(downloadRuntimeInstaller).toHaveBeenCalledWith(
    expect.stringContaining('install.ps1'),
    expect.objectContaining({ OMNIVOICE_TORCH_VARIANT: 'rocm' }),
    expect.any(AbortSignal),
  );
  const abi = run.mock.calls.find(([, args]) => args[1]?.includes('validate_target()'));
  expect(abi).toBeDefined();
  const imports = run.mock.calls.find(([, args]) => args[1] === 'import sentencepiece');
  expect(imports).toBeDefined();
  for (const [, , , env] of run.mock.calls) expect(env?.OMNIVOICE_TORCH_VARIANT).toBe('rocm');
  expect(run.mock.calls.some(([, args]) => args.includes('--uv'))).toBe(true);
  expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('cpu');
});

it('inherits explicit HIP in an actual installer subprocess with ambient CPU', async () => {
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
  const { bundle, project } = await fixture();
  const run = runner(project);
  let inherited: string | undefined;
  run.mockImplementation(async (_command, args, _cwd, env) => {
    if (args[0] === 'sync') await interpreter(project);
    if (args.includes('--uv')) {
      inherited = execFileSync(process.execPath, ['-p', 'process.env.OMNIVOICE_TORCH_VARIANT'], {
        env: { ...process.env, ...env },
        encoding: 'utf8',
        windowsHide: true,
        timeout: 10_000,
      }).trim();
    }
    return report;
  });
  await installRuntime(
    bundle,
    project,
    'uv',
    run,
    new AbortController().signal,
    undefined,
    'global',
    HIP,
  );
  expect(inherited).toBe('rocm');
  expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('cpu');
});

it('keeps concurrent CPU, CUDA and HIP operations isolated without changing ambient CPU', async () => {
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
  const markers = await Promise.all(
    [CPU, CUDA, HIP].map(async (choice) => {
      const { bundle, project } = await fixture();
      const run = runner(project);
      await installRuntime(
        bundle,
        project,
        'uv',
        run,
        new AbortController().signal,
        undefined,
        'global',
        choice,
      );
      for (const [, , , env] of run.mock.calls) {
        expect(env?.OMNIVOICE_TORCH_VARIANT).toBe(
          choice.variant === 'default' ? 'cuda' : choice.variant,
        );
        expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('cpu');
      }
      expect(await runtimeProjectReady(bundle, project, choice)).toBe(true);
      return readFile(join(project, '.runtime-ready'), 'utf8');
    }),
  );
  expect(new Set(markers).size).toBe(3);
  expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('cpu');
});

it.each([CPU, CUDA, HIP])(
  'matches only the selected $variant stamp across ambient changes',
  async (choice) => {
    const { bundle, project } = await fixture();
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', choice.variant === 'default' ? 'cuda' : choice.variant);
    await installRuntime(
      bundle,
      project,
      'uv',
      runner(project),
      new AbortController().signal,
      undefined,
      'global',
    );
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', choice.variant === 'cpu' ? 'rocm' : 'cpu');
    expect(await runtimeReady(bundle, project, choice)).toBe(true);
    expect(await runtimeCompatible(bundle, project, choice)).toBe(true);
    for (const other of [CPU, CUDA, HIP].filter(
      (candidate) => candidate.variant !== choice.variant,
    )) {
      expect(await runtimeReady(bundle, project, other)).toBe(false);
      expect(await runtimeCompatible(bundle, project, other)).toBe(false);
    }
  },
);

it.each(['win32', 'linux'] as const)(
  'preserves inferred CPU reuse of stamped CUDA on %s while an explicit CPU choice rejects it',
  async (platform) => {
    selectHost(platform, 'x64');
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cuda');
    const { bundle, project } = await fixture();
    await installRuntime(
      bundle,
      project,
      'uv',
      runner(project),
      new AbortController().signal,
      undefined,
      'global',
    );
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
    expect(await runtimeReady(bundle, project, INFERRED_CPU)).toBe(true);
    expect(await runtimeCompatible(bundle, project, INFERRED_CPU)).toBe(true);
    expect(await runtimeReady(bundle, project, CPU)).toBe(false);
    expect(await runtimeCompatible(bundle, project, CPU)).toBe(false);
  },
);

it.each(['win32', 'linux', 'darwin'] as const)(
  'preserves markerless legacy reuse rules with explicit choices on %s',
  async (platform) => {
    selectHost(platform, platform === 'darwin' ? 'arm64' : 'x64');
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cuda');
    const { bundle, project } = await fixture();
    await installRuntime(
      bundle,
      project,
      'uv',
      runner(project),
      new AbortController().signal,
      undefined,
      'global',
    );
    await rm(join(project, '.runtime-ready'));
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
    expect(await runtimeCompatible(bundle, project, CUDA)).toBe(true);
    if (platform !== 'darwin') {
      expect(await runtimeCompatible(bundle, project, INFERRED_CPU)).toBe(true);
      expect(await runtimeCompatible(bundle, project, CPU)).toBe(false);
      expect(await runtimeCompatible(bundle, project, HIP)).toBe(false);
    }
    expect(await runtimeReady(bundle, project, CUDA)).toBe(false);
  },
);

it('checks both bundled and staged HIP recipes when the environment requests CPU', async () => {
  const { bundle, project } = await fixture();
  await installRuntime(
    bundle,
    project,
    'uv',
    runner(project),
    new AbortController().signal,
    undefined,
    'global',
  );
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
  const marker = await readFile(join(project, '.runtime-ready'), 'utf8');
  await writeFile(join(project, 'scripts/windows-rocm-recipe.json'), recipe + '\n');
  expect(await runtimeReady(bundle, project, HIP)).toBe(false);
  expect(await runtimeCompatible(bundle, project, HIP)).toBe(false);
  await writeFile(join(project, 'scripts/windows-rocm-recipe.json'), recipe);
  expect(await runtimeReady(bundle, project, HIP)).toBe(true);
  await writeFile(join(bundle, 'scripts/windows-rocm-recipe.json'), recipe + '\n');
  expect(await runtimeReady(bundle, project, HIP)).toBe(false);
  expect(await runtimeCompatible(bundle, project, HIP)).toBe(false);
  expect(await readFile(join(project, '.runtime-ready'), 'utf8')).toBe(marker);
});

it.each([CPU, CUDA])(
  'bypasses HIP gates and imports for explicit $variant despite an ambient HIP choice',
  async (choice) => {
    const { project } = await fixture();
    vi.mocked(execFile).mockImplementation(((
      _command: unknown,
      args: string[],
      options: { env: NodeJS.ProcessEnv },
      callback: (error: Error | null, stdout: string, stderr: string) => void,
    ) => {
      expect(args).not.toContain('--verify-only');
      expect(args[1]).not.toContain('initialize_dll_search');
      expect(options.env.OMNIVOICE_TORCH_VARIANT).toBe(
        choice.variant === 'default' ? 'cuda' : 'cpu',
      );
      callback(null, '', '');
    }) as never);
    expect(await runtimeDependenciesReady(project, undefined, choice)).toBe(true);
    expect(execFile).toHaveBeenCalledOnce();
  },
);

it.each(['native-failure', 'native-timeout', 'import-timeout'] as const)(
  'fails closed on explicit HIP %s despite ambient CPU',
  async (failure) => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
    const { project } = await fixture();
    const onFailure = vi.fn();
    vi.mocked(execFile).mockImplementation(((
      _command: unknown,
      args: string[],
      options: { env: NodeJS.ProcessEnv },
      callback: (error: Error | null, stdout: string, stderr: string) => void,
    ) => {
      if (args.includes('--verify-only'))
        callback(
          failure === 'native-failure'
            ? Error('driver')
            : failure === 'native-timeout'
              ? Object.assign(Error('timeout'), { killed: true, signal: 'SIGTERM' })
              : null,
          report,
          '',
        );
      else {
        expect(args[1]).toContain('initialize_dll_search');
        expect(options.env.OMNIVOICE_TORCH_VARIANT).toBe('rocm');
        callback(Object.assign(Error('timeout'), { killed: true, signal: 'SIGTERM' }), '', '');
      }
    }) as never);
    expect(await runtimeDependenciesReady(project, onFailure, HIP)).toBe(false);
    expect(onFailure).toHaveBeenCalledOnce();
  },
);

it.each([CPU, HIP])(
  'rejects failed application imports for explicit $variant when the environment requests the opposite',
  async (choice) => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', choice.variant === 'cpu' ? 'rocm' : 'cpu');
    const { bundle, project } = await fixture();
    await installRuntime(
      bundle,
      project,
      'uv',
      runner(project),
      new AbortController().signal,
      undefined,
      'global',
      choice,
    );
    vi.mocked(execFile).mockImplementation(((
      _command: unknown,
      args: string[],
      options: { env: NodeJS.ProcessEnv },
      callback: (error: Error | null, stdout: string, stderr: string) => void,
    ) => {
      if (args.includes('--verify-only')) callback(null, report, '');
      else {
        expect(options.env.OMNIVOICE_TORCH_VARIANT).toBe(choice.variant);
        expect(args[1]?.includes('initialize_dll_search')).toBe(choice.variant === 'rocm');
        callback(Error('import failed'), '', 'ModuleNotFoundError: missing runtime dependency');
      }
    }) as never);
    const onFailure = vi.fn();
    expect(await runtimeDependenciesReady(project, onFailure, choice)).toBe(false);
    expect(onFailure).toHaveBeenCalledWith('ModuleNotFoundError: missing runtime dependency');
    expect(await runtimeProjectReady(bundle, project, choice)).toBe(false);
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe(choice.variant === 'cpu' ? 'rocm' : 'cpu');
  },
);

it.each([CPU, HIP])(
  'uses the selected $variant ABI and imports to inspect an existing interpreter',
  async (choice) => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', choice.variant === 'cpu' ? 'rocm' : 'cpu');
    const { bundle, project } = await fixture();
    await interpreter(project);
    const run = runner(project);
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
      choice,
    );
    const syncIndex = run.mock.calls.findIndex(([, args]) => args[0] === 'sync');
    const probes = run.mock.calls.slice(0, syncIndex);
    expect(probes.length).toBeGreaterThan(0);
    expect(probes[0]![1][1]?.includes('validate_target()')).toBe(choice.variant === 'rocm');
    expect(probes[0]![1][1]?.includes('sys.version_info[:2] == (3, 11)')).toBe(
      choice.variant === 'cpu',
    );
    for (const [, , , env] of probes) expect(env?.OMNIVOICE_TORCH_VARIANT).toBe(choice.variant);
    const sync = run.mock.calls[syncIndex]![1];
    expect(sync[sync.indexOf('--python') + 1]).toBe(join(project, '.venv/Scripts/python.exe'));
  },
);

it('shares failed HIP verification across ready and compatible fallback only within one decision', async () => {
  const { bundle, project } = await fixture();
  await installRuntime(
    bundle,
    project,
    'uv',
    runner(project),
    new AbortController().signal,
    undefined,
    'global',
  );
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
  vi.mocked(execFile).mockClear();
  vi.mocked(execFile).mockImplementationOnce(((
    _command: unknown,
    _args: unknown,
    _options: unknown,
    callback: (error: Error | null, stdout: string, stderr: string) => void,
  ) => callback(Error('driver unavailable'), report, '')) as never);
  expect(await runtimeProjectReady(bundle, project, HIP)).toBe(false);
  expect(execFile).toHaveBeenCalledOnce();
  expect(await runtimeProjectReady(bundle, project, HIP)).toBe(true);
  expect(
    vi
      .mocked(execFile)
      .mock.calls.filter(([, args]) => (args as string[]).includes('--verify-only')),
  ).toHaveLength(2);
});

it('shares HIP native verification for one explicit readiness decision and rechecks later', async () => {
  const { bundle, project } = await fixture();
  await installRuntime(
    bundle,
    project,
    'uv',
    runner(project),
    new AbortController().signal,
    undefined,
    'global',
  );
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
  vi.mocked(execFile).mockClear();
  expect(await runtimeProjectReady(bundle, project, HIP)).toBe(true);
  expect(
    vi
      .mocked(execFile)
      .mock.calls.filter(([, args]) => (args as string[]).includes('--verify-only')),
  ).toHaveLength(1);
  expect(await runtimeProjectReady(bundle, project, CPU)).toBe(false);
  expect(await runtimeDependenciesReady(project, undefined, HIP)).toBe(true);
  expect(
    vi
      .mocked(execFile)
      .mock.calls.filter(([, args]) => (args as string[]).includes('--verify-only')),
  ).toHaveLength(2);
  expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('cpu');
});
