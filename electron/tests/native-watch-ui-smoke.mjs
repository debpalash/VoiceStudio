import { _electron as electron } from 'playwright';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import assert from 'node:assert/strict';
import { startHealthBackend } from './fake-health-backend.mjs';
const profile = mkdtempSync(join(tmpdir(), 'voicestudio-watch-ui-'));
const folder = mkdtempSync(join(tmpdir(), 'voicestudio-watch-ui-folder-'));
writeFileSync(join(folder, 'existing.mp4'), 'existing fixture: must never enqueue');
const wayland = process.platform === 'linux' && process.env.VOICESTUDIO_TEST_WAYLAND === '1';
const packagedExecutable = process.env.VOICESTUDIO_PACKAGED_EXE;
const extractAndRun = process.env.APPIMAGE_EXTRACT_AND_RUN === '1';
const health = await startHealthBackend();
const app = await electron.launch({
  executablePath:
    packagedExecutable ||
    resolve(
      'electron/node_modules/electron/dist',
      process.platform === 'win32' ? 'electron.exe' : 'electron',
    ),
  args: [
    ...(packagedExecutable ? [] : [resolve('electron/out/main/index.js')]),
    '--user-data-dir=' + profile,
    ...(wayland ? ['--ozone-platform=wayland'] : []),
  ],
  env: {
    ...process.env,
    ELECTRON_RENDERER_URL: '',
    OMNIVOICE_PORT: String(health.port),
    VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: '1',
    VOICESTUDIO_ELECTRON_PROXY_PORT: process.env.VOICESTUDIO_ELECTRON_PROXY_PORT || '0',
    VOICESTUDIO_SKIP_BACKEND: '1',
  },
  timeout: 30000,
});
try {
  const page = await app.firstWindow();
  const failures = [];
  const pageErrors = [];
  const consoleErrors = [];
  page.on('response', (response) => {
    if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`);
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.waitForFunction(() => window.voicestudio?.watch);
  await page.evaluate(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
  await page.reload();
  await app.evaluate(({ dialog }, folder) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] });
  }, folder);
  await page.evaluate(() => {
    window.location.hash = '/batch';
  });
  const clickButton = async (name) => {
    const button = page.getByRole('button', { name, exact: true });
    if (extractAndRun) await button.dispatchEvent('click');
    else await button.click();
  };
  try {
    await clickButton('Watch folder');
  } catch (error) {
    const showLog = page.getByRole('button', { name: 'Show log', exact: true });
    if (await showLog.isVisible().catch(() => false)) await showLog.click();
    console.error(
      'Batch watch controls did not become ready:',
      await page.evaluate(() => ({
        url: location.href,
        text: document.body.innerText.slice(0, 1600),
      })),
      failures,
      pageErrors,
      consoleErrors,
    );
    throw error;
  }
  await page.getByRole('button', { name: 'Pause', exact: true }).waitFor();
  await page.getByText('0 auto-added', { exact: true }).waitFor();
  await clickButton('Pause');
  await page.getByRole('button', { name: 'Resume', exact: true }).waitFor();
  if (!extractAndRun) await page.screenshot({ path: join(profile, 'watch-folder.png') });
  await clickButton('Stop');
  await page.getByRole('button', { name: 'Watch folder', exact: true }).waitFor();
  const revoked = await page.evaluate(async () => {
    try {
      await window.voicestudio.watch.scan('invented');
      return false;
    } catch {
      return true;
    }
  });
  assert.equal(revoked, true);
  console.log(
    `${packagedExecutable ? 'Packaged ' : ''}native ${process.platform}${wayland ? ' Wayland' : ''} watch UI start, priming, pause and stop passed. Screenshot: ` +
      (extractAndRun ? 'skipped for AppImage extract-and-run' : join(profile, 'watch-folder.png')),
  );
} finally {
  await app.close();
  await health.close();
}
