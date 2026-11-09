import { _electron as electron } from 'playwright';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const app = await electron.launch({
  executablePath: join(workspaceRoot, 'electron/node_modules/electron/dist/electron.exe'),
  args: [
    join(workspaceRoot, 'electron/out/main/index.js'),
    '--user-data-dir=' + mkdtempSync(join(tmpdir(), 'voicestudio-report-')),
  ],
  env: {
    ...process.env,
    ELECTRON_RENDERER_URL: '',
    VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: '1',
    VOICESTUDIO_ELECTRON_PROXY_PORT: '0',
  },
});
try {
  await app.evaluate(({ shell }) => {
    globalThis.reportUrls = [];
    shell.openExternal = async (url) => {
      globalThis.reportUrls.push(url);
    };
  });
  const page = await app.firstWindow();
  await page.evaluate(() => {
    window.location.hash = '/settings/support';
  });
  const report = page.getByRole('button', { name: 'Report a bug', exact: true });
  await report.waitFor();
  assert.deepEqual(await app.evaluate(() => globalThis.reportUrls), []);
  await report.click();

  let urls = [];
  for (let i = 0; i < 100; i++) {
    urls = await app.evaluate(() => globalThis.reportUrls);
    if (urls.length) break;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.equal(urls.length, 1);
  const url = new URL(urls[0]);
  assert.equal(url.hostname, 'github.com');
  const body = url.searchParams.get('body');
  assert.ok(body.includes('**Shell:** Electron'));
  assert.ok(body.includes('## Environment'));
  console.log(
    'Native report diagnostics and external IPC passed; browser launch intercepted, no report submitted.',
  );
} finally {
  await app.close();
}
