// @vitest-environment node
import { spawnSync } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { afterEach, expect, it } from 'vitest';
import { runRuntimeProbe, withFields } from './runtime-probe';

const children: ChildProcess[] = [];
afterEach(() => {
  for (const child of children.splice(0)) {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  }
});

it('terminates a non-terminating native-probe stand-in and awaits close before retry', async () => {
  let closed = false;
  let child: ChildProcess | undefined;
  const failure = runRuntimeProbe(
    process.execPath,
    [
      '-e',
      "process.on('SIGTERM', () => {}); console.log('probe-started'); setInterval(() => {}, 1000)",
    ],
    process.cwd(),
    process.env,
    {
      timeoutMs: 1000,
      onSpawn: (spawned) => {
        children.push(spawned);
        child = spawned;
        spawned.once('close', () => {
          closed = true;
        });
      },
    },
  );
  await expect(failure).rejects.toMatchObject({
    code: 'ETIMEDOUT',
    stdout: expect.stringContaining('probe-started'),
  });
  expect(closed).toBe(true);
  expect(child?.exitCode !== null || child?.signalCode !== null).toBe(true);
  if (process.platform === 'win32') {
    const result = spawnSync('tasklist', ['/FI', `PID eq ${child!.pid}`, '/FO', 'CSV', '/NH'], {
      encoding: 'utf8',
    });
    expect(result.stdout).not.toContain(`"${child!.pid}"`);
  } else expect(() => process.kill(child!.pid!, 0)).toThrow();
  expect(
    await runRuntimeProbe(
      process.execPath,
      ['-e', "console.log('retry-ready')"],
      process.cwd(),
      process.env,
      { timeoutMs: 5000 },
    ),
  ).toContain('retry-ready');
});

it('rejects with the abort reason instead of throwing when a running probe is cancelled', async () => {
  const controller = new AbortController();
  const failure = runRuntimeProbe(
    process.execPath,
    ['-e', "console.log('probe-started'); setInterval(() => {}, 1000)"],
    process.cwd(),
    process.env,
    {
      timeoutMs: 30_000,
      signal: controller.signal,
      onSpawn: (spawned) => {
        children.push(spawned);
        spawned.stdout?.once('data', () => controller.abort());
      },
    },
  );
  // The default abort reason is a DOMException: its `code` has only a getter.
  const error = await failure.catch((reason: unknown) => reason);
  expect(error).toBe(controller.signal.reason);
  expect(error).toMatchObject({
    name: 'AbortError',
    stdout: expect.stringContaining('probe-started'),
  });
});

it('sets fields over getter-only accessors without throwing', () => {
  const error = new DOMException('cancelled', 'AbortError');
  expect(() => Object.assign(error, { code: 1 })).toThrow(TypeError);
  expect(withFields(error, { code: 'X', stdout: 'out' })).toMatchObject({
    code: 'X',
    stdout: 'out',
  });
  expect(withFields(Object.freeze(new Error('frozen')), { code: 'X' }).message).toBe('frozen');
});
