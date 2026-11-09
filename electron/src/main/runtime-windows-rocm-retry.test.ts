// @vitest-environment node
import { fixture, interpreter, report } from './runtime-windows-rocm.fixture';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import { installRuntime, runtimeReady } from './runtime-project';
import type { RuntimeRunner } from './runtime-project';
import { runRuntimeProbe } from './runtime-probe';

it('repairs broken sentencepiece metadata without importing or evicting HIP wheels', async () => {
  const { bundle, project } = await fixture();
  await interpreter(project);
  let synced = false;
  const run = vi.fn<RuntimeRunner>(async (_command, args, _cwd, _env, options) => {
    if (args[0] === 'sync') synced = true;
    if (!synced && args[1] === 'import sentencepiece') {
      expect(options?.timeoutMs).toBeGreaterThan(0);
      throw Error('damaged sentencepiece.pyd');
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
  );
  expect(
    run.mock.calls.find(([, args]) => args[1] === 'import sentencepiece')?.[4]?.timeoutMs,
  ).toBeGreaterThan(0);
  expect(run.mock.calls.find(([, args]) => args[0] === 'cache')?.[1]).toEqual([
    'cache',
    'clean',
    'sentencepiece',
  ]);
  const sync = run.mock.calls.find(([, args]) => args[0] === 'sync')![1];
  expect(
    sync.slice(sync.indexOf('--reinstall-package'), sync.indexOf('--reinstall-package') + 2),
  ).toEqual(['--reinstall-package', 'sentencepiece']);
  const overlay = run.mock.calls.findIndex(([, args]) =>
    args.includes('backend.core.windows_rocm.install'),
  );
  expect(
    run.mock.calls
      .slice(0, overlay)
      .some(([, args]) => /import (torch|ctranslate2)/.test(args[1] ?? '')),
  ).toBe(false);
  expect(await runtimeReady(bundle, project)).toBe(true);
});

it('requires bounded application imports and leaves interrupted setup retryable', async () => {
  const { bundle, project } = await fixture();
  await mkdir(join(project, 'models'), { recursive: true });
  await writeFile(join(project, 'models/keep'), 'model');
  let fail = true;
  let closed = false;
  const run = vi.fn<RuntimeRunner>(async (_command, args, _cwd, _env, options) => {
    if (args[1]?.includes('import fastapi')) {
      expect(options?.timeoutMs).toBeGreaterThan(0);
      return runRuntimeProbe(
        process.execPath,
        [
          '-e',
          fail
            ? "console.log('probe-started'); setInterval(() => {}, 1000)"
            : "console.log('retry-ready')",
        ],
        project,
        process.env,
        {
          timeoutMs: fail ? 1000 : 5000,
          onClose: () => {
            closed = true;
          },
        },
      );
    }
    if (args[0] === 'sync') await interpreter(project);
    return report;
  });
  await expect(
    installRuntime(bundle, project, 'uv', run, new AbortController().signal, undefined, 'global'),
  ).rejects.toMatchObject({ code: 'ETIMEDOUT', stdout: expect.stringContaining('probe-started') });
  expect(closed).toBe(true);
  expect(await runtimeReady(bundle, project)).toBe(false);
  await expect(readFile(join(project, '.runtime-ready'))).rejects.toThrow();
  await expect(readFile(join(project, '.runtime-installing'))).resolves.toBeDefined();
  expect(await readFile(join(project, 'models/keep'), 'utf8')).toBe('model');
  fail = false;
  await installRuntime(
    bundle,
    project,
    'uv',
    run,
    new AbortController().signal,
    undefined,
    'global',
  );
  expect(await runtimeReady(bundle, project)).toBe(true);
});
