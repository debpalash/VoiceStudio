// @vitest-environment node
import { fixture, runner, recipe, report } from './runtime-windows-rocm.fixture';
import { execFile } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import {
  installRuntime,
  runtimeReady,
  runtimeCompatible,
  runtimeDependenciesReady,
} from './runtime-project';

it('recipe bytes invalidate both readiness and compatible reuse', async () => {
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
  expect(await runtimeCompatible(bundle, project)).toBe(true);
  await writeFile(join(bundle, 'scripts/windows-rocm-recipe.json'), recipe + '\n');
  expect(await runtimeReady(bundle, project)).toBe(false);
  expect(await runtimeCompatible(bundle, project)).toBe(false);
});
it('does not trust a staged recipe changed after readiness was stamped', async () => {
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
  await writeFile(join(project, 'scripts/windows-rocm-recipe.json'), recipe + '\n');
  expect(await runtimeReady(bundle, project)).toBe(false);
  expect(await runtimeCompatible(bundle, project)).toBe(false);
});
it.each([Object.assign(Error('timeout'), { killed: true, signal: 'SIGTERM' }), Error('crash')])(
  'never accepts a failed or timed out Windows HIP verification',
  async (failure) => {
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
    vi.mocked(execFile).mockImplementation(((
      _c: unknown,
      _a: unknown,
      _o: unknown,
      callback: (error: Error) => void,
    ) => callback(failure)) as never);
    expect(await runtimeReady(bundle, project)).toBe(false);
    expect(await runtimeCompatible(bundle, project)).toBe(false);
    expect(await runtimeDependenciesReady(project)).toBe(false);
  },
);

it('also fails closed when native verification passes but application imports time out', async () => {
  const { project } = await fixture();
  vi.stubEnv('PYTHONHOME', '/unrelated');
  vi.stubEnv('PYTHONPATH', '/unrelated');
  vi.mocked(execFile).mockImplementation(((
    _command: unknown,
    args: string[],
    options: { env: NodeJS.ProcessEnv },
    callback: (error: Error | null, stdout: string, stderr: string) => void,
  ) => {
    expect(options.env.PYTHONHOME).toBeUndefined();
    expect(options.env.PYTHONPATH).toBeUndefined();
    expect(options.env.HF_HUB_OFFLINE).toBe('1');
    if (args.includes('--verify-only')) callback(null, report, '');
    else {
      expect(args[1]).toMatch(/^from backend.core.windows_rocm.runtime/);
      callback(Object.assign(Error('timeout'), { killed: true, signal: 'SIGTERM' }), '', '');
    }
  }) as never);
  expect(await runtimeDependenciesReady(project)).toBe(false);
});
