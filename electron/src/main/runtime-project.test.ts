// @vitest-environment node
import { mkdtemp, mkdir, readFile, rm, writeFile, statfs } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, posix } from 'node:path';
import { createHash } from 'node:crypto';
import windowsRocmRecipe from '../../../scripts/windows-rocm-recipe.json';
import { downloadRuntimeArchive, downloadRuntimeInstaller } from './runtime-download';
import * as pthAscii from './pth-ascii';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import {
  installRuntime,
  promoteLegacyRuntimeCaches,
  CUDNN8_COMPAT_PIN,
  ROCM_TORCH_INDEX,
  ROCM_TORCH_PINS,
  WINDOWS_ROCM_FIND_LINKS,
  WINDOWS_ROCM_TORCH_PINS,
  WINDOWS_ROCM_CT2_ARCHIVE,
  WINDOWS_ROCM_CT2_ARCHIVE_SHA256,
  WINDOWS_ROCM_CT2_WHEEL,
  WINDOWS_ROCM_CT2_WHEEL_SHA256,
  WINDOWS_ROCM_CT2_PROBE,
  rocmTorchOptIn,
  RUNTIME_IMPORT_PROBE,
  RUNTIME_NATIVE_IMPORT_PROBE,
  RUNTIME_REPAIR_PACKAGES,
  clearCtranslate2ExecutableStack,
  runtimePython,
  rocmTorchInstallArgs,
  runtimeReady,
  runtimeCompatible,
  runtimeInstallInterrupted,
  stageRuntimeSources,
  UV_VERSION,
  type TorchVariant,
} from './runtime-project';

vi.mock('./runtime-download', () => ({
  downloadRuntimeArchive: vi.fn(),
  downloadRuntimeInstaller: vi.fn(),
}));

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
  await mkdir(join(bundle, 'frontend', 'dist'), { recursive: true });
  await mkdir(join(bundle, 'omnivoice'));
  for (const file of [
    'pyproject.toml',
    'uv.lock',
    'README.md',
    'LICENSE',
    'backend/main.py',
    'frontend/dist/index.html',
    'omnivoice/__init__.py',
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
const recipeChanges: [string, (recipe: typeof windowsRocmRecipe) => void][] = [
  [
    'schema version',
    (recipe) => {
      recipe.schema_version += 1;
    },
  ],
  [
    'Python version',
    (recipe) => {
      recipe.python.version = '3.13';
    },
  ],
  [
    'Python platform',
    (recipe) => {
      recipe.python.platform = 'win-arm64';
    },
  ],
  [
    'torch URL',
    (recipe) => {
      recipe.torch.find_links += 'changed/';
    },
  ],
  ...windowsRocmRecipe.torch.packages.map(
    (pin, index): [string, (recipe: typeof windowsRocmRecipe) => void] => [
      pin.split('==')[0]!,
      (recipe) => {
        recipe.torch.packages[index] = `${pin}.changed`;
      },
    ],
  ),
  [
    'CT2 version',
    (recipe) => {
      recipe.ctranslate2.version = '0.0.0';
    },
  ],
  [
    'CT2 archive URL',
    (recipe) => {
      recipe.ctranslate2.archive_url += '?changed';
    },
  ],
  [
    'CT2 archive checksum',
    (recipe) => {
      recipe.ctranslate2.archive_sha256 = '0'.repeat(64);
    },
  ],
  [
    'CT2 wheel member',
    (recipe) => {
      recipe.ctranslate2.wheel_member = 'changed/wheel.whl';
    },
  ],
  [
    'CT2 wheel checksum',
    (recipe) => {
      recipe.ctranslate2.wheel_sha256 = '0'.repeat(64);
    },
  ],
  [
    'CT2 DLL markers',
    (recipe) => {
      recipe.ctranslate2.required_dlls.push('other.dll');
    },
  ],
];

async function installRecipeFixture(variant: TorchVariant = 'rocm') {
  const { bundle, project } = await fixture();
  const run = vi.fn(async (_command: string, args: string[]) => {
    await interpreter(project);
    if (args[0] === '-c' && args[1]?.includes('Expected ROCm wheel missing')) {
      await writeFile(args[5]!, 'verified wheel');
    }
  });
  await installRuntime(
    bundle,
    project,
    'uv',
    run,
    new AbortController().signal,
    undefined,
    'global',
    variant,
  );
  return { bundle, project, run };
}

async function legacyStamp(bundle: string, variant: TorchVariant, platform: NodeJS.Platform) {
  const hash = createHash('sha256').update('electron-runtime-v2-cudnn8');
  if (variant === 'rocm') {
    hash.update(':torch=rocm');
    if (platform === 'win32') hash.update(`:ctranslate2=${WINDOWS_ROCM_CT2_WHEEL_SHA256}`);
    hash.update(platform === 'win32' ? WINDOWS_ROCM_FIND_LINKS : ROCM_TORCH_INDEX);
  }
  if (variant === 'cpu') hash.update(':torch=cpu');
  for (const file of ['pyproject.toml', 'uv.lock']) hash.update(await readFile(join(bundle, file)));
  return hash.digest('hex');
}
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
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
  describe('Windows ROCm recipe readiness', () => {
    beforeEach(() => {
      forcePlatform('win32');
      vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
      vi.stubEnv('OMNIVOICE_WINDOWS_ROCM_FIND_LINKS', '');
      vi.stubEnv('OMNIVOICE_TORCH_INDEX', '');
    });

    it.each(recipeChanges)(
      'invalidates Windows readiness when %s changes',
      async (_name, change) => {
        const { bundle, project, run } = await installRecipeFixture();
        const marker = await readFile(join(project, '.runtime-ready'), 'utf8');
        const original = structuredClone(windowsRocmRecipe);
        const changed = structuredClone(original);
        change(changed);
        expect(await runtimeReady(bundle, project, 'rocm')).toBe(true);
        expect(await runtimeCompatible(bundle, project, 'rocm')).toBe(true);
        run.mockClear();
        try {
          Object.assign(windowsRocmRecipe, changed);
          expect(await runtimeReady(bundle, project, 'rocm')).toBe(false);
          expect(await runtimeCompatible(bundle, project, 'rocm')).toBe(false);
          expect(await readFile(join(project, '.runtime-ready'), 'utf8')).toBe(marker);
          expect(await readFile(runtimePython(project), 'utf8')).toBe('interpreter');
          expect(run).not.toHaveBeenCalled();
        } finally {
          Object.assign(windowsRocmRecipe, original);
        }
        expect(await runtimeReady(bundle, project, 'rocm')).toBe(true);
        expect(await runtimeCompatible(bundle, project, 'rocm')).toBe(true);
      },
    );

    it('keeps an unchanged recipe ready but requires explicit repair for old Windows markers', async () => {
      const { bundle, project, run } = await installRecipeFixture();
      expect(await runtimeReady(bundle, project, 'rocm')).toBe(true);
      const previous = await legacyStamp(bundle, 'rocm', 'win32');
      await writeFile(join(project, '.runtime-ready'), previous);
      run.mockClear();
      expect(await runtimeReady(bundle, project, 'rocm')).toBe(false);
      expect(await runtimeCompatible(bundle, project, 'rocm')).toBe(false);
      expect(await readFile(join(project, '.runtime-ready'), 'utf8')).toBe(previous);
      expect(await readFile(runtimePython(project), 'utf8')).toBe('interpreter');
      expect(run).not.toHaveBeenCalled();
    });

    it('invalidates Windows readiness when the find-links override changes', async () => {
      const { bundle, project } = await installRecipeFixture();
      vi.stubEnv('OMNIVOICE_WINDOWS_ROCM_FIND_LINKS', 'https://mirror.invalid/rocm/');
      expect(await runtimeReady(bundle, project, 'rocm')).toBe(false);
      expect(await runtimeCompatible(bundle, project, 'rocm')).toBe(false);
    });

    it.each<[NodeJS.Platform, TorchVariant]>([
      ['win32', 'default'],
      ['win32', 'cpu'],
      ['linux', 'rocm'],
      ['linux', 'default'],
      ['linux', 'cpu'],
      ['darwin', 'default'],
    ])(
      'preserves existing %s %s markers independently of the Windows recipe',
      async (platform, variant) => {
        forcePlatform(platform);
        vi.spyOn(process, 'arch', 'get').mockReturnValue(platform === 'darwin' ? 'arm64' : 'x64');
        const { bundle, project } = await installRecipeFixture(variant);
        const marker = await legacyStamp(bundle, variant, platform);
        expect(await readFile(join(project, '.runtime-ready'), 'utf8')).toBe(marker);
        const original = structuredClone(windowsRocmRecipe);
        try {
          for (const [, change] of recipeChanges) {
            const changed = structuredClone(original);
            change(changed);
            Object.assign(windowsRocmRecipe, changed);
            expect(await runtimeReady(bundle, project, variant)).toBe(true);
            expect(await runtimeCompatible(bundle, project, variant)).toBe(true);
          }
        } finally {
          Object.assign(windowsRocmRecipe, original);
        }
      },
    );
  });

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
    expect(run.mock.calls).toHaveLength(process.platform === 'darwin' ? 2 : 3);
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
    expect(syncEnv?.UV_PYTHON_INSTALL_DIR).toBe(join(project, '..', '.python'));
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
    const platform = vi.spyOn(process, 'platform', 'get').mockReturnValue('linux');
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
      '--reinstall',
      '--python',
      runtimePython(project),
      ...ROCM_TORCH_PINS,
      '--index-url',
      'https://mirror.invalid/rocm',
    ]);
    expect(ROCM_TORCH_INDEX).toContain('/rocm');
    platform.mockRestore();
  });
  it('installs the AMD Windows ROCm recipe only after opt-in', async () => {
    expect(WINDOWS_ROCM_CT2_WHEEL_SHA256).toBe(windowsRocmRecipe.ctranslate2.wheel_sha256);
    const { bundle, project } = await fixture();
    const platform = vi.spyOn(process, 'platform', 'get').mockReturnValue('win32');
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', '');
    const run = vi.fn(
      async (_command: string, _args: string[], _cwd: string, _env?: NodeJS.ProcessEnv) => {
        await interpreter(project);
        if (_args[0] === '-c' && _args[1]?.includes('Expected ROCm wheel missing')) {
          await writeFile(_args[5], 'verified wheel');
        }
      },
    );
    const phase = vi.fn();
    try {
      await installRuntime(
        bundle,
        project,
        'uv',
        run,
        new AbortController().signal,
        phase,
        'auto',
        'rocm',
      );
      expect(phase.mock.calls.map(([value]) => value)).toEqual([
        'checking',
        'installing_deps',
        'installing_deps',
        'installing_deps',
        'verifying',
      ]);
      const sync = run.mock.calls.find(([, args]) => args[0] === 'sync');
      expect(sync?.[1]).toContain(windowsRocmRecipe.python.version);
      expect(run.mock.calls.find(([, args]) => args[0] === '--no-config')?.[1]).toEqual(
        rocmTorchInstallArgs(runtimePython(project), 'win32'),
      );
      expect(rocmTorchInstallArgs(runtimePython(project), 'win32')).toEqual([
        '--no-config',
        'pip',
        'install',
        '--python',
        runtimePython(project),
        ...WINDOWS_ROCM_TORCH_PINS,
        '--reinstall-package',
        'torch',
        '--reinstall-package',
        'torchaudio',
        '--reinstall-package',
        'torchvision',
        '--find-links',
        WINDOWS_ROCM_FIND_LINKS,
      ]);
      expect(
        run.mock.calls.some(([, args]) => args[1]?.includes('torch.cuda.is_available()')),
      ).toBe(true);
      const gpuProbe = run.mock.calls.find(([, args]) =>
        args[1]?.includes('torch.cuda.is_available()'),
      )?.[1][1];
      expect(gpuProbe).toContain('result = (tensor @ tensor)[0, 0].item()');
      expect(gpuProbe).toContain('if result != 4:');
      expect(gpuProbe).not.toMatch(/\bassert\b/);
      expect(WINDOWS_ROCM_CT2_PROBE).not.toMatch(/\bassert\b/);
      expect(WINDOWS_ROCM_CT2_PROBE).toContain('raise RuntimeError');
      expect(WINDOWS_ROCM_CT2_PROBE).toContain('importlib.util.find_spec("ctranslate2")');
      expect(WINDOWS_ROCM_CT2_PROBE).toContain('importlib.util.find_spec("torch")');
      expect(WINDOWS_ROCM_CT2_PROBE).toContain('ct2_dir.parent / "_rocm_sdk_core" / "bin"');
      expect(WINDOWS_ROCM_CT2_PROBE).toContain(
        'ct2_dir.parent / "_rocm_sdk_libraries_custom" / "bin"',
      );
      expect(WINDOWS_ROCM_CT2_PROBE).toContain('torch_dir / "lib"');
      expect(
        WINDOWS_ROCM_CT2_PROBE.indexOf('stack.enter_context(os.add_dll_directory'),
      ).toBeLessThan(WINDOWS_ROCM_CT2_PROBE.indexOf('    import ctranslate2'));
      expect(downloadRuntimeArchive).toHaveBeenCalledWith(
        WINDOWS_ROCM_CT2_ARCHIVE,
        join(
          project,
          '..',
          '.uv-cache',
          `${posix.basename(new URL(windowsRocmRecipe.ctranslate2.archive_url).pathname, '.zip')}-v${windowsRocmRecipe.ctranslate2.version}.zip`,
        ),
        WINDOWS_ROCM_CT2_ARCHIVE_SHA256,
        expect.anything(),
        expect.any(AbortSignal),
      );
      expect(
        run.mock.calls.find(([, args]) => args[1]?.includes('Expected ROCm wheel missing'))?.[1],
      ).toEqual(
        expect.arrayContaining([
          windowsRocmRecipe.ctranslate2.wheel_member,
          WINDOWS_ROCM_CT2_WHEEL_SHA256,
        ]),
      );
      const extraction = run.mock.calls.find(([, args]) =>
        args[1]?.includes('Expected ROCm wheel missing'),
      )?.[1][1];
      expect(extraction).not.toMatch(/\bassert\b/);
      expect(extraction).toContain('if hashlib.sha256(wheel).hexdigest() != sys.argv[3]:');
      expect(run.mock.calls.find(([, args]) => args.includes('--no-deps'))?.[1]).toContain(
        join(project, '..', '.uv-cache', WINDOWS_ROCM_CT2_WHEEL),
      );
      expect(run.mock.calls.some(([, args]) => args[1] === WINDOWS_ROCM_CT2_PROBE)).toBe(true);
      expect(await runtimeReady(bundle, project, 'rocm')).toBe(true);
      expect(await runtimeReady(bundle, project, 'default')).toBe(false);
    } finally {
      platform.mockRestore();
    }
  });
  it.each(['fresh', 'healthy', 'broken'])(
    'never syncs locked CUDA packages during Windows ROCm %s setup',
    async (state) => {
      forcePlatform('win32');
      vi.spyOn(process, 'arch', 'get').mockReturnValue('x64');
      const { bundle, project } = await fixture();
      await writeFile(
        join(bundle, 'uv.lock'),
        ['torch', 'torchaudio', 'torchvision', 'nvidia-cublas-cu12', 'nvidia-ml-py']
          .map((name) => `[[package]]\nname = "${name}"`)
          .join('\n'),
      );
      if (state !== 'fresh') await interpreter(project);
      let synced = false;
      const run = vi.fn(async (_command: string, args: string[]) => {
        if (
          state === 'broken' &&
          !synced &&
          (args[1] === RUNTIME_IMPORT_PROBE || args[1] === RUNTIME_NATIVE_IMPORT_PROBE)
        ) {
          throw new Error('broken native torch library');
        }
        if (args[0] === 'sync') {
          synced = true;
          await interpreter(project);
        }
        if (args[0] === '-c' && args[1]?.includes('Expected ROCm wheel missing')) {
          await writeFile(args[5]!, 'verified wheel');
        }
      });
      await installRuntime(
        bundle,
        project,
        'uv',
        run,
        new AbortController().signal,
        undefined,
        'global',
        'rocm',
      );
      const sync = run.mock.calls.find(([, args]) => args[0] === 'sync')![1];
      const skipped = sync.flatMap((arg, index) =>
        arg === '--no-install-package' ? [sync[index + 1]] : [],
      );
      expect(skipped).toEqual([...RUNTIME_REPAIR_PACKAGES, 'nvidia-cublas-cu12']);
      expect(sync).not.toContain('--reinstall-package');
      expect(sync).toContain(state === 'fresh' ? '--managed-python' : runtimePython(project));
      expect(run.mock.calls.find(([, args]) => args[0] === '--no-config')?.[1]).toEqual(
        rocmTorchInstallArgs(runtimePython(project), 'win32'),
      );
      expect(
        run.mock.calls.filter(([, args]) => args[0] === 'cache').map(([, args]) => args),
      ).toEqual(state === 'broken' ? [['cache', 'clean', ...RUNTIME_REPAIR_PACKAGES]] : []);
      expect(await runtimeReady(bundle, project, 'rocm')).toBe(true);
      expect(await runtimeReady(bundle, project, 'default')).toBe(false);
      expect(await runtimeInstallInterrupted(project)).toBe(false);
    },
  );
  it.each<[NodeJS.Platform, TorchVariant]>([
    ['win32', 'default'],
    ['win32', 'cpu'],
    ['linux', 'default'],
    ['linux', 'cpu'],
    ['linux', 'rocm'],
    ['darwin', 'default'],
  ])('preserves the frozen sync contract for %s %s', async (platform, variant) => {
    forcePlatform(platform);
    vi.spyOn(process, 'arch', 'get').mockReturnValue(platform === 'darwin' ? 'arm64' : 'x64');
    const { bundle, project, run } = await installRecipeFixture(variant);
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync')![1];
    const skipped = sync.flatMap((arg, index) =>
      arg === '--no-install-package' ? [sync[index + 1]] : [],
    );
    expect(skipped).toEqual(variant === 'cpu' ? [...RUNTIME_REPAIR_PACKAGES] : []);
    expect(sync).toContain('--frozen');
    expect(run.mock.calls.some(([, args]) => args[0] === '--no-config')).toBe(false);
    expect(await runtimeReady(bundle, project, variant)).toBe(true);
  });
  it('ignores the ROCm opt-in on Windows ARM where no native ROCm wheels exist', async () => {
    forcePlatform('win32');
    vi.spyOn(process, 'arch', 'get').mockReturnValue('arm64');
    const { bundle, project } = await fixture();
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
    expect(rocmTorchOptIn('win32')).toBe(false);
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
  it('does not mark an AMD runtime ready if the signed CTranslate2 wheel fails verification', async () => {
    const { bundle, project } = await fixture();
    const platform = vi.spyOn(process, 'platform', 'get').mockReturnValue('win32');
    const run = vi.fn(async (_command: string, args: string[]) => {
      await interpreter(project);
      if (args[1]?.includes('Invalid ROCm wheel checksum')) {
        throw new Error('Invalid ROCm wheel checksum');
      }
    });
    try {
      await expect(
        installRuntime(
          bundle,
          project,
          'uv',
          run,
          new AbortController().signal,
          undefined,
          'global',
          'rocm',
        ),
      ).rejects.toThrow('Invalid ROCm wheel checksum');
      expect(await runtimeReady(bundle, project, 'rocm')).toBe(false);
      expect(await runtimeInstallInterrupted(project)).toBe(true);
      expect(run.mock.calls.some(([, args]) => args.includes('--no-deps'))).toBe(false);
    } finally {
      platform.mockRestore();
    }
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
    const probe = run.mock.calls.find(([, args]) => args[1]?.includes('sys.version_info'))?.[1][1];
    expect(probe).not.toMatch(/\bassert\b/);
    expect(probe).toContain('raise RuntimeError("Unsupported runtime Python version")');
    const cleanIndex = run.mock.calls.findIndex(([, args]) => args[0] === 'cache');
    expect(cleanIndex).toBe(-1);
  });
  it('keeps the Windows ARM interpreter guard active under inherited optimization', async () => {
    forcePlatform('win32');
    vi.spyOn(process, 'arch', 'get').mockReturnValue('arm64');
    vi.stubEnv('PYTHONOPTIMIZE', '2');
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
    const probe = run.mock.calls.find(([, args]) => args[1]?.includes('platform.machine'))?.[1][1];
    expect(probe).not.toMatch(/\bassert\b/);
    expect(probe).toContain('if platform.machine().upper() not in ("AMD64", "X86_64"):');
    expect(probe).toContain(
      'raise RuntimeError("Windows ARM requires an x64 runtime interpreter")',
    );
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
    ).rejects.toThrow('9 GiB');
    expect(fetch).not.toHaveBeenCalled();
    expect(run).not.toHaveBeenCalled();
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
