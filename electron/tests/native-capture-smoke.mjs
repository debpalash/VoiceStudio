import { _electron as electron } from 'playwright';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import assert from 'node:assert/strict';
import { startHealthBackend } from './fake-health-backend.mjs';
const profile = mkdtempSync(join(tmpdir(), 'voicestudio-capture-check-'));
const wayland = process.platform === 'linux' && process.env.VOICESTUDIO_TEST_WAYLAND === '1';
const packagedExecutable = process.env.VOICESTUDIO_PACKAGED_EXE;
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
    '--use-fake-device-for-media-stream',
    '--use-fake-ui-for-media-stream',
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
  const main = await app.firstWindow();
  await main.waitForFunction(() => window.voicestudio?.capture);
  // Exercise the real tray callback while avoiding physical mouse/focus changes.
  await app.evaluate(({ Tray }) => {
    const original = Tray.prototype.setContextMenu;
    Tray.prototype.setContextMenu = function (menu) {
      globalThis.captureTestMenu = menu;
      return original.call(this, menu);
    };
  });
  await main.evaluate(() =>
    window.voicestudio.capture.labels({
      show: 'Show VoiceStudio',
      start: 'Start dictation',
      stop: 'Stop recording',
      settings: 'Settings',
      exit: 'Exit',
    }),
  );
  await assert.rejects(
    () => main.evaluate(() => window.voicestudio.capture.accept(1)),
    /Untrusted recorder/,
  );
  // Capture transport is the subject of this smoke, not model installation.
  await app.context().route(/\/dictation\/prefs$/, (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ enabled: true, mode: 'toggle', model_id: 'capture-smoke' }),
    }),
  );
  await app.context().route(/\/dictation\/models$/, (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        engine_available: true,
        models: [{ id: 'capture-smoke', installed: true }],
      }),
    }),
  );
  // Native output is never exercised against an uncontrolled desktop target.
  let transcript = '';
  // First session has no speech; the second exercises history with intercepted output.
  await app.context().routeWebSocket(/\/ws\/transcribe/, (peer) => {
    peer.onMessage((message) => {
      if (message === 'EOF')
        peer.send(JSON.stringify({ type: 'final', final_kind: 'summary', text: transcript }));
    });
  });
  const nextWindow = app.waitForEvent('window');
  await app.evaluate(() =>
    globalThis.captureTestMenu.items
      .find((item) => item.label.startsWith('Start dictation'))
      .click(),
  );
  const widget = await nextWindow;
  const errors = [];
  widget.on('pageerror', (error) => errors.push(error.message));
  try {
    await widget.getByRole('button', { name: 'Stop', exact: true }).waitFor({ timeout: 30000 });
  } catch (error) {
    console.error(
      'Recorder did not become ready:',
      await widget.evaluate(() => ({
        url: location.href,
        text: document.body.innerText.slice(0, 1200),
        bridge: typeof window.voicestudio?.capture,
      })),
      errors,
    );
    throw error;
  }
  await widget.getByRole('button', { name: 'Pause', exact: true }).click();
  await widget.getByRole('button', { name: 'Resume', exact: true }).click();
  await widget.getByRole('button', { name: 'Stop', exact: true }).click();
  await widget.getByText('No speech detected', { exact: true }).waitFor();
  await widget.getByRole('button', { name: 'Cancel', exact: true }).click();
  await app.evaluate(async ({ BrowserWindow }) => {
    const win = BrowserWindow.getAllWindows().find((window) =>
      window.webContents.getURL().endsWith('#/capture'),
    );
    for (let i = 0; i < 50 && win.isVisible(); i++)
      await new Promise((resolve) => setTimeout(resolve, 20));
    if (win.isVisible()) throw new Error('Recorder did not hide');
  });
  // Intercept only delivery: lifecycle still uses the real helper and native IPC.
  await app.evaluate(({ ipcMain }) => {
    globalThis.captureDeliveries = [];
    ipcMain.removeHandler('capture:deliver');
    ipcMain.handle('capture:deliver', (_event, session, sequence, text) => {
      globalThis.captureDeliveries.push({ session, sequence, text });
      return 'copied';
    });
  });
  transcript = 'Controlled capture transcript.';
  // Reuse the hidden recorder and check its listener survives a complete session.
  await app.evaluate(() =>
    globalThis.captureTestMenu.items
      .find((item) => item.label.startsWith('Start dictation'))
      .click(),
  );
  await widget.getByRole('button', { name: 'Stop', exact: true }).waitFor();
  await widget.getByRole('button', { name: 'Cancel', exact: true }).click();
  await app.evaluate(() =>
    globalThis.captureTestMenu.items
      .find((item) => item.label.startsWith('Start dictation'))
      .click(),
  );
  await widget.getByRole('button', { name: 'Stop', exact: true }).waitFor();
  await widget.getByRole('button', { name: 'Stop', exact: true }).click();
  await widget.getByText('Copied to clipboard', { exact: true }).waitFor();
  const delivered = await app.evaluate(() => globalThis.captureDeliveries);
  assert.equal(delivered.length, 1);
  assert.equal(delivered[0].text, transcript);
  assert.equal(delivered[0].sequence, 0);
  const stored = await main.evaluate(() =>
    JSON.parse(localStorage.getItem('omni_transcriptions') || '[]'),
  );
  assert(stored.some((entry) => entry.text === transcript));
  await widget.screenshot({ path: join(profile, 'recorder.png') });
  await widget.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.deepEqual(errors, []);
  console.log(
    `PASS: ${packagedExecutable ? 'packaged ' : ''}native ${process.platform}${wayland ? ' Wayland' : ''} tray recorder, real helper acceptance, pause/stop/cancel/reopen, main-frame output denial, transcript history and intercepted output; no physical microphone or text insertion`,
  );
} finally {
  await app.close();
  await health.close();
}
