// @vitest-environment node
import { mkdirSync, mkdtempSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  handle: vi.fn(),
  fetch: vi.fn(),
  afterStat: null as null | (() => void),
}));
// Run a hook right after the handle's stat, to swap the file at the path the way a
// concurrent writer could between the size check and the read.
vi.mock('node:fs/promises', async () => {
  const actual = await vi.importActual<typeof import('node:fs/promises')>('node:fs/promises');
  return {
    ...actual,
    open: async (...args: Parameters<typeof actual.open>) => {
      const handle = await actual.open(...args);
      return new Proxy(handle, {
        get(target, key) {
          const value = Reflect.get(target, key, target);
          if (key !== 'stat') return typeof value === 'function' ? value.bind(target) : value;
          return async (...a: unknown[]) => {
            const info = await (value as (...x: unknown[]) => Promise<unknown>).apply(target, a);
            mocks.afterStat?.();
            return info;
          };
        },
      });
    },
  };
});
vi.mock('electron', () => ({
  protocol: { handle: mocks.handle },
  net: { fetch: mocks.fetch },
}));
import { APP_ORIGIN, installAppProtocol } from './protocol';

it('serves small and large static files from one open handle and 404s a directory', async () => {
  const root = mkdtempSync(join(tmpdir(), 'vs-proto-handle-'));
  try {
    mkdirSync(join(root, 'assets', 'dir.js'), { recursive: true });
    const large = Buffer.alloc(600 * 1024, 7);
    writeFileSync(join(root, 'index.html'), '<div id="root"></div>');
    writeFileSync(join(root, 'assets', 'small-1.js'), 'x'.repeat(100));
    writeFileSync(join(root, 'assets', 'large-1.js'), large);
    installAppProtocol(() => 'http://127.0.0.1:3900', root);
    const handler = mocks.handle.mock.lastCall![1];

    const small = await handler(new Request(`${APP_ORIGIN}/assets/small-1.js`));
    expect(small.headers.get('content-length')).toBe('100');
    expect((await small.text()).length).toBe(100);
    const cached = await handler(new Request(`${APP_ORIGIN}/assets/small-1.js`));
    expect((await cached.text()).length).toBe(100);

    const big = await handler(new Request(`${APP_ORIGIN}/assets/large-1.js`));
    expect(big.headers.get('content-length')).toBe(String(large.length));
    expect((await big.arrayBuffer()).byteLength).toBe(large.length);

    expect((await handler(new Request(`${APP_ORIGIN}/assets/dir.js`))).status).toBe(404);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

it.each([
  ['buffered', 100],
  ['streamed', 600 * 1024],
])('serves the bytes it inspected when the path is replaced mid-request (%s)', async (_kind, size) => {
  const root = mkdtempSync(join(tmpdir(), 'vs-proto-race-'));
  try {
    mkdirSync(join(root, 'assets'), { recursive: true });
    const target = join(root, 'assets', 'race-1.js');
    writeFileSync(join(root, 'index.html'), '<div id="root"></div>');
    writeFileSync(target, Buffer.alloc(size, 1));
    installAppProtocol(() => 'http://127.0.0.1:3900', root);
    const handler = mocks.handle.mock.lastCall![1];
    mocks.afterStat = () => {
      // Replace the directory entry; the open handle still refers to the old inode.
      renameSync(target, `${target}.old`);
      writeFileSync(target, Buffer.alloc(size * 2, 2));
    };
    const response = await handler(new Request(`${APP_ORIGIN}/assets/race-1.js`));
    mocks.afterStat = null;
    const bytes = Buffer.from(await response.arrayBuffer());
    expect(bytes.length).toBe(size);
    expect(bytes.every((byte) => byte === 1)).toBe(true);
  } finally {
    mocks.afterStat = null;
    rmSync(root, { recursive: true, force: true });
  }
});
