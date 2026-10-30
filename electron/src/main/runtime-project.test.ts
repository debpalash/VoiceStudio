// @vitest-environment node
import { existsSync } from 'node:fs';
import { cp, mkdtemp, mkdir, readFile, rm, writeFile, statfs, utimes } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { downloadRuntimeInstaller } from './runtime-download';
import { createHash } from 'node:crypto';
import {
  httpsRangeTransport,
  PREFETCH_MIN_BYTES,
  RangeUnsupportedError,
  type RangeTransport,
} from './wheel-prefetch';
import * as pthAscii from './pth-ascii';
import { PYPI_SPEED_SAMPLE_BYTES } from './pypi-mirror';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import {
  installRuntime,
  promoteLegacyRuntimeCaches,
  CUDNN8_COMPAT_PIN,
  MISSING_SYSTEM_LIBRARY_CODE,
  OUTDATED_SYSTEM_LIBRARY_CODE,
  RUNTIME_FILES_LOCKED_CODE,
  msvcp140Outdated,
  parseNativeDiagnosis,
  NATIVE_DIAGNOSTIC_PREFIX,
  ROCM_TORCH_INDEX,
  ROCM_TORCH_PINS,
  ROCM_TORCH_PROBE,
  ROCM_TORCH_PROBE_FAILURE,
  rocmTorchInstallArgs,
  rocmTorchOptIn,
  RUNTIME_IMPORT_PROBE,
  RUNTIME_NATIVE_DIAGNOSTIC_PROBE,
  lastImportAttempted,
  RUNTIME_NATIVE_IMPORT_PROBE,
  RUNTIME_REPAIR_PACKAGES,
  clearCtranslate2ExecutableStack,
  runtimePython,
  runtimeReady,
  runtimeCompatible,
  runtimeInstallInterrupted,
  stageRuntimeSources,
  UV_VERSION,
} from './runtime-project';

vi.mock('./runtime-download', () => ({ downloadRuntimeInstaller: vi.fn() }));
vi.mock('./wheel-prefetch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./wheel-prefetch')>();
  return { ...actual, httpsRangeTransport: vi.fn(actual.httpsRangeTransport) };
});

vi.mock('node:fs/promises', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:fs/promises')>()),
  statfs: vi.fn(async () => ({ bavail: 100 * 1024 ** 3, bsize: 1 })),
}));
const roots: string[] = [];
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'vs-runtime-test-'));
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
    'uv.lock',
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
  return { bundle, project };
}
async function interpreter(project: string) {
  await mkdir(dirname(runtimePython(project)), { recursive: true });
  await writeFile(runtimePython(project), 'interpreter');
  await writeFile(join(project, '.venv', 'pyvenv.cfg'), 'home = managed');
}
function forcePlatform(platform: NodeJS.Platform) {
  vi.spyOn(process, 'platform', 'get').mockReturnValue(platform);
}
beforeEach(() => {
  // Runtime fixtures must not depend on live region-probe latency.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true })),
  );
  // Installation fixtures exercise a supported host; the Intel case overrides it.
  if (process.platform === 'darwin') vi.spyOn(process, 'arch', 'get').mockReturnValue('arm64');
  // The baseline fixtures model an NVIDIA host (the lock's default wheels) whatever
  // the machine running the tests has; CPU/ARM hosts are pinned explicitly below.
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cuda');
});
afterEach(async () => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.mocked(statfs).mockClear();
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe('packaged runtime setup', () => {
  it.each(['win32', 'linux'] as const)(
    'does not import torch for the CUDA compatibility probe on a %s CPU runtime',
    async (platform) => {
      forcePlatform(platform);
      vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
      vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
      const { bundle, project } = await fixture();
      const sitePackages = join(project, '.venv', 'Lib', 'site-packages');
      const run = vi.fn(async (_command: string, args: string[]) => {
        if (args[0] === 'sync') await interpreter(project);
        if (args[1]?.includes('VOICESTUDIO_CUDNN8_PROBE=')) {
          if (args[1].includes('import torch')) {
            throw new Error('Runtime setup exited with code 3221225477');
          }
          return `VOICESTUDIO_CUDNN8_PROBE=${JSON.stringify({ device: 'none', sitePackages })}\n`;
        }
        return undefined;
      });
      await installRuntime(
        bundle,
        project,
        'uv',
        run,
        new AbortController().signal,
        undefined,
        'global',
      );
      expect(run.mock.calls.some(([, args]) => args.includes(CUDNN8_COMPAT_PIN))).toBe(false);
      expect(run.mock.calls.some(([, args]) => args[1] === RUNTIME_IMPORT_PROBE)).toBe(true);
      expect(await runtimeReady(bundle, project)).toBe(true);
    },
  );
  it('names the module whose native import crashed the final verification (#3293)', async () => {
    forcePlatform('win32');
    vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
    const { bundle, project } = await fixture();
    const sitePackages = join(project, '.venv', 'Lib', 'site-packages');
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[0] === 'sync') await interpreter(project);
      if (args[1]?.includes('VOICESTUDIO_CUDNN8_PROBE=')) {
        return `VOICESTUDIO_CUDNN8_PROBE=${JSON.stringify({ device: 'none', sitePackages })}\n`;
      }
      if (args[1] === RUNTIME_IMPORT_PROBE) {
        throw Object.assign(new Error('Runtime setup exited with code 3221225477'), {
          code: 3221225477,
          stdout: 'VOICESTUDIO_IMPORT=fastapi\nVOICESTUDIO_IMPORT=faster_whisper\n',
        });
      }
      return undefined;
    });
    await expect(
      installRuntime(bundle, project, 'uv', run, new AbortController().signal, undefined, 'global'),
    ).rejects.toMatchObject({
      message: expect.stringContaining('while importing "faster_whisper"'),
      code: 3221225477,
    });
  });
  it.each([
    ['after printing its result', true],
    ['before printing anything', false],
  ])(
    'keeps setting up when the CUDA compatibility probe crashes natively %s',
    async (_name, printed) => {
      forcePlatform('win32');
      vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
      vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cuda');
      const { bundle, project } = await fixture();
      const sitePackages = join(project, '.venv', 'Lib', 'site-packages');
      const run = vi.fn(async (_command: string, args: string[]) => {
        if (args[0] === 'sync') await interpreter(project);
        if (args[1]?.includes('VOICESTUDIO_CUDNN8_PROBE=')) {
          throw Object.assign(new Error('Runtime setup exited with code 3221225477'), {
            stdout: printed
              ? `VOICESTUDIO_CUDNN8_PROBE=${JSON.stringify({ device: 'none', sitePackages })}\n`
              : '',
          });
        }
        return undefined;
      });
      await installRuntime(
        bundle,
        project,
        'uv',
        run,
        new AbortController().signal,
        undefined,
        'global',
      );
      expect(run.mock.calls.some(([, args]) => args[1] === RUNTIME_IMPORT_PROBE)).toBe(true);
      expect(await runtimeReady(bundle, project)).toBe(true);
    },
  );
  it.each([0, 1])(
    'verifies the runtime when .pth repair attempt %i fails',
    async (failedAttempt) => {
      const { bundle, project } = await fixture();
      let attempt = 0;
      const heal = vi.spyOn(pthAscii, 'asciiSafePthFiles').mockImplementation(async () => {
        if (attempt++ === failedAttempt)
          throw Object.assign(new Error('file locked'), { code: 'EPERM' });
        return [];
      });
      const run = vi.fn(async () => {});

      await installRuntime(bundle, project, 'uv', run, new AbortController().signal);

      expect(heal).toHaveBeenCalledTimes(2);
      expect(run).toHaveBeenCalledWith(
        runtimePython(project),
        ['-c', RUNTIME_IMPORT_PROBE],
        project,
        expect.objectContaining({ OMNIVOICE_TORCH_VARIANT: 'cuda' }),
        undefined,
      );
      expect(await readFile(join(project, '.runtime-ready'), 'utf8')).not.toBe('');
    },
  );

  it('does not mark a runtime ready when verification fails after a .pth repair error', async () => {
    const { bundle, project } = await fixture();
    vi.spyOn(pthAscii, 'asciiSafePthFiles').mockRejectedValue(new Error('file locked'));
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args.includes(RUNTIME_IMPORT_PROBE)) throw new Error('Python import failed');
    });

    await expect(
      installRuntime(bundle, project, 'uv', run, new AbortController().signal),
    ).rejects.toThrow('Python import failed');
    await expect(readFile(join(project, '.runtime-ready'))).rejects.toMatchObject({
      code: 'ENOENT',
    });
  });

  it('rejects Intel Macs before creating files or downloading dependencies', async () => {
    const { bundle, project } = await fixture();
    const platform = vi.spyOn(process, 'platform', 'get').mockReturnValue('darwin');
    const arch = vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
    const run = vi.fn();
    try {
      await expect(
        installRuntime(bundle, project, null, run, new AbortController().signal),
      ).rejects.toMatchObject({ code: 'INTEL_MAC_UNSUPPORTED' });
      expect(run).not.toHaveBeenCalled();
      expect(statfs).not.toHaveBeenCalled();
    } finally {
      platform.mockRestore();
      arch.mockRestore();
    }
  });

  it('downloads the first-run installer with the same proxy environment as uv', async () => {
    const { bundle, project } = await fixture();
    vi.stubEnv('HTTPS_PROXY', 'socks5h://127.0.0.1:1080');
    vi.mocked(downloadRuntimeInstaller).mockResolvedValue('# installer');
    const run = vi.fn(async () => {});
    const signal = new AbortController().signal;
    await installRuntime(bundle, project, null, run, signal, undefined, 'global');
    expect(downloadRuntimeInstaller).toHaveBeenCalledWith(
      expect.stringContaining('https://astral.sh/uv/'),
      expect.objectContaining({ HTTPS_PROXY: 'socks5h://127.0.0.1:1080' }),
      signal,
    );
    const script = join(
      project,
      '.tools',
      process.platform === 'win32' ? 'install.ps1' : 'install.sh',
    );
    expect(await readFile(script, 'utf8')).toBe('# installer');
    expect(run.mock.calls[0]).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ HTTPS_PROXY: 'socks5h://127.0.0.1:1080' }),
      ]),
    );
  });
  it('never reuses an interrupted install as a compatible Tauri environment', async () => {
    const { bundle, project } = await fixture();
    await stageRuntimeSources(bundle, project);
    await interpreter(project);
    expect(await runtimeCompatible(bundle, project)).toBe(true);
    await expect(
      installRuntime(
        bundle,
        project,
        'uv',
        async () => {
          throw new Error('interrupted');
        },
        new AbortController().signal,
        undefined,
        'global',
      ),
    ).rejects.toThrow('interrupted');
    expect(await runtimeInstallInterrupted(project)).toBe(true);
    expect(await runtimeReady(bundle, project)).toBe(false);
    expect(await runtimeCompatible(bundle, project)).toBe(false);
    await installRuntime(
      bundle,
      project,
      'uv',
      async () => {},
      new AbortController().signal,
      undefined,
      'global',
    );
    expect(await runtimeReady(bundle, project)).toBe(true);
    expect(await runtimeCompatible(bundle, project)).toBe(true);
    expect(await runtimeInstallInterrupted(project)).toBe(false);
  });
  it('requires successful installation, a complete venv and the current dependency graph', async () => {
    const { bundle, project } = await fixture();
    expect(await runtimeReady(bundle, project)).toBe(false);
    const run = vi.fn(
      async (_command: string, _args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
        await interpreter(project);
      },
    );
    const phase = vi.fn();
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal, phase);
    expect(phase.mock.calls.map(([value]) => value)).toEqual([
      'checking',
      'installing_deps',
      'verifying',
    ]);
    // The dry run that plans multi-connection downloads (#3411), the sync, the
    // import check, and off macOS the CUDA compatibility probe.
    expect(run.mock.calls).toHaveLength(process.platform === 'darwin' ? 3 : 4);
    expect(run.mock.calls[0]![1]).toEqual(expect.arrayContaining(['-v', 'sync', '--dry-run']));
    expect(await runtimeReady(bundle, project)).toBe(true);
    await writeFile(join(bundle, 'uv.lock'), 'updated dependencies');
    expect(await runtimeReady(bundle, project)).toBe(false);
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    await rm(join(project, '.venv', 'pyvenv.cfg'));
    expect(await runtimeReady(bundle, project)).toBe(false);
  });
  it('keeps reusable uv downloads outside the replaceable Python project', async () => {
    const { bundle, project } = await fixture();
    const run = vi.fn(
      async (_command: string, _args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
        await interpreter(project);
      },
    );

    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
    );

    const syncEnv = run.mock.calls[0]?.[3];
    expect(syncEnv?.UV_CACHE_DIR).toBe(join(project, '..', '.uv-cache'));
    for (const key of ['TEMP', 'TMP', 'TMPDIR'])
      expect(syncEnv?.[key]).toBe(join(project, '..', '.tmp'));
    expect(syncEnv?.UV_PYTHON_INSTALL_DIR).toBe(join(project, '..', '.python'));
  });
  // #3411: uv cannot resume, and a cancelled uv leaves its partial wheel in the
  // cache. Retry must drop those leftovers yet reuse every finished download.
  it('drops partial downloads from a cancelled attempt and keeps finished wheels', async () => {
    const { bundle, project } = await fixture();
    const cache = join(project, '..', '.uv-cache');
    await mkdir(join(cache, '.tmpHZ1msW', 'torch'), { recursive: true });
    await writeFile(join(cache, '.tmpHZ1msW', 'torch', 'partial.so'), 'partial');
    await mkdir(join(cache, 'archive-v0', 'finished'), { recursive: true });
    await writeFile(join(cache, 'archive-v0', 'finished', 'torch.so'), 'verified');
    let leftoverAtSync: boolean | undefined;
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[0] === 'sync') leftoverAtSync = existsSync(join(cache, '.tmpHZ1msW'));
      await interpreter(project);
    });

    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
    );

    expect(leftoverAtSync).toBe(false);
    expect(await readFile(join(cache, 'archive-v0', 'finished', 'torch.so'), 'utf8')).toBe(
      'verified',
    );
  });
  it('uses managed Python for new runtime setup and verifies native tokenizer imports', async () => {
    const { bundle, project } = await fixture();
    const run = vi.fn(
      async (_command: string, _args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
        await interpreter(project);
      },
    );
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
    );
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync');
    expect(sync?.[1]).toContain('--managed-python');
    const verify = run.mock.calls.find(
      ([, args]) => args[0] === '-c' && args[1]?.includes('import fastapi'),
    );
    expect(verify?.[1][1]).toContain('sentencepiece');
  });
  it('reinstalls the pinned ROCm stack only after an explicit opt-in', async () => {
    forcePlatform('linux');
    const { bundle, project } = await fixture();
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', ' ROCm ');
    vi.stubEnv('OMNIVOICE_TORCH_INDEX', 'https://mirror.invalid/rocm');
    const run = vi.fn(
      async (_command: string, _args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
        await interpreter(project);
      },
    );
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    const reinstall = run.mock.calls.find(([, args]) => args[0] === 'pip');
    expect(reinstall?.[1]).toEqual([
      'pip',
      'install',
      '--python',
      runtimePython(project),
      ...rocmTorchInstallArgs('https://mirror.invalid/rocm'),
    ]);
    expect(ROCM_TORCH_INDEX).toContain('/rocm');
  });
  it.each([false, true])(
    'repairs executable-stack libraries after a ROCm swap (existing environment: %s)',
    async (existing) => {
      forcePlatform('linux');
      vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
      vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
      const { bundle, project } = await fixture();
      const sitePackages = join(project, '.venv', 'lib', 'python3.11', 'site-packages');
      const library = join(sitePackages, 'ctranslate2.libs', 'libctranslate2-d3638643.so.4.4.0');
      const installLibrary = async () => {
        await interpreter(project);
        await mkdir(dirname(library), { recursive: true });
        const elf = Buffer.alloc(120);
        elf.set([0x7f, 0x45, 0x4c, 0x46, 2, 1]);
        elf.writeBigUInt64LE(64n, 32);
        elf.writeUInt16LE(56, 54);
        elf.writeUInt16LE(1, 56);
        elf.writeUInt32LE(0x6474e551, 64);
        elf.writeUInt32LE(7, 68);
        await writeFile(library, elf);
      };
      if (existing) await installLibrary();
      let importFailures = 0;
      const run = vi.fn(async (_command: string, args: string[]) => {
        if (args[0] === 'sync' && !args.includes('--dry-run')) await installLibrary();
        if (args[0] === 'pip' && args[1] === 'install') {
          if (args.includes('--index-url') || args.includes('--reinstall')) {
            throw new Error('No solution found: only pillow<=12.3.0 is available');
          }
        }
        if (args[1]?.includes('VOICESTUDIO_CUDNN8_PROBE=')) {
          expect(args[1]).not.toContain('import torch');
          return `VOICESTUDIO_CUDNN8_PROBE=${JSON.stringify({ device: 'hip', sitePackages })}\n`;
        }
        // The torch-only native probe never imports faster_whisper/CTranslate2.
        if (args[1] === RUNTIME_IMPORT_PROBE) {
          if ((await readFile(library)).readUInt32LE(68) & 1) {
            importFailures += 1;
            throw new Error(
              'ImportError: libctranslate2-d3638643.so.4.4.0: cannot enable executable stack as shared object requires: Invalid argument',
            );
          }
        }
        return undefined;
      });
      await installRuntime(
        bundle, project, 'uv', run, new AbortController().signal, undefined, 'global',
      );
      expect(importFailures).toBe(existing ? 1 : 0);
      for (const [, args] of run.mock.calls) {
        if (args[0] === 'sync') expect(args).not.toContain('--reinstall-package');
      }
      expect((await readFile(library)).readUInt32LE(68)).toBe(6);
      expect(await runtimeReady(bundle, project)).toBe(true);
    },
  );
  it('refuses to mark a non-ROCm torch ready after a ROCm install', async () => {
    forcePlatform('linux');
    const { bundle, project } = await fixture();
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
    vi.stubEnv('OMNIVOICE_TORCH_INDEX', 'https://mirror.invalid/simple');
    const run = vi.fn(
      async (_command: string, args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
        await interpreter(project);
        if (args[0] === '-c' && args[1] === ROCM_TORCH_PROBE) throw new Error('exit 1');
      },
    );
    await expect(
      installRuntime(bundle, project, 'uv', run, new AbortController().signal),
    ).rejects.toThrow(ROCM_TORCH_PROBE_FAILURE);
  });
  it('swaps only the torch stack and keeps the default index for its dependencies', () => {
    // #3158: `--index-url` + `--reinstall` forced Pillow onto PyTorch's index,
    // which has no Pillow 13, so the ROCm swap was unsatisfiable.
    const args = rocmTorchInstallArgs(ROCM_TORCH_INDEX);
    expect(args).not.toContain('--reinstall');
    expect(args).not.toContain('--index-url');
    expect(args).toEqual([
      '--reinstall-package',
      'torch',
      '--reinstall-package',
      'torchaudio',
      '--reinstall-package',
      'torchvision',
      'torch==2.8.0+rocm6.4',
      'torchaudio==2.8.0+rocm6.4',
      'torchvision==0.23.0+rocm6.4',
      '--extra-index-url',
      ROCM_TORCH_INDEX,
      '--index-strategy',
      'unsafe-best-match',
    ]);
    // A mirror without a rocm-style name keeps the bare pins.
    expect(rocmTorchInstallArgs('https://mirror.invalid/rocm/')).toContain('torch==2.8.0');
  });
  it('leaves macOS on its native stack when ROCm is requested', async () => {
    forcePlatform('darwin');
    vi.spyOn(process, 'arch', 'get').mockReturnValue('arm64');
    const { bundle, project } = await fixture();
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
    expect(rocmTorchOptIn('win32')).toBe(true);
    expect(rocmTorchOptIn('darwin')).toBe(false);
    expect(rocmTorchOptIn('linux')).toBe(true);
    const run = vi.fn(
      async (_command: string, _args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
        await interpreter(project);
      },
    );
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    // No Linux-only index lookup that would fail the whole bootstrap...
    const rocmLookups = run.mock.calls.filter(([, args]) =>
      args.some((arg) => arg === ROCM_TORCH_INDEX || ROCM_TORCH_PINS.some((pin) => arg === pin)),
    );
    expect(rocmLookups).toEqual([]);
    // ...and the default runtime stays reusable instead of being rebuilt forever.
    expect(await runtimeReady(bundle, project)).toBe(true);
  });
  it('does not reuse a default runtime after ROCm is selected', async () => {
    forcePlatform('linux');
    const { bundle, project } = await fixture();
    const run = vi.fn(
      async (_command: string, _args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
        await interpreter(project);
      },
    );
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    expect(await runtimeReady(bundle, project)).toBe(true);
    expect(await runtimeCompatible(bundle, project)).toBe(true);

    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
    expect(await runtimeReady(bundle, project)).toBe(false);
    expect(await runtimeCompatible(bundle, project)).toBe(false);
  });
  it('does not mark a runtime ready if the native tokenizer crashes during verification', async () => {
    const { bundle, project } = await fixture();
    const run = vi.fn(async (_command: string, args: string[]) => {
      await interpreter(project);
      if (args[0] === '-c' && args[1]?.includes('import fastapi')) {
        throw new Error('native import failed');
      }
    });
    await expect(
      installRuntime(bundle, project, 'uv', run, new AbortController().signal, undefined, 'global'),
    ).rejects.toThrow('native import failed');
    expect(await runtimeReady(bundle, project)).toBe(false);
    expect(await runtimeInstallInterrupted(project)).toBe(true);
  });
  it('keeps the selected interpreter when updating an existing runtime', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    const run = vi.fn(async (_command: string, _args: string[]) => {});
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
    );
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync');
    expect(sync?.[1]).not.toContain('--managed-python');
    expect(sync?.[1]).not.toContain('--reinstall-package');
    expect(sync?.[1]).toContain(runtimePython(project));
  });
  it('does not reuse an existing interpreter with the wrong Python version', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[1]?.includes('sys.version_info')) throw new Error('broken interpreter');
    });
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
    );
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync');
    expect(sync?.[1]).toContain('--managed-python');
    expect(sync?.[1]).not.toContain(runtimePython(project));
    const cleanIndex = run.mock.calls.findIndex(([, args]) => args[0] === 'cache');
    expect(cleanIndex).toBe(-1);
  });
  it('repairs a broken native PyTorch stack without replacing the interpreter', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    let repaired = false;
    const run = vi.fn(async (command: string, args: string[]) => {
      if (args[0] === 'sync') repaired = true;
      if (
        !repaired &&
        command === runtimePython(project) &&
        (args[1] === RUNTIME_IMPORT_PROBE || args[1] === RUNTIME_NATIVE_IMPORT_PROBE)
      ) {
        throw new Error('missing libtorchaudio.pyd');
      }
    });
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync');
    expect(sync?.[1]).toContain(runtimePython(project));
    expect(sync?.[1]).not.toContain('--managed-python');
    for (const name of RUNTIME_REPAIR_PACKAGES) {
      expect(sync?.[1]).toContain(name);
    }
    expect(run.mock.calls.find(([, args]) => args[0] === 'cache')?.[1]).toEqual([
      'cache',
      'clean',
      ...RUNTIME_REPAIR_PACKAGES,
    ]);
  });
  it('reports a missing system library instead of reinstalling wheels that cannot supply it', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[0] !== '-c') return undefined;
      if (args[1] === RUNTIME_IMPORT_PROBE || args[1] === RUNTIME_NATIVE_IMPORT_PROBE) {
        throw new Error("Could not find module 'libtorchaudio.pyd' (or one of its dependencies).");
      }
      if (args[1] === RUNTIME_NATIVE_DIAGNOSTIC_PROBE) {
        return `${NATIVE_DIAGNOSTIC_PREFIX}${JSON.stringify({
          missing_system_libraries: ['vcomp140.dll'],
          extension_present: true,
        })}\n`;
      }
      return undefined;
    });

    await expect(
      installRuntime(bundle, project, 'uv', run, new AbortController().signal),
    ).rejects.toMatchObject({
      code: MISSING_SYSTEM_LIBRARY_CODE,
      missingSystemLibraries: ['vcomp140.dll'],
    });
    // No wheel can supply a system library, so nothing is downloaded for it.
    expect(run.mock.calls.some(([, args]) => args[0] === 'sync')).toBe(false);
    expect(run.mock.calls.some(([, args]) => args[0] === 'cache')).toBe(false);
    expect(await runtimeReady(bundle, project)).toBe(false);
  });
  it('reports a missing system library when a fresh install fails verification', async () => {
    const { bundle, project } = await fixture();
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[0] !== '-c') return undefined;
      if (args[1] === RUNTIME_IMPORT_PROBE) {
        throw new Error("Could not find module 'libtorchaudio.pyd' (or one of its dependencies).");
      }
      if (args[1] === RUNTIME_NATIVE_DIAGNOSTIC_PROBE) {
        return `${NATIVE_DIAGNOSTIC_PREFIX}${JSON.stringify({
          missing_system_libraries: ['msvcp140.dll'],
          extension_present: true,
        })}\n`;
      }
      return undefined;
    });

    await expect(
      installRuntime(bundle, project, 'uv', run, new AbortController().signal),
    ).rejects.toMatchObject({ code: MISSING_SYSTEM_LIBRARY_CODE });
  });
  it('treats a finished runtime with a leftover install marker as ready, not interrupted (#3475)', async () => {
    const { bundle, project } = await fixture();
    await installRuntime(
      bundle,
      project,
      'uv',
      async () => interpreter(project),
      new AbortController().signal,
      undefined,
      'global',
    );
    // The marker survived the finished install (Windows refused its removal).
    const marker = join(project, '.runtime-installing');
    await writeFile(marker, '');
    const old = new Date(Date.now() - 60_000);
    await utimes(marker, old, old);

    expect(await runtimeInstallInterrupted(project)).toBe(false);
    expect(await runtimeReady(bundle, project)).toBe(true);
    expect(await runtimeCompatible(bundle, project)).toBe(true);
    expect(existsSync(marker)).toBe(false);
  });
  it('still reports an install that started after the last ready marker as interrupted', async () => {
    const { bundle, project } = await fixture();
    await installRuntime(
      bundle,
      project,
      'uv',
      async () => interpreter(project),
      new AbortController().signal,
      undefined,
      'global',
    );
    const old = new Date(Date.now() - 60_000);
    await utimes(join(project, '.runtime-ready'), old, old);
    await writeFile(join(project, '.runtime-installing'), new Date().toISOString());

    expect(await runtimeInstallInterrupted(project)).toBe(true);
    expect(await runtimeReady(bundle, project)).toBe(false);
  });
  it('resumes an interrupted install from the uv cache instead of evicting its wheels (#3475)', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    await writeFile(join(project, '.runtime-installing'), new Date().toISOString());
    let synced = false;
    const run = vi.fn(async (command: string, args: string[]) => {
      if (args[0] === 'sync') synced = true;
      if (
        !synced &&
        command === runtimePython(project) &&
        (args[1] === RUNTIME_IMPORT_PROBE || args[1] === RUNTIME_NATIVE_IMPORT_PROBE)
      ) {
        throw new Error("No module named 'torch'");
      }
    });
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    expect(run.mock.calls.some(([, args]) => args[0] === 'cache')).toBe(false);
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync');
    expect(sync?.[1]).toContain(runtimePython(project));
    expect(await runtimeReady(bundle, project)).toBe(true);
    expect(await runtimeInstallInterrupted(project)).toBe(false);
  });
  it('keeps an installed CPU torch through a re-run instead of uninstalling it (#3475)', async () => {
    forcePlatform('win32');
    vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
    const { bundle, project } = await fixture();
    const sitePackages = join(project, '.venv', 'Lib', 'site-packages');
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[0] === 'sync') await interpreter(project);
      if (args[1]?.includes('VOICESTUDIO_CUDNN8_PROBE=')) {
        return `VOICESTUDIO_CUDNN8_PROBE=${JSON.stringify({ device: 'none', sitePackages })}\n`;
      }
      return undefined;
    });
    const install = () =>
      installRuntime(bundle, project, 'uv', run, new AbortController().signal, undefined, 'global');
    await install();
    const syncs = () => run.mock.calls.filter(([, args]) => args[0] === 'sync');
    // First install: exact, nothing CPU-specific to keep yet.
    expect(syncs()[0]?.[1]).not.toContain('--inexact');
    await install();
    const resync = syncs()[1]?.[1];
    expect(resync).toContain('--inexact');
    expect(resync).toEqual(expect.arrayContaining(['--no-install-package', 'torch']));
  });
  it('repairs sentencepiece without evicting healthy PyTorch wheels', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    let synced = false;
    const run = vi.fn(async (command: string, args: string[]) => {
      if (args[0] === 'sync') synced = true;
      if (!synced && command === runtimePython(project) && args[1]?.includes('sentencepiece')) {
        throw new Error('broken sentencepiece native library');
      }
    });
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    expect(run.mock.calls.find(([, args]) => args[0] === 'cache')?.[1]).toEqual([
      'cache',
      'clean',
      'sentencepiece',
    ]);
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync');
    expect(sync?.[1]).toContain('sentencepiece');
    for (const name of ['torch', 'torchaudio', 'torchvision']) {
      expect(sync?.[1]).not.toContain(name);
    }
  });
  it('repairs an unrelated missing dependency without evicting native wheels', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    let synced = false;
    const run = vi.fn(async (command: string, args: string[]) => {
      if (args[0] === 'sync') synced = true;
      if (!synced && command === runtimePython(project) && args[1] === RUNTIME_IMPORT_PROBE) {
        throw new Error('No module named fastapi');
      }
    });
    await installRuntime(bundle, project, 'uv', run, new AbortController().signal);
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync');
    expect(sync?.[1]).not.toContain('--reinstall-package');
    expect(run.mock.calls.some(([, args]) => args[0] === 'cache')).toBe(false);
    expect(
      run.mock.calls.some(
        ([command, args]) =>
          command === runtimePython(project) && args[1] === RUNTIME_NATIVE_IMPORT_PROBE,
      ),
    ).toBe(true);
  });
  it('does not continue a cancelled interpreter probe into dependency installation', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    const controller = new AbortController();
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[1]?.includes('sys.version_info')) {
        controller.abort();
        throw new Error('probe aborted');
      }
    });
    await expect(
      installRuntime(bundle, project, 'uv', run, controller.signal, undefined, 'global'),
    ).rejects.toThrow();
    expect(run.mock.calls.some(([, args]) => args[0] === 'sync')).toBe(false);
  });
  it('moves legacy in-project caches before a clean retry can remove them', async () => {
    const { project } = await fixture();
    await mkdir(join(project, '.uv-cache'), { recursive: true });
    await mkdir(join(project, '.python'), { recursive: true });
    await writeFile(join(project, '.uv-cache', 'wheel'), 'verified');
    await writeFile(join(project, '.python', 'interpreter'), 'managed');

    await promoteLegacyRuntimeCaches(project);

    expect(await readFile(join(project, '..', '.uv-cache', 'wheel'), 'utf8')).toBe('verified');
    expect(await readFile(join(project, '..', '.python', 'interpreter'), 'utf8')).toBe('managed');
    await expect(readFile(join(project, '.uv-cache', 'wheel'))).rejects.toThrow();
  });
  it('does not reuse an Electron runtime with an obsolete readiness schema', async () => {
    const { bundle, project } = await fixture();
    await stageRuntimeSources(bundle, project);
    await interpreter(project);
    expect(await runtimeCompatible(bundle, project)).toBe(true);
    await writeFile(join(project, '.runtime-ready'), 'legacy-manifest-only-stamp');
    expect(await runtimeCompatible(bundle, project)).toBe(false);
  });
  it('does not copy the web UI into the runtime project', async () => {
    const { bundle, project } = await fixture();
    await stageRuntimeSources(bundle, project);
    // The backend serves the packaged build in place (OMNIVOICE_FRONTEND_DIST);
    // a staged copy would only ever be a stale second source of truth (#2599).
    expect(existsSync(join(project, 'frontend'))).toBe(false);
    expect(existsSync(join(project, 'backend', 'main.py'))).toBe(true);
    expect(await readFile(join(project, 'config', 'app.json'), 'utf8')).toBe('config/app.json');
  });
  it('replaces obsolete bundled modules without removing the interpreter or user files', async () => {
    const { bundle, project } = await fixture();
    await stageRuntimeSources(bundle, project);
    await interpreter(project);
    await writeFile(join(project, 'backend', 'obsolete.py'), 'old');
    await writeFile(join(project, 'personal.txt'), 'preserve');
    await writeFile(join(bundle, 'backend', 'main.py'), 'new');
    await stageRuntimeSources(bundle, project);
    expect(await readFile(join(project, 'backend', 'main.py'), 'utf8')).toBe('new');
    await expect(readFile(join(project, 'backend', 'obsolete.py'))).rejects.toThrow();
    expect(await readFile(runtimePython(project), 'utf8')).toBe('interpreter');
    expect(await readFile(join(project, 'personal.txt'), 'utf8')).toBe('preserve');
  });
  it('falls back to an in-place copy when Windows keeps a folder locked (#3010, #2669)', async () => {
    const { bundle, project } = await fixture();
    await stageRuntimeSources(bundle, project);
    await writeFile(join(project, 'backend', 'obsolete.py'), 'old');
    await writeFile(join(bundle, 'backend', 'main.py'), 'new');
    const locked = async () => {
      throw Object.assign(new Error('EPERM: operation not permitted, rename'), { code: 'EPERM' });
    };
    await stageRuntimeSources(bundle, project, locked);
    expect(await readFile(join(project, 'backend', 'main.py'), 'utf8')).toBe('new');
    expect(existsSync(join(project, 'backend', 'obsolete.py'))).toBe(false);
    expect(existsSync(join(project, '.incoming-backend'))).toBe(false);
  });
  it('updates in place when a locked .previous folder blocks the swap (#3409)', async () => {
    const { bundle, project } = await fixture();
    await stageRuntimeSources(bundle, project);
    await writeFile(join(bundle, 'backend', 'main.py'), 'new');
    const previous = join(project, '.previous-backend');
    const lockedRm = (async (
      path: Parameters<typeof rm>[0],
      options?: Parameters<typeof rm>[1],
    ) => {
      if (path === previous) throw Object.assign(new Error('EPERM: unlink'), { code: 'EPERM' });
      return rm(path, options);
    }) as typeof rm;
    const rename = vi.fn(async (_from: string, _to: string) => {});
    await stageRuntimeSources(bundle, project, rename, {
      fs: { cp, rm: lockedRm },
      retry: { platform: 'win32', sleep: async () => {} },
    });
    expect(await readFile(join(project, 'backend', 'main.py'), 'utf8')).toBe('new');
    expect(rename.mock.calls.some(([from]) => String(from).includes('backend'))).toBe(false);
  });
  it('retries a copy that a scanner briefly blocks before giving up (#3409)', async () => {
    const { bundle, project } = await fixture();
    await stageRuntimeSources(bundle, project);
    await writeFile(join(bundle, 'backend', 'main.py'), 'new');
    const locked = async () => {
      throw Object.assign(new Error('EPERM: operation not permitted, rename'), { code: 'EPERM' });
    };
    let blocked = 0;
    const flakyCp = (async (...args: Parameters<typeof cp>) => {
      if (args[1] === join(project, 'backend') && blocked++ < 2) {
        throw Object.assign(new Error('EBUSY: resource busy'), { code: 'EBUSY' });
      }
      return cp(...args);
    }) as typeof cp;
    await stageRuntimeSources(bundle, project, locked, {
      fs: { cp: flakyCp, rm },
      retry: { platform: 'win32', sleep: async () => {} },
    });
    expect(blocked).toBe(3);
    expect(await readFile(join(project, 'backend', 'main.py'), 'utf8')).toBe('new');
  });
  it('names the lock and the fix when a folder stays locked through every retry (#3409)', async () => {
    const { bundle, project } = await fixture();
    await stageRuntimeSources(bundle, project);
    const locked = async () => {
      throw Object.assign(new Error('EPERM: operation not permitted, rename'), { code: 'EPERM' });
    };
    const lockedCp = (async (...args: Parameters<typeof cp>) => {
      if (args[1] === join(project, 'backend')) {
        throw Object.assign(new Error('EPERM: operation not permitted, open'), { code: 'EPERM' });
      }
      return cp(...args);
    }) as typeof cp;
    const failure = stageRuntimeSources(bundle, project, locked, {
      fs: { cp: lockedCp, rm },
      retry: { platform: 'win32', sleep: async () => {} },
    });
    await expect(failure).rejects.toMatchObject({ code: RUNTIME_FILES_LOCKED_CODE });
    await expect(failure).rejects.toThrow(/OneDrive.*press Retry/s);
    expect(existsSync(join(project, '.incoming-backend'))).toBe(false);
  });
  it.each(['darwin', 'linux'] as const)(
    'keeps a %s permission error as-is instead of the Windows lock guidance',
    async (platform) => {
      const { bundle, project } = await fixture();
      await stageRuntimeSources(bundle, project);
      const denied = async () => {
        throw Object.assign(new Error('EACCES: permission denied, rename'), { code: 'EACCES' });
      };
      const deniedCp = (async (...args: Parameters<typeof cp>) => {
        if (args[1] === join(project, 'backend')) {
          throw Object.assign(new Error('EACCES: permission denied, open'), { code: 'EACCES' });
        }
        return cp(...args);
      }) as typeof cp;
      const failure = stageRuntimeSources(bundle, project, denied, {
        fs: { cp: deniedCp, rm },
        retry: { platform, sleep: async () => {} },
      });
      await expect(failure).rejects.toMatchObject({ code: 'EACCES' });
    },
  );
  it('still fails on a rename error that is not a lock', async () => {
    const { bundle, project } = await fixture();
    const broken = async () => {
      throw Object.assign(new Error('ENOSPC'), { code: 'ENOSPC' });
    };
    await expect(stageRuntimeSources(bundle, project, broken)).rejects.toThrow('ENOSPC');
  });
  it('does not leave a ready marker after a failed repair', async () => {
    const { bundle, project } = await fixture();
    await installRuntime(
      bundle,
      project,
      'uv',
      async () => interpreter(project),
      new AbortController().signal,
    );
    await expect(
      installRuntime(
        bundle,
        project,
        'uv',
        async () => {
          throw new Error('offline');
        },
        new AbortController().signal,
      ),
    ).rejects.toThrow('offline');
    expect(await runtimeReady(bundle, project)).toBe(false);
  });
  it('does not download or run commands after cancellation', async () => {
    const { bundle, project } = await fixture();
    const controller = new AbortController();
    controller.abort();
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const run = vi.fn();
    await expect(installRuntime(bundle, project, null, run, controller.signal)).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
    expect(run).not.toHaveBeenCalled();
  });
  it('rejects insufficient space before downloads or commands', async () => {
    const { bundle, project } = await fixture();
    vi.mocked(statfs).mockResolvedValueOnce({ bavail: 1024, bsize: 1024 } as Awaited<
      ReturnType<typeof statfs>
    >);
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const run = vi.fn();
    await expect(
      installRuntime(bundle, project, null, run, new AbortController().signal),
    ).rejects.toThrow(/needs at least \d+ GiB/);
    expect(fetch).not.toHaveBeenCalled();
    expect(run).not.toHaveBeenCalled();
  });
  it('drops partial downloads before checking free space', async () => {
    const { bundle, project } = await fixture();
    const partial = join(project, '..', '.uv-cache', '.tmpHZ1msW');
    await mkdir(partial, { recursive: true });
    await writeFile(join(partial, 'torch.so'), 'partial');
    let partialAtCheck: boolean | undefined;
    vi.mocked(statfs).mockImplementationOnce(async () => {
      partialAtCheck = existsSync(partial);
      return { bavail: 1024, bsize: 1024 } as Awaited<ReturnType<typeof statfs>>;
    });
    await expect(
      installRuntime(bundle, project, null, vi.fn(), new AbortController().signal),
    ).rejects.toThrow('GiB');
    expect(partialAtCheck).toBe(false);
  });
  it('reuses the app-private uv after a cancelled or failed installation', async () => {
    const { bundle, project } = await fixture();
    await mkdir(join(project, '.tools'), { recursive: true });
    const executable = join(project, '.tools', process.platform === 'win32' ? 'uv.exe' : 'uv');
    await writeFile(executable, 'uv');
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const run = vi.fn(async (_command: string) => interpreter(project));
    await installRuntime(
      bundle,
      project,
      null,
      run,
      new AbortController().signal,
      undefined,
      'global',
    );
    expect(fetch).not.toHaveBeenCalled();
    expect(run.mock.calls[0]?.[0]).toBe(executable);
  });
  it.skipIf(process.platform === 'darwin')(
    'installs and validates cuDNN 8 compatibility on a CUDA runtime',
    async () => {
      const { bundle, project } = await fixture();
      const sitePackages = join(project, '.venv', 'Lib', 'site-packages');
      const run = vi.fn(async (command: string, args: string[]) => {
        if (args[0] === 'sync') await interpreter(project);
        if (command === runtimePython(project) && args[1]?.includes('VOICESTUDIO_CUDNN8_PROBE=')) {
          return `VOICESTUDIO_CUDNN8_PROBE=${JSON.stringify({ device: 'cuda', sitePackages })}\n`;
        }
        if (args[0] === 'pip') {
          const libDir = join(
            sitePackages,
            'cudnn8_compat',
            'nvidia',
            'cudnn',
            process.platform === 'win32' ? 'bin' : 'lib',
          );
          await mkdir(libDir, { recursive: true });
          await Promise.all(
            Array.from({ length: 5 }, (_, index) =>
              writeFile(
                join(
                  libDir,
                  process.platform === 'win32'
                    ? `cudnn-${index}64_8.dll`
                    : `libcudnn-${index}.so.8`,
                ),
                'library',
              ),
            ),
          );
        }
        return undefined;
      });
      await installRuntime(
        bundle,
        project,
        'uv',
        run,
        new AbortController().signal,
        undefined,
        'global',
      );
      const compatInstall = run.mock.calls.find(([, args]) => args[0] === 'pip');
      expect(compatInstall?.[1]).toContain(CUDNN8_COMPAT_PIN);
      expect(compatInstall?.[1]).toContain(join(sitePackages, 'cudnn8_compat'));
      expect(await runtimeReady(bundle, project)).toBe(true);
    },
  );
  it.skipIf(process.platform === 'darwin')(
    'keeps a CUDA runtime incomplete when the compatibility wheel is partial',
    async () => {
      const { bundle, project } = await fixture();
      const sitePackages = join(project, '.venv', 'Lib', 'site-packages');
      const run = vi.fn(async (command: string, args: string[]) => {
        if (args[0] === 'sync') await interpreter(project);
        if (command === runtimePython(project) && args[1]?.includes('VOICESTUDIO_CUDNN8_PROBE=')) {
          return `VOICESTUDIO_CUDNN8_PROBE=${JSON.stringify({ device: 'cuda', sitePackages })}\n`;
        }
        return undefined;
      });
      await expect(
        installRuntime(
          bundle,
          project,
          'uv',
          run,
          new AbortController().signal,
          undefined,
          'global',
        ),
      ).rejects.toThrow('did not install completely');
      expect(await runtimeReady(bundle, project)).toBe(false);
      expect(await runtimeCompatible(bundle, project)).toBe(false);
    },
  );
  it('clears CTranslate2 executable-stack requests in Linux ELF libraries', async () => {
    const { project } = await fixture();
    const sitePackages = join(project, '.venv', 'lib', 'python3.11', 'site-packages');
    const libraryDir = join(sitePackages, 'ctranslate2.libs');
    await mkdir(libraryDir, { recursive: true });
    const library = join(libraryDir, 'libctranslate2-test.so.4.4.0');
    const elf = Buffer.alloc(120);
    elf.set([0x7f, 0x45, 0x4c, 0x46, 2, 1]);
    elf.writeBigUInt64LE(64n, 32);
    elf.writeUInt16LE(56, 54);
    elf.writeUInt16LE(1, 56);
    elf.writeUInt32LE(0x6474e551, 64);
    elf.writeUInt32LE(7, 68);
    await writeFile(library, elf);
    expect(await clearCtranslate2ExecutableStack(sitePackages, 'linux')).toBe(1);
    expect((await readFile(library)).readUInt32LE(68)).toBe(6);
    expect(await clearCtranslate2ExecutableStack(sitePackages, 'linux')).toBe(0);
  });
  it('does not mark a cancelled dependency install ready or run its import check', async () => {
    const { bundle, project } = await fixture();
    const controller = new AbortController();
    const run = vi.fn(async () => {
      await interpreter(project);
      controller.abort();
    });
    await expect(installRuntime(bundle, project, 'uv', run, controller.signal)).rejects.toThrow();
    expect(run).toHaveBeenCalledTimes(1);
    expect(await runtimeReady(bundle, project)).toBe(false);
  });
  it('pins the release workflow installer version', async () => {
    const release = await readFile(
      new URL('../../../.github/workflows/electron-release.yml', import.meta.url),
      'utf8',
    );
    expect(release).toContain(`version: '${UV_VERSION}'`);
  });
});

it('hands explicit download proxies and loopback bypasses to uv', async () => {
  const { bundle, project } = await fixture();
  vi.stubEnv('HTTPS_PROXY', 'socks5h://127.0.0.1:10808');
  vi.stubEnv('NO_PROXY', 'internal.example');
  vi.stubEnv('PYTHONPATH', '/must-not-leak-into-runtime-overrides');
  const run = vi.fn(
    async (_command: string, _args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) =>
      interpreter(project),
  );
  await installRuntime(
    bundle,
    project,
    'uv',
    run,
    new AbortController().signal,
    undefined,
    'global',
  );
  const env = run.mock.calls.find(([, args]) => args[0] === 'sync')?.[3];
  expect(env?.HTTPS_PROXY).toBe('socks5h://127.0.0.1:10808');
  expect(env?.NO_PROXY).toContain('localhost,127.0.0.1,::1');
  expect(env?.PYTHONPATH).toBeUndefined();
});

describe('import verification names the module that crashed (#3293)', () => {
  it('announces each runtime module before importing it', () => {
    expect(RUNTIME_IMPORT_PROBE.split('\n')).toEqual([
      "print('VOICESTUDIO_IMPORT=fastapi', flush=True)",
      'import fastapi',
      "print('VOICESTUDIO_IMPORT=uvicorn', flush=True)",
      'import uvicorn',
      "print('VOICESTUDIO_IMPORT=omnivoice', flush=True)",
      'import omnivoice',
      "print('VOICESTUDIO_IMPORT=faster_whisper', flush=True)",
      'import faster_whisper',
      "print('VOICESTUDIO_IMPORT=sentencepiece', flush=True)",
      'import sentencepiece',
      "print('VOICESTUDIO_IMPORT=torch', flush=True)",
      'import torch',
      "print('VOICESTUDIO_IMPORT=torchaudio', flush=True)",
      'import torchaudio',
      "print('VOICESTUDIO_IMPORT=torchvision', flush=True)",
      'import torchvision',
    ]);
  });

  it('reads the last announced module from partial output', () => {
    expect(lastImportAttempted('VOICESTUDIO_IMPORT=fastapi\nVOICESTUDIO_IMPORT=torch\n')).toBe(
      'torch',
    );
    expect(lastImportAttempted('no markers here')).toBeNull();
    expect(lastImportAttempted(undefined)).toBeNull();
  });
});

describe('regional PyPI mirror for the frozen sync (#3444)', () => {
  const WHEEL = 'packages/a8/db/253133d7/accelerate-1.14.0-py3-none-any.whl';
  const LOCK = [
    '[[package]]',
    'name = "accelerate"',
    'source = { registry = "https://pypi.org/simple" }',
    `wheels = [{ url = "https://files.pythonhosted.org/${WHEEL}", hash = "sha256:e943", size = 1 }]`,
    '[[package]]',
    'name = "torch"',
    'wheels = [{ url = "https://download-r2.pytorch.org/whl/cu128/torch.whl", hash = "sha256:aa" }]',
    '',
  ].join('\n');
  async function lockedFixture() {
    const paths = await fixture();
    await writeFile(join(paths.bundle, 'uv.lock'), LOCK);
    return paths;
  }
  function recordingRun(project: string, fail = 0) {
    const seen: string[] = [];
    let failures = fail;
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[0] !== 'sync') return undefined;
      seen.push(await readFile(join(project, 'uv.lock'), 'utf8'));
      if (failures-- > 0) throw new Error('Runtime setup exited with code 2');
      await interpreter(project);
      return undefined;
    });
    return { run, seen };
  }

  it('syncs the china region from the aliyun mirror and restores the bundled lock', async () => {
    const { bundle, project } = await lockedFixture();
    const { run, seen } = recordingRun(project);
    const log = vi.fn();
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'china',
      undefined,
      {
        log,
      },
    );
    expect(seen).toHaveLength(1);
    expect(seen[0]).toContain(
      `url = "https://mirrors.aliyun.com/pypi/${WHEEL}", hash = "sha256:e943"`,
    );
    expect(seen[0]).toContain('https://download-r2.pytorch.org/whl/cu128/torch.whl');
    expect(seen[0]).not.toContain('files.pythonhosted.org');
    expect(log).toHaveBeenCalledWith(expect.stringContaining('mirrors.aliyun.com'));
    expect(await readFile(join(project, 'uv.lock'), 'utf8')).toBe(LOCK);
    expect(await runtimeReady(bundle, project)).toBe(true);
    expect(await runtimeCompatible(bundle, project)).toBe(true);
  });

  it.each(['global', 'russia', 'restricted'] as const)(
    'leaves the lock byte-identical for the %s region',
    async (region) => {
      const { bundle, project } = await lockedFixture();
      const { run, seen } = recordingRun(project);
      await installRuntime(
        bundle,
        project,
        'uv',
        run,
        new AbortController().signal,
        undefined,
        region,
      );
      expect(seen).toEqual([LOCK]);
    },
  );

  describe('the auto region', () => {
    // Big enough for the speed check to sample.
    const SAMPLED = LOCK.replace('size = 1 }', 'size = 1048576 }');
    async function autoInstall(fetchImpl: (url: string) => Promise<Response>) {
      vi.stubGlobal('fetch', vi.fn(fetchImpl));
      const { bundle, project } = await fixture();
      await writeFile(join(bundle, 'uv.lock'), SAMPLED);
      const { run, seen } = recordingRun(project);
      const log = vi.fn();
      await installRuntime(
        bundle,
        project,
        'uv',
        run,
        new AbortController().signal,
        undefined,
        'auto',
        undefined,
        {
          log,
        },
      );
      return { seen, log };
    }
    const body = () => new Response(new Uint8Array(PYPI_SPEED_SAMPLE_BYTES), { status: 206 });

    it('uses the aliyun mirror when it answers and PyPI does not', async () => {
      const { seen, log } = await autoInstall(async (url) => {
        if (url.includes('files.pythonhosted.org')) throw new Error('ETIMEDOUT');
        return body();
      });
      expect(seen).toHaveLength(1);
      expect(seen[0]).toContain(`url = "https://mirrors.aliyun.com/pypi/${WHEEL}"`);
      expect(log).toHaveBeenCalledWith(expect.stringContaining('PyPI speed check'));
    });

    it('stays on PyPI when the mirror does not answer', async () => {
      const { seen } = await autoInstall(async (url) => {
        if (url.includes('mirrors.aliyun.com')) throw new Error('ECONNREFUSED');
        return body();
      });
      expect(seen).toEqual([SAMPLED]);
    });
  });

  it('retries once from the original URLs when the mirrored sync fails', async () => {
    const { bundle, project } = await lockedFixture();
    const { run, seen } = recordingRun(project, 1);
    const log = vi.fn();
    const pypiHost = vi.fn();
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'china',
      undefined,
      {
        log,
        pypiHost,
      },
    );
    expect(seen).toHaveLength(2);
    expect(seen[0]).toContain('mirrors.aliyun.com');
    expect(seen[1]).toBe(LOCK);
    expect(pypiHost).toHaveBeenCalledWith('files.pythonhosted.org');
    expect(log).toHaveBeenCalledWith(
      expect.stringContaining('retrying from files.pythonhosted.org'),
    );
    expect(await runtimeReady(bundle, project)).toBe(true);
  });

  it('restores the bundled lock when both attempts fail', async () => {
    const { bundle, project } = await lockedFixture();
    const { run, seen } = recordingRun(project, 2);
    await expect(
      installRuntime(bundle, project, 'uv', run, new AbortController().signal, undefined, 'china'),
    ).rejects.toThrow('code 2');
    expect(seen).toHaveLength(2);
    expect(await readFile(join(project, 'uv.lock'), 'utf8')).toBe(LOCK);
  });

  it('treats a lock left on the mirror as the same dependency graph', async () => {
    const { bundle, project } = await lockedFixture();
    await installRuntime(
      bundle,
      project,
      'uv',
      recordingRun(project).run,
      new AbortController().signal,
      undefined,
      'global',
    );
    await writeFile(
      join(project, 'uv.lock'),
      LOCK.replace('https://files.pythonhosted.org/', 'https://mirrors.aliyun.com/pypi/'),
    );
    expect(await runtimeCompatible(bundle, project)).toBe(true);
    await writeFile(join(project, 'uv.lock'), LOCK.replace('sha256:e943', 'sha256:0000'));
    expect(await runtimeCompatible(bundle, project)).toBe(false);
  });
});

describe('multi-connection download of large locked wheels (#3411)', () => {
  const data = Buffer.alloc(PREFETCH_MIN_BYTES + 5, 7);
  const digest = createHash('sha256').update(data).digest('hex');
  const URL_ =
    'https://download-r2.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp311-cp311-win_amd64.whl';
  const LOCK = [
    '[[package]]',
    'name = "torch"',
    'version = "2.8.0+cu128"',
    'source = { registry = "https://download.pytorch.org/whl/cu128" }',
    'wheels = [',
    `    { url = "${URL_}", hash = "sha256:${digest}", upload-time = "2025-10-01T23:49:30Z" },`,
    ']',
    '',
  ].join('\n');
  const DRY_RUN = [
    'Using CPython 3.11.16 interpreter at: C:\\rt\\.python\\python.exe',
    'DEBUG Identified uncached distribution: torch==2.8.0+cu128',
  ].join('\n');

  function transportFor(fail: boolean): RangeTransport {
    return async (url, start, end) => {
      if (fail) throw new RangeUnsupportedError(url);
      return (async function* () {
        yield data.subarray(start, end + 1);
      })();
    };
  }

  async function install(fail = false) {
    forcePlatform('win32');
    vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
    vi.mocked(httpsRangeTransport).mockReturnValue({
      transport: transportFor(fail),
      probeSize: async () => data.length,
      close: () => undefined,
    });
    const { bundle, project } = await fixture();
    await writeFile(join(bundle, 'uv.lock'), LOCK);
    const seen: string[] = [];
    const planning: Array<{ timeoutMs: number; quiet?: boolean } | undefined> = [];
    const run = vi.fn(
      async (
        _command: string,
        args: string[],
        _cwd: string,
        _env?: NodeJS.ProcessEnv,
        options?: { timeoutMs: number; quiet?: boolean },
      ) => {
        if (args[0] === '-v' && args.includes('--dry-run')) {
          planning.push(options);
          return DRY_RUN;
        }
        if (args[0] !== 'sync') return undefined;
        seen.push(await readFile(join(project, 'uv.lock'), 'utf8'));
        await interpreter(project);
        return undefined;
      },
    );
    const transfer = vi.fn();
    const log = vi.fn();
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
      undefined,
      { transfer, log },
    );
    return { bundle, project, seen, planning, transfer, log };
  }

  it('hands uv the verified local copy, then restores the lock and frees the space', async () => {
    const { bundle, project, seen, planning, transfer } = await install();
    expect(planning).toEqual([expect.objectContaining({ quiet: true })]);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatch(
      new RegExp(
        `url = "file://[^"]+/torch-2\\.8\\.0%2Bcu128-cp311-cp311-win_amd64\\.whl", hash = "sha256:${digest}"`,
      ),
    );
    expect(seen[0]).not.toContain(URL_);
    expect(transfer).toHaveBeenLastCalledWith('torch', data.length, data.length);
    expect(await readFile(join(project, 'uv.lock'), 'utf8')).toBe(LOCK);
    expect(existsSync(join(project, '..', '.wheel-prefetch'))).toBe(false);
    expect(await runtimeReady(bundle, project)).toBe(true);
  });

  it('leaves the wheel to uv when the fast download fails', async () => {
    const { seen, log } = await install(true);
    expect(seen).toEqual([LOCK]);
    expect(log).toHaveBeenCalledWith(expect.stringContaining('uv will download it instead'));
  });

  it('lets uv download the wheel itself when it cannot use the local copy', async () => {
    forcePlatform('win32');
    vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
    vi.mocked(httpsRangeTransport).mockReturnValue({
      transport: transportFor(false),
      probeSize: async () => data.length,
      close: () => undefined,
    });
    const { bundle, project } = await fixture();
    await writeFile(join(bundle, 'uv.lock'), LOCK);
    const seen: string[] = [];
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[0] === '-v') return DRY_RUN;
      if (args[0] !== 'sync') return undefined;
      const lock = await readFile(join(project, 'uv.lock'), 'utf8');
      seen.push(lock);
      if (lock.includes('file://')) throw new Error('Runtime setup exited with code 2');
      await interpreter(project);
      return undefined;
    });
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
    );
    expect(seen).toHaveLength(2);
    expect(seen[1]).toBe(LOCK);
    expect(await runtimeReady(bundle, project)).toBe(true);
  });

  it('can be turned off with OMNIVOICE_SETUP_CONNECTIONS=1', async () => {
    vi.stubEnv('OMNIVOICE_SETUP_CONNECTIONS', '1');
    const { seen, planning } = await install();
    expect(planning).toEqual([]);
    expect(seen).toEqual([LOCK]);
  });
});

describe('outdated Visual C++ runtime behind a native import crash (#3293)', () => {
  const crash = () =>
    Object.assign(new Error('Runtime setup exited with code 3221225477'), {
      code: 3221225477,
      stdout: 'VOICESTUDIO_IMPORT=fastapi\nVOICESTUDIO_IMPORT=sentencepiece\n',
    });
  const diagnosis = (version: string) =>
    `${NATIVE_DIAGNOSTIC_PREFIX}${JSON.stringify({
      missing_system_libraries: [],
      extension_present: true,
      msvcp140: { path: 'C:\\Windows\\System32\\MSVCP140.dll', version },
    })}\n`;
  function runner(version: string, failure: () => Error) {
    return vi.fn(async (_command: string, args: string[]) => {
      if (args[0] !== '-c') return undefined;
      if (args[1] === RUNTIME_IMPORT_PROBE) throw failure();
      if (args[1] === RUNTIME_NATIVE_DIAGNOSTIC_PROBE) return diagnosis(version);
      return undefined;
    });
  }

  it('compares MSVCP140 versions against the 14.40 floor', () => {
    expect(msvcp140Outdated('14.29.30133.0')).toBe(true);
    expect(msvcp140Outdated('14.36.32532.0')).toBe(true);
    expect(msvcp140Outdated('14.40.33810.0')).toBe(false);
    expect(msvcp140Outdated('14.44.35211.0')).toBe(false);
    expect(msvcp140Outdated(undefined)).toBe(false);
    expect(msvcp140Outdated('garbage')).toBe(false);
  });

  it('reads the loaded MSVCP140 from the diagnostic and tolerates its absence', () => {
    expect(parseNativeDiagnosis(diagnosis('14.29.30133.0'))?.msvcp140).toEqual({
      path: 'C:\\Windows\\System32\\MSVCP140.dll',
      version: '14.29.30133.0',
    });
    expect(
      parseNativeDiagnosis(
        `${NATIVE_DIAGNOSTIC_PREFIX}${JSON.stringify({ missing_system_libraries: [] })}`,
      )?.msvcp140,
    ).toBeNull();
    expect(RUNTIME_NATIVE_DIAGNOSTIC_PROBE).toContain('GetFileVersionInfoW');
  });

  it('explains an access violation on an outdated runtime instead of a bare exit code', async () => {
    const { bundle, project } = await fixture();
    const failure = installRuntime(
      bundle,
      project,
      'uv',
      runner('14.29.30133.0', crash),
      new AbortController().signal,
    );
    await expect(failure).rejects.toMatchObject({ code: OUTDATED_SYSTEM_LIBRARY_CODE });
    await expect(failure).rejects.toThrow(/"sentencepiece".*14\.29\.30133\.0.*Visual C\+\+/s);
    expect(await runtimeReady(bundle, project)).toBe(false);
  });

  it('keeps the plain import failure when the runtime is current or the error is not a crash', async () => {
    const current = await fixture();
    await expect(
      installRuntime(
        current.bundle,
        current.project,
        'uv',
        runner('14.44.35211.0', crash),
        new AbortController().signal,
      ),
    ).rejects.toThrow('the import check stopped while importing "sentencepiece"');
    const notACrash = await fixture();
    await expect(
      installRuntime(
        notACrash.bundle,
        notACrash.project,
        'uv',
        runner('14.29.30133.0', () =>
          Object.assign(new Error("No module named 'sentencepiece'"), { code: 1, stdout: '' }),
        ),
        new AbortController().signal,
      ),
    ).rejects.not.toMatchObject({ code: OUTDATED_SYSTEM_LIBRARY_CODE });
  });
});
