// @vitest-environment node
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  legacyNvidiaFromSmi,
  nvidiaSmiCommand,
  LEGACY_CUDA_MIN_DRIVER,
} from '../../scripts/torch-variant.mjs';
import {
  CPU_TORCH_ARGS,
  CPU_TORCH_PINS,
  LEGACY_CUDA_ARGS,
  LEGACY_CUDA_FALLBACK_MARKER,
  LEGACY_CUDA_INDEX,
  LEGACY_CUDA_PINS,
  LEGACY_CUDA_PROBE,
  LEGACY_CUDA_PROBE_PREFIX,
  installRuntime,
  parseLegacyCudaProbe,
  resolveTorchVariant,
  runtimePython,
  runtimeReady,
} from './runtime-project';

const hoisted = vi.hoisted(() => ({ legacy: { legacy: true, reason: 'test' } }));
vi.mock('./runtime-download', () => ({ downloadRuntimeInstaller: vi.fn() }));
vi.mock('node:fs/promises', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:fs/promises')>()),
  statfs: vi.fn(async () => ({ bavail: 100 * 1024 ** 3, bsize: 1 })),
}));
vi.mock('../../scripts/torch-variant.mjs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../scripts/torch-variant.mjs')>()),
  nvidiaDriverPresent: () => true,
  legacyNvidiaCard: () => hoisted.legacy,
}));

describe('legacyNvidiaFromSmi', () => {
  it('selects pre-Volta cards with a recent enough driver', () => {
    expect(legacyNvidiaFromSmi('6.1, 580.95.05\n').legacy).toBe(true);
    expect(legacyNvidiaFromSmi('5.2, 528.33\n6.1, 528.33').legacy).toBe(true);
  });
  it('applies the lower bound to every card, not only the highest', () => {
    expect(legacyNvidiaFromSmi('3.5, 580.0\n6.1, 580.0').legacy).toBe(false);
    expect(legacyNvidiaFromSmi('6.1, 580.0\n3.5, 580.0').legacy).toBe(false);
  });
  it('finds nvidia-smi in the Windows driver folders when PATH lacks it', () => {
    const sys32 = 'C:\\Windows\\System32\\nvidia-smi.exe';
    expect(
      nvidiaSmiCommand('win32', (p: string) => p === sys32, { SystemRoot: 'C:\\Windows' }),
    ).toBe(sys32);
    expect(nvidiaSmiCommand('win32', () => false, {})).toBe('nvidia-smi');
    expect(nvidiaSmiCommand('linux', () => true, {})).toBe('nvidia-smi');
  });
  it('leaves Volta and newer, mixed hosts and Kepler alone', () => {
    expect(legacyNvidiaFromSmi('7.0, 580.0').legacy).toBe(false);
    expect(legacyNvidiaFromSmi('12.0, 580.0').legacy).toBe(false);
    expect(legacyNvidiaFromSmi('6.1, 580.0\n8.6, 580.0').legacy).toBe(false);
    expect(legacyNvidiaFromSmi('3.5, 470.0').legacy).toBe(false);
  });
  it('requires a driver new enough for CUDA 12.6 and a readable answer', () => {
    expect(legacyNvidiaFromSmi(`6.1, ${LEGACY_CUDA_MIN_DRIVER - 1}.0`).legacy).toBe(false);
    expect(legacyNvidiaFromSmi(`6.1, ${LEGACY_CUDA_MIN_DRIVER}.60`).legacy).toBe(true);
    expect(legacyNvidiaFromSmi('').legacy).toBe(false);
    expect(legacyNvidiaFromSmi('[N/A], 580.0').legacy).toBe(false);
  });
  it('treats a failing nvidia-smi as not legacy', async () => {
    const { legacyNvidiaCard } = await vi.importActual<
      typeof import('../../scripts/torch-variant.mjs')
    >('../../scripts/torch-variant.mjs');
    const run = () => {
      throw new Error('no such command');
    };
    expect(legacyNvidiaCard({ run, cache: false }).legacy).toBe(false);
    expect(legacyNvidiaCard({ run: () => '6.1, 580.0', cache: false }).legacy).toBe(true);
  });
});

describe('resolveTorchVariant for legacy NVIDIA cards', () => {
  const gpu = () => true;
  const pascal = () => true;
  const modern = () => false;
  it('picks cu126 on Linux and Windows x64 for a pre-Volta card', () => {
    for (const platform of ['linux', 'win32'] as const) {
      expect(resolveTorchVariant({}, platform, 'x64', gpu, pascal)).toEqual({
        variant: 'cu126',
        explicit: false,
      });
    }
  });
  it('keeps the default build for Volta and newer', () => {
    expect(resolveTorchVariant({}, 'linux', 'x64', gpu, modern).variant).toBe('default');
  });
  it('keeps CPU when there is no NVIDIA driver, whatever the probe says', () => {
    expect(resolveTorchVariant({}, 'linux', 'x64', () => false, pascal).variant).toBe('cpu');
  });
  it('lets an explicit cuda or cpu request win', () => {
    expect(
      resolveTorchVariant({ OMNIVOICE_TORCH_VARIANT: 'cuda' }, 'linux', 'x64', gpu, pascal).variant,
    ).toBe('default');
    expect(
      resolveTorchVariant({ OMNIVOICE_TORCH_VARIANT: 'cpu' }, 'linux', 'x64', gpu, pascal).variant,
    ).toBe('cpu');
  });
  it('never applies on macOS or Linux arm64', () => {
    expect(resolveTorchVariant({}, 'darwin', 'arm64', gpu, pascal).variant).toBe('default');
    expect(resolveTorchVariant({}, 'linux', 'arm64', gpu, pascal).variant).toBe('default');
  });
});

describe('legacy CUDA pins', () => {
  it('are the cu126 flavour of the locked stack, from the sanctioned PyTorch index', () => {
    expect(LEGACY_CUDA_PINS.map((pin) => pin.replace('+cu126', '+cpu'))).toEqual([
      ...CPU_TORCH_PINS,
    ]);
    expect(LEGACY_CUDA_INDEX).toBe('https://download.pytorch.org/whl/cu126');
    expect(LEGACY_CUDA_ARGS).toContain(LEGACY_CUDA_INDEX);
  });
});

const roots: string[] = [];
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'vs-legacy-cuda-'));
  roots.push(root);
  const bundle = join(root, 'bundle');
  const project = join(root, 'runtime');
  await mkdir(join(bundle, 'backend'), { recursive: true });
  await mkdir(join(bundle, 'config'), { recursive: true });
  await mkdir(join(bundle, 'frontend', 'dist'), { recursive: true });
  await mkdir(join(bundle, 'omnivoice'));
  await mkdir(join(bundle, 'scripts'));
  for (const file of [
    'pyproject.toml',
    'README.md',
    'LICENSE',
    'backend/main.py',
    'config/app.json',
    'frontend/dist/index.html',
    'omnivoice/__init__.py',
    'scripts/windows-rocm-recipe.json',
    'scripts/smoke_windows_rocm.py',
  ]) {
    await writeFile(join(bundle, file), file);
  }
  await writeFile(
    join(bundle, 'uv.lock'),
    [
      'version = 1',
      '[[package]]',
      'name = "nvidia-cublas-cu12"',
      'version = "12.8.4.1"',
      '[[package]]',
      'name = "torch"',
      '',
    ].join('\n'),
  );
  return { bundle, project };
}
const probeLine = (ok: boolean, reason = '') =>
  `${LEGACY_CUDA_PROBE_PREFIX}${JSON.stringify({ ok, reason })}\n`;
const runner = (project: string, verdict: string) =>
  vi.fn(async (_command: string, args: string[], _cwd?: string) => {
    await mkdir(dirname(runtimePython(project)), { recursive: true });
    await writeFile(runtimePython(project), 'interpreter');
    await writeFile(join(project, '.venv', 'pyvenv.cfg'), 'home = managed');
    return args[0] === '-c' && args[1] === LEGACY_CUDA_PROBE ? verdict : undefined;
  });

beforeEach(() => {
  hoisted.legacy = { legacy: true, reason: 'test' };
  vi.spyOn(process, 'platform', 'get').mockReturnValue('linux');
  vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true })),
  );
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', '');
});
afterEach(async () => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

const pipInstalls = (run: ReturnType<typeof runner>) =>
  run.mock.calls
    .map(([, args]) => args)
    .filter((args) => args[0] === 'pip' && args[1] === 'install');

describe('legacy CUDA runtime install', () => {
  it('syncs without the lock torch, installs cu126 on top and stamps the runtime', async () => {
    const { bundle, project } = await fixture();
    const run = runner(project, probeLine(true));
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    const calls = run.mock.calls.map(([, args]) => args);
    const sync = calls.find((args) => args[0] === 'sync')!;
    const skipped = sync.flatMap((arg, i) => (arg === '--no-install-package' ? [sync[i + 1]] : []));
    expect(skipped).toEqual(expect.arrayContaining(['torch', 'nvidia-cublas-cu12']));
    expect(sync).toContain('--inexact');
    expect(calls.some((args) => args[0] === 'pip' && args[1] === 'uninstall')).toBe(false);
    expect(pipInstalls(run)).toEqual([
      [
        'pip',
        'install',
        '--python',
        runtimePython(project),
        ...LEGACY_CUDA_PINS,
        ...LEGACY_CUDA_ARGS,
      ],
    ]);
    expect(await readFile(join(project, '.runtime-torch'), 'utf8')).toBe('cu126');
    await expect(readFile(join(project, LEGACY_CUDA_FALLBACK_MARKER))).rejects.toThrow();
    expect(await runtimeReady(bundle, project)).toBe(true);
  });

  it('falls back to the CPU build, logs why, and does not loop on the next launch', async () => {
    const { bundle, project } = await fixture();
    const reason = 'no kernels for sm_61 in sm_70';
    const run = runner(project, probeLine(false, reason));
    const log = vi.fn();
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
      {
        log,
      },
    );
    expect(log).toHaveBeenCalledWith(expect.stringContaining(reason));
    const installs = pipInstalls(run);
    expect(installs).toHaveLength(2);
    expect(installs[1]).toEqual(expect.arrayContaining([...CPU_TORCH_PINS, ...CPU_TORCH_ARGS]));
    expect(await readFile(join(project, '.runtime-torch'), 'utf8')).toBe('cpu');
    expect(await readFile(join(project, LEGACY_CUDA_FALLBACK_MARKER), 'utf8')).toBe(reason);
    expect(await runtimeReady(bundle, project)).toBe(true);
  });

  it('falls back when the cu126 install itself fails', async () => {
    const { bundle, project } = await fixture();
    const base = runner(project, probeLine(true));
    const run = vi.fn(async (command: string, args: string[], cwd?: string) => {
      if (args[0] === 'pip' && args.includes(LEGACY_CUDA_PINS[0])) throw new Error('404 cu126');
      return base(command, args, cwd);
    });
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    expect(await readFile(join(project, '.runtime-torch'), 'utf8')).toBe('cpu');
    expect(await readFile(join(project, LEGACY_CUDA_FALLBACK_MARKER), 'utf8')).toContain(
      '404 cu126',
    );
  });

  it('scrubs home paths from the persisted fallback reason', async () => {
    const { bundle, project } = await fixture();
    const run = runner(project, probeLine(false, 'failed in /Users/alice/venv/torch'));
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    expect(await readFile(join(project, LEGACY_CUDA_FALLBACK_MARKER), 'utf8')).not.toContain(
      'alice',
    );
  });

  it('clears a stale fallback marker when the install is any other variant', async () => {
    const { bundle, project } = await fixture();
    await mkdir(project, { recursive: true });
    await writeFile(join(project, LEGACY_CUDA_FALLBACK_MARKER), 'old reason');
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cuda');
    await installRuntime(bundle, project, 'uv', runner(project, ''), new AbortController().signal);
    await expect(readFile(join(project, LEGACY_CUDA_FALLBACK_MARKER))).rejects.toThrow();
  });

  it('flags a cu128 runtime stale on a legacy host so the next setup switches it', async () => {
    const { bundle, project } = await fixture();
    hoisted.legacy = { legacy: false, reason: 'modern' };
    await installRuntime(bundle, project, 'uv', runner(project, ''), new AbortController().signal);
    expect(await runtimeReady(bundle, project)).toBe(true);
    hoisted.legacy = { legacy: true, reason: 'pascal' };
    expect(await runtimeReady(bundle, project)).toBe(false);
  });
});

describe('parseLegacyCudaProbe', () => {
  it('fails closed on missing or unreadable output', () => {
    expect(parseLegacyCudaProbe(undefined).ok).toBe(false);
    expect(parseLegacyCudaProbe(`${LEGACY_CUDA_PROBE_PREFIX}{nope`).ok).toBe(false);
    expect(parseLegacyCudaProbe(probeLine(true)).ok).toBe(true);
  });
});

const python = spawnSync('python3', ['--version']).status === 0 ? 'python3' : null;
function runProbe(torch: string) {
  const code = `import sys, types\n${torch}\nexec(${JSON.stringify(LEGACY_CUDA_PROBE)})`;
  return parseLegacyCudaProbe(spawnSync(python!, ['-I', '-c', code], { encoding: 'utf8' }).stdout);
}
const fakeTorch = (cuda: string | null, available: boolean, arch: string[], caps: number[][]) => `
m = types.ModuleType('torch')
m.version = types.SimpleNamespace(cuda=${cuda ? `'${cuda}'` : 'None'})
m.cuda = types.SimpleNamespace(
    is_available=lambda: ${available ? 'True' : 'False'},
    get_arch_list=lambda: ${JSON.stringify(arch)},
    device_count=lambda: ${caps.length},
    get_device_capability=lambda i: ${JSON.stringify(caps)}[i],
)
sys.modules['torch'] = m
`;
describe.skipIf(!python)('LEGACY_CUDA_PROBE', () => {
  const cu126 = [
    'sm_50',
    'sm_60',
    'sm_61',
    'sm_70',
    'sm_75',
    'sm_80',
    'sm_86',
    'sm_90',
    'compute_90',
  ];
  const cu128 = ['sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120', 'compute_120'];
  it('accepts a cu126 build on a GTX 1070 (sm_61)', () => {
    expect(runProbe(fakeTorch('12.6', true, cu126, [[6, 1]])).ok).toBe(true);
  });
  it('rejects a build without the card kernels, without CUDA, or without a device', () => {
    const missing = runProbe(fakeTorch('12.8', true, cu128, [[6, 1]]));
    expect(missing.ok).toBe(false);
    expect(missing.reason).toContain('sm_61');
    expect(runProbe(fakeTorch(null, false, [], []))).toMatchObject({ ok: false });
    expect(runProbe(fakeTorch('12.6', false, cu126, []))).toMatchObject({ ok: false });
  });
  it('reports a missing torch instead of throwing', () => {
    expect(runProbe('sys.modules["torch"] = None').ok).toBe(false);
  });
});

describe('non-blocking GPU probe', () => {
  it('fills the cache asynchronously and the sync reader then never spawns', async () => {
    const real = await vi.importActual<typeof import('../../scripts/torch-variant.mjs')>(
      '../../scripts/torch-variant.mjs',
    );
    real.resetLegacyNvidiaProbe();
    const async = vi.fn(
      (_c: string, _a: string[], _o: object, cb: (e: Error | null, s: string) => void) =>
        cb(null, '6.1, 580.0\n'),
    );
    const sync = vi.fn(() => {
      throw new Error('sync nvidia-smi must not run once primed');
    });
    expect((await real.primeLegacyNvidiaProbe({ run: async })).legacy).toBe(true);
    expect(real.legacyNvidiaCard({ run: sync }).legacy).toBe(true);
    expect(sync).not.toHaveBeenCalled();
    await real.primeLegacyNvidiaProbe({ run: async });
    expect(async).toHaveBeenCalledTimes(1);
    real.resetLegacyNvidiaProbe();
    const failing = (
      _c: string,
      _a: string[],
      _o: object,
      cb: (e: Error | null, s: string) => void,
    ) => cb(new Error('missing'), '');
    expect((await real.primeLegacyNvidiaProbe({ run: failing })).legacy).toBe(false);
    real.resetLegacyNvidiaProbe();
  });
  it('startup entry points prime the probe before resolving, only for NVIDIA hosts', async () => {
    const { primeTorchVariantProbe } = await import('./runtime-project');
    const prime = vi.fn(async () => undefined);
    await primeTorchVariantProbe('linux', 'x64', () => true, prime);
    expect(prime).toHaveBeenCalledTimes(1);
    await primeTorchVariantProbe('linux', 'x64', () => false, prime);
    await primeTorchVariantProbe('darwin', 'arm64', () => true, prime);
    await primeTorchVariantProbe('win32', 'arm64', () => true, prime);
    expect(prime).toHaveBeenCalledTimes(1);
    // `auto` and an empty value reach the sync reader too, so they prime as well.
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'AUTO');
    await primeTorchVariantProbe('linux', 'x64', () => true, prime);
    expect(prime).toHaveBeenCalledTimes(2);
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cuda');
    await primeTorchVariantProbe('linux', 'x64', () => true, prime);
    expect(prime).toHaveBeenCalledTimes(2);
  });
  it('installRuntime and runtimeReady prime before the first variant resolution', async () => {
    const order: string[] = [];
    const { readFileSync } = await import('node:fs');
    const src = readFileSync(new URL('./runtime-project.ts', import.meta.url), 'utf8');
    for (const fn of ['runtimeReady', 'runtimeCompatible', 'installRuntime']) {
      const body = src.slice(src.indexOf(`export async function ${fn}(`));
      order.push(
        `${fn}:${body.indexOf('await primeTorchVariantProbe()') < body.indexOf('resolveTorchVariant(')}`,
      );
    }
    expect(order).toEqual(['runtimeReady:true', 'runtimeCompatible:true', 'installRuntime:true']);
    const backend = readFileSync(new URL('./backend.ts', import.meta.url), 'utf8');
    const start = backend.slice(backend.indexOf('async start(): Promise<void> {'));
    expect(start.indexOf('await primeTorchVariantProbe()')).toBeGreaterThan(0);
    expect(start.indexOf('await primeTorchVariantProbe()')).toBeLessThan(
      start.indexOf('resolveTorchVariant'),
    );
  });
});
