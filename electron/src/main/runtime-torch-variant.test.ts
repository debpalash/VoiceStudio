// @vitest-environment node
import { mkdtemp, mkdir, readFile, rm, writeFile, statfs } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CPU_TORCH_ARGS,
  CPU_TORCH_PINS,
  WIN_ARM64_PYTHON_REQUEST,
  cudaOnlyPackages,
  installRuntime,
  managedPythonRequest,
  nvidiaDriverPresent,
  resolveTorchVariant,
  runtimeCompatible,
  runtimePython,
  runtimeReady,
} from './runtime-project';

vi.mock('./runtime-download', () => ({ downloadRuntimeInstaller: vi.fn() }));
vi.mock('node:fs/promises', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:fs/promises')>()),
  statfs: vi.fn(async () => ({ bavail: 100 * 1024 ** 3, bsize: 1 })),
}));
// "No NVIDIA driver on this machine", whatever machine runs the tests.
vi.mock('node:fs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:fs')>()),
  existsSync: vi.fn(() => false),
}));

const LOCK = [
  'version = 1',
  '[[package]]',
  'name = "nvidia-cublas-cu12"',
  'version = "12.8.4.1"',
  '[[package]]',
  'name = "nvidia-cudnn-cu12"',
  'version = "9.10.2.21"',
  '[[package]]',
  'name = "nvidia-ml-py"',
  'version = "13.0"',
  '[[package]]',
  'name = "torch"',
  'dependencies = [',
  '    { name = "nvidia-cublas-cu12", marker = "sys_platform == \'linux\'" },',
  ']',
  '',
].join('\n');

const roots: string[] = [];
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'vs-torch-variant-'));
  roots.push(root);
  const bundle = join(root, 'bundle');
  const project = join(root, 'runtime');
  await mkdir(join(bundle, 'backend'), { recursive: true });
  await mkdir(join(bundle, 'frontend', 'dist'), { recursive: true });
  await mkdir(join(bundle, 'omnivoice'));
  for (const file of [
    'pyproject.toml',
    'README.md',
    'LICENSE',
    'backend/main.py',
    'frontend/dist/index.html',
    'omnivoice/__init__.py',
  ]) {
    await writeFile(join(bundle, file), file);
  }
  await writeFile(join(bundle, 'uv.lock'), LOCK);
  return { bundle, project };
}
async function interpreter(project: string) {
  await mkdir(dirname(runtimePython(project)), { recursive: true });
  await writeFile(runtimePython(project), 'interpreter');
  await writeFile(join(project, '.venv', 'pyvenv.cfg'), 'home = managed');
}
const fakeRun = (project: string) =>
  vi.fn(async (_command: string, _args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
    await interpreter(project);
  });
function host(platform: NodeJS.Platform, arch: typeof process.arch) {
  vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
  vi.spyOn(process, 'arch', 'get').mockReturnValue(arch);
}

beforeEach(() => {
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', '');
});
afterEach(async () => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.mocked(statfs).mockClear();
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe('resolveTorchVariant', () => {
  const noGpu = () => false;
  const gpu = () => true;
  it('installs CPU torch on Linux and Windows x64 hosts without an NVIDIA driver', () => {
    expect(resolveTorchVariant({}, 'linux', 'x64', noGpu)).toEqual({
      variant: 'cpu',
      explicit: false,
    });
    expect(resolveTorchVariant({}, 'win32', 'x64', noGpu).variant).toBe('cpu');
  });
  it('keeps the lock CUDA wheels whenever an NVIDIA driver is present', () => {
    expect(resolveTorchVariant({}, 'linux', 'x64', gpu).variant).toBe('default');
    expect(resolveTorchVariant({}, 'win32', 'x64', gpu).variant).toBe('default');
  });
  it('leaves macOS and Linux arm64 on their PyPI wheels', () => {
    for (const [platform, arch] of [
      ['darwin', 'arm64'],
      ['darwin', 'x64'],
      ['linux', 'arm64'],
    ] as const) {
      expect(resolveTorchVariant({}, platform, arch, noGpu).variant).toBe('default');
    }
  });
  it('always uses CPU torch on Windows on ARM, GPU probe or not', () => {
    expect(resolveTorchVariant({}, 'win32', 'arm64', gpu).variant).toBe('cpu');
  });
  it('honours an explicit variant over detection', () => {
    expect(
      resolveTorchVariant({ OMNIVOICE_TORCH_VARIANT: ' CUDA ' }, 'linux', 'x64', noGpu),
    ).toEqual({ variant: 'default', explicit: true });
    expect(resolveTorchVariant({ OMNIVOICE_TORCH_VARIANT: 'cpu' }, 'linux', 'x64', gpu)).toEqual({
      variant: 'cpu',
      explicit: true,
    });
    expect(resolveTorchVariant({ OMNIVOICE_TORCH_VARIANT: 'rocm' }, 'linux', 'x64', noGpu)).toEqual(
      { variant: 'rocm', explicit: true },
    );
  });
  it('ignores an explicit cpu request where the lock already ships CPU/MPS wheels', () => {
    expect(
      resolveTorchVariant({ OMNIVOICE_TORCH_VARIANT: 'cpu' }, 'darwin', 'arm64', noGpu).variant,
    ).toBe('default');
  });
  it('treats unknown values like auto', () => {
    expect(
      resolveTorchVariant({ OMNIVOICE_TORCH_VARIANT: 'auto' }, 'linux', 'x64', noGpu).variant,
    ).toBe('cpu');
  });
});

describe('nvidiaDriverPresent', () => {
  it('finds a Windows driver by its system DLLs', () => {
    const seen: string[] = [];
    const env = { SystemRoot: 'C:\\Windows' };
    expect(nvidiaDriverPresent('win32', (p) => (seen.push(p), p.endsWith('nvcuda.dll')), env)).toBe(
      true,
    );
    expect(nvidiaDriverPresent('win32', () => false, env)).toBe(false);
  });
  it('finds a Linux driver through procfs, device nodes or libcuda (incl. WSL)', () => {
    for (const marker of [
      '/proc/driver/nvidia/version',
      '/dev/nvidiactl',
      '/usr/lib/wsl/lib/libcuda.so.1',
    ]) {
      expect(nvidiaDriverPresent('linux', (p) => p === marker)).toBe(true);
    }
    expect(nvidiaDriverPresent('linux', () => false)).toBe(false);
  });
  it('is false on macOS', () => {
    expect(nvidiaDriverPresent('darwin', () => true)).toBe(false);
  });
});

describe('cudaOnlyPackages', () => {
  it('lists the nvidia runtime wheels but keeps the tiny NVML binding', () => {
    expect(cudaOnlyPackages(LOCK)).toEqual(['nvidia-cublas-cu12', 'nvidia-cudnn-cu12']);
  });
  it('does not match dependency edges or unrelated packages', () => {
    expect(cudaOnlyPackages('    { name = "nvidia-foo" },\nname = "numpy"\n')).toEqual([]);
  });
});

describe('managedPythonRequest', () => {
  it('requests the emulated x64 build on Windows on ARM only', () => {
    expect(managedPythonRequest('win32', 'arm64')).toBe(WIN_ARM64_PYTHON_REQUEST);
    expect(managedPythonRequest('win32', 'x64')).toBe('3.11');
    expect(managedPythonRequest('darwin', 'arm64')).toBe('3.11');
    expect(managedPythonRequest('linux', 'x64')).toBe('3.11');
  });
});

describe('CPU-only runtime install', () => {
  it('never syncs CUDA torch or nvidia wheels, then lays down the CPU build', async () => {
    host('linux', 'x64');
    const { bundle, project } = await fixture();
    const run = fakeRun(project);
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);

    const calls = run.mock.calls.map(([, args]) => args);
    const syncIndex = calls.findIndex((args) => args[0] === 'sync');
    const pipIndex = calls.findIndex((args) => args[0] === 'pip');
    expect(syncIndex).toBeGreaterThanOrEqual(0);
    expect(pipIndex).toBeGreaterThan(syncIndex);
    const sync = calls[syncIndex]!;
    const skipped = sync.flatMap((arg, i) => (arg === '--no-install-package' ? [sync[i + 1]] : []));
    expect(skipped).toEqual(
      expect.arrayContaining(['torch', 'torchaudio', 'torchvision', 'nvidia-cublas-cu12']),
    );
    expect(skipped).not.toContain('nvidia-ml-py');
    expect(calls[pipIndex]).toEqual([
      'pip',
      'install',
      '--python',
      runtimePython(project),
      ...CPU_TORCH_PINS,
      ...CPU_TORCH_ARGS,
    ]);
    expect(CPU_TORCH_ARGS.join(' ')).toContain('download.pytorch.org/whl/cpu');
  });
  it('needs 5 GiB instead of 9 GiB and says so', async () => {
    host('win32', 'x64');
    const { bundle, project } = await fixture();
    vi.mocked(statfs).mockResolvedValueOnce({ bavail: 4 * 1024 ** 3, bsize: 1 } as Awaited<
      ReturnType<typeof statfs>
    >);
    const run = vi.fn();
    await expect(
      installRuntime(bundle, project, 'uv', run, new AbortController().signal),
    ).rejects.toMatchObject({ code: 'ENOSPC', requiredGib: 5 });
    expect(run).not.toHaveBeenCalled();

    vi.mocked(statfs).mockResolvedValueOnce({ bavail: 6 * 1024 ** 3, bsize: 1 } as Awaited<
      ReturnType<typeof statfs>
    >);
    await installRuntime(bundle, project, 'uv', fakeRun(project), new AbortController().signal);
  });
  it('keeps the lock wheels on an NVIDIA host', async () => {
    host('linux', 'x64');
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cuda');
    const { bundle, project } = await fixture();
    const run = fakeRun(project);
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync')?.[1];
    expect(sync).not.toContain('--no-install-package');
    expect(run.mock.calls.some(([, args]) => args[0] === 'pip')).toBe(false);
  });
  it('repairs a broken CPU torch through pip, not through the skipped lock entry', async () => {
    host('linux', 'x64');
    const { bundle, project } = await fixture();
    await interpreter(project);
    let repaired = false;
    const run = vi.fn(async (command: string, args: string[]) => {
      if (args[0] === 'pip') repaired = true;
      if (!repaired && command === runtimePython(project) && args[0] === '-c') {
        if (String(args[1]).includes('torch')) throw new Error('missing libtorch_cpu');
      }
    });
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync')?.[1];
    expect(sync).not.toContain('--reinstall-package');
    const pip = run.mock.calls.find(([, args]) => args[0] === 'pip')?.[1];
    expect(pip).toEqual(expect.arrayContaining(['--reinstall-package', 'torch', 'torchaudio']));
  });
});

describe('Windows on ARM runtime install', () => {
  it('asks uv for the x64 interpreter and the CPU torch build', async () => {
    host('win32', 'arm64');
    const { bundle, project } = await fixture();
    const run = fakeRun(project);
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync')?.[1] ?? [];
    expect(sync).toEqual(expect.arrayContaining(['--managed-python', '--python']));
    expect(sync[sync.indexOf('--python') + 1]).toBe(WIN_ARM64_PYTHON_REQUEST);
    expect(run.mock.calls.some(([, args]) => args[0] === 'pip')).toBe(true);
  });
  it('rejects a native ARM64 interpreter left behind by an earlier attempt', async () => {
    host('win32', 'arm64');
    const { bundle, project } = await fixture();
    await interpreter(project);
    const run = vi.fn(async (command: string, args: string[]) => {
      // The reuse probe is the only interpreter call that mentions the machine type.
      if (command === runtimePython(project) && String(args[1]).includes('platform.machine')) {
        throw new Error('AssertionError');
      }
    });
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync')?.[1] ?? [];
    expect(sync).toContain('--managed-python');
    expect(sync).not.toContain(runtimePython(project));
  });
});

describe('runtime markers across torch flavours', () => {
  it('keeps a pre-existing CUDA runtime on a host that now infers CPU', async () => {
    host('linux', 'x64');
    const { bundle, project } = await fixture();
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cuda');
    await installRuntime(bundle, project, 'uv', fakeRun(project), new AbortController().signal);
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', '');
    expect(await runtimeReady(bundle, project)).toBe(true);
    expect(await runtimeCompatible(bundle, project)).toBe(true);
    // ...but an explicit CPU request is a deliberate reinstall.
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
    expect(await runtimeReady(bundle, project)).toBe(false);
  });
  it('flags a CPU runtime stale once the host gains an NVIDIA driver', async () => {
    host('linux', 'x64');
    const { bundle, project } = await fixture();
    await installRuntime(bundle, project, 'uv', fakeRun(project), new AbortController().signal);
    expect(await readFile(join(project, '.runtime-ready'), 'utf8')).toHaveLength(64);
    expect(await runtimeReady(bundle, project)).toBe(true);
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cuda');
    expect(await runtimeReady(bundle, project)).toBe(false);
    expect(await runtimeCompatible(bundle, project)).toBe(false);
  });
});
