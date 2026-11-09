import { expect, it, vi } from 'vitest';
import { collectHostSnapshot } from './host-snapshot';

const fail = (code: string) => Object.assign(new Error(code), { code });
const ok = { bavail: 1000, bsize: 1024 ** 3 };
const run = (statfs: unknown) =>
  collectHostSnapshot({
    runtimeProject: '/a/b/c',
    runtimePython: null,
    uv: null,
    statfs: statfs as never,
  });

it('does not borrow a parent filesystem when the path exists but is unreadable', async () => {
  const statfs = vi.fn().mockRejectedValueOnce(fail('EACCES')).mockResolvedValue(ok);
  expect((await run(statfs)).diskFreeGb).toBeNull();
});

it('walks to the parent only when the path is absent', async () => {
  const statfs = vi.fn().mockRejectedValueOnce(fail('ENOENT')).mockResolvedValue(ok);
  expect((await run(statfs)).diskFreeGb).toBe(1000);
});
