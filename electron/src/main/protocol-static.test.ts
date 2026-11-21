// @vitest-environment node
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ handle: vi.fn(), fetch: vi.fn() }));
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
