// @vitest-environment node
import { spawnSync } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { afterEach, expect, it } from 'vitest';
import { runRuntimeProbe } from './runtime-probe';

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
