// @vitest-environment node
import { fixture, interpreter, runner, recipe, report } from './runtime-windows-rocm.fixture';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import {
  installRuntime,
  RUNTIME_NATIVE_IMPORT_PROBE,
  resolveTorchVariant,
  runtimePython,
  runtimeReady,
} from './runtime-project';

it('selects only the explicit Windows x64 recipe', () => {
  expect(resolveTorchVariant({ OMNIVOICE_TORCH_VARIANT: ' ROCm ' }, 'win32', 'x64').variant).toBe(
    'rocm',
  );
  expect(() => resolveTorchVariant({ OMNIVOICE_TORCH_VARIANT: 'rocm' }, 'win32', 'arm64')).toThrow(
    /Windows.*x64/,
  );
  expect(resolveTorchVariant({}, 'win32', 'x64', () => false).variant).toBe('cpu');
});
it.each(['fresh', 'healthy', 'broken'] as const)(
  'protects the overlay before %s frozen sync',
  async (state) => {
    const { bundle, project } = await fixture();
    if (state !== 'fresh') await interpreter(project);
    const run = runner(project);
    if (state === 'broken') {
      run.mockImplementation(async (_command, args) => {
        if (args[0] === 'sync') await interpreter(project);
        // Only the native-wheel probe is broken; the import probe now names each
        // module on its own line, so a substring match would hit verification too.
        if (args[0] === '-c' && args[1] === RUNTIME_NATIVE_IMPORT_PROBE)
          throw Error('broken native wheel');
        return report;
      });
    }
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
    );
    const calls = run.mock.calls;
    const syncIndex = calls.findIndex(([, args]) => args[0] === 'sync');
    const sync = calls[syncIndex]![1];
    expect(sync).toContain('--inexact');
    expect(sync[sync.indexOf('--python') + 1]).toBe(
      state === 'fresh' ? 'cpython-3.12-windows-x86_64' : runtimePython(project),
    );
    expect(sync).not.toContain('--reinstall-package');
    const excluded = sync.flatMap((arg, i) =>
      arg === '--no-install-package' ? [sync[i + 1]] : [],
    );
    expect(excluded).toEqual(
      expect.arrayContaining([
        'torch',
        'torchaudio',
        'torchvision',
        'ctranslate2',
        'nvidia-cudnn-cu12',
      ]),
    );
    expect(excluded).not.toContain('nvidia-ml-py');
    const overlayIndex = calls.findIndex(([, args]) =>
      args.includes('backend.core.windows_rocm.install'),
    );
    expect(overlayIndex).toBeGreaterThan(syncIndex);
    expect(calls[overlayIndex]![1]).toEqual([
      '-m',
      'backend.core.windows_rocm.install',
      '--uv',
      'uv',
    ]);
    expect(
      calls.slice(0, overlayIndex).some(([, args]) => args.join(' ').includes('import torch')),
    ).toBe(false);
    expect(
      calls
        .filter(([, args]) => args.join(' ').includes('import fastapi'))
        .every(
          ([, args]) =>
            args[1]?.indexOf('initialize_dll_search()')! < args[1]?.indexOf('import fastapi')!,
        ),
    ).toBe(true);
    expect(await readFile(join(project, 'scripts/windows-rocm-recipe.json'), 'utf8')).toBe(recipe);
    expect(await runtimeReady(bundle, project)).toBe(true);
  },
);
it.each(['wrong-version', 'wrong-architecture'])(
  'replaces a %s interpreter before native imports',
  async () => {
    const { bundle, project } = await fixture();
    await interpreter(project);
    const run = runner(project, true);
    await installRuntime(
      bundle,
      project,
      'uv',
      run,
      new AbortController().signal,
      undefined,
      'global',
    );
    const probe = run.mock.calls.find(
      ([, args]) => args[0] === '-c' && args[1]?.includes('validate_target()'),
    );
    expect(probe?.[1][1]).toContain('backend.core.windows_rocm.recipe import validate_target');
    const sync = run.mock.calls.find(([, args]) => args[0] === 'sync')![1];
    expect(sync[sync.indexOf('--python') + 1]).toBe('cpython-3.12-windows-x86_64');
  },
);
it('does not stamp a failed overlay and preserves models for retry', async () => {
  const { bundle, project } = await fixture();
  await mkdir(join(project, 'models'), { recursive: true });
  await writeFile(join(project, 'models/keep'), 'model');
  const run = runner(project);
  run.mockImplementation(async (_command, args) => {
    if (args.includes('backend.core.windows_rocm.install')) throw Error('verification failed');
    if (args[0] === 'sync') await interpreter(project);
    return report;
  });
  await expect(
    installRuntime(bundle, project, 'uv', run, new AbortController().signal, undefined, 'global'),
  ).rejects.toThrow('verification failed');
  expect(await runtimeReady(bundle, project)).toBe(false);
  expect(await readFile(join(project, 'models/keep'), 'utf8')).toBe('model');
  await expect(readFile(join(project, '.runtime-installing'))).resolves.toBeDefined();
});
