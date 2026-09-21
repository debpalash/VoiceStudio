// @vitest-environment node
import { mkdtemp, mkdir, readFile, rm, writeFile, statfs } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { downloadRuntimeInstaller } from './runtime-download';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  installRuntime,
  promoteLegacyRuntimeCaches,
  CUDNN8_COMPAT_PIN,
  clearCtranslate2ExecutableStack,
  runtimePython,
  runtimeReady,
  runtimeCompatible,
  runtimeInstallInterrupted,
  stageRuntimeSources,
  UV_VERSION,
} from './runtime-project';

vi.mock('./runtime-download', () => ({ downloadRuntimeInstaller: vi.fn() }));

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
  await mkdir(join(bundle, 'omnivoice'));
  for (const file of [
    'pyproject.toml',
    'uv.lock',
    'README.md',
    'LICENSE',
    'backend/main.py',
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
afterEach(async () => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.mocked(statfs).mockClear();
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe('packaged runtime setup', () => {
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
  it('does not reuse an existing interpreter that fails the version/native probe', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[1]?.includes('import sys, sentencepiece')) throw new Error('broken interpreter');
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
    expect(sync?.[1]).toContain('--reinstall-package');
    expect(sync?.[1]).toContain('sentencepiece');
    const cleanIndex = run.mock.calls.findIndex(([, args]) => args[0] === 'cache');
    expect(run.mock.calls[cleanIndex]?.[1]).toEqual(['cache', 'clean', 'sentencepiece']);
    expect(cleanIndex).toBeLessThan(run.mock.calls.findIndex(([, args]) => args[0] === 'sync'));
  });
  it('does not continue a cancelled interpreter probe into dependency installation', async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    const controller = new AbortController();
    const run = vi.fn(async (_command: string, args: string[]) => {
      if (args[1]?.includes('import sys, sentencepiece')) {
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
      new URL('../../../.github/workflows/release.yml', import.meta.url),
      'utf8',
    );
    expect(release).toContain(`UV_VERSION: "${UV_VERSION}"`);
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
