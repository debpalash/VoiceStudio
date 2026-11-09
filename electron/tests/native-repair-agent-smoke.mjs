import { _electron as electron } from 'playwright';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const executable =
  process.platform === 'win32'
    ? join(workspaceRoot, 'electron/node_modules/electron/dist/electron.exe')
    : resolve(
        workspaceRoot,
        'electron/node_modules/electron/dist',
        process.platform === 'darwin' ? 'Electron.app/Contents/MacOS/Electron' : 'electron',
      );
const launchEnvironment = { ...process.env };
delete launchEnvironment.ELECTRON_RUN_AS_NODE;
const app = await electron.launch({
  executablePath: executable,
  args: [
    join(workspaceRoot, 'electron/out/main/index.js'),
    '--user-data-dir=' + mkdtempSync(join(tmpdir(), 'voicestudio-repair-')),
  ],
  env: {
    ...launchEnvironment,
    ELECTRON_RENDERER_URL: '',
    VOICESTUDIO_ELECTRON_PROXY_PORT: '0',
    VOICESTUDIO_SKIP_BACKEND: '1',
    VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: '1',
  },
});

try {
  const page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  await page.waitForFunction(() => Boolean(window.voicestudio?.repair));
  const state = await page.evaluate(() => window.voicestudio.repair.getState());
  assert.equal(state.workspaceAvailable, true);
  assert.ok(state.workspacePath?.endsWith('VoiceStudio'));

  const agents = await page.evaluate(() => window.voicestudio.repair.list());
  assert.deepEqual(
    agents.map(({ id }) => id),
    ['codex', 'claude', 'opencode', 'pi'],
  );
  assert.ok(agents.some(({ available }) => available));

  await page.evaluate(() => {
    window.location.hash = '/settings/updates';
  });
  const launcher = page.getByRole('button', { name: 'Ask VoiceStudio Agent' });
  const launcherBounds = await launcher.boundingBox();
  const viewport = await page.evaluate(() => ({
    width: window.innerWidth,
    height: window.innerHeight,
  }));
  assert.ok(launcherBounds && viewport);
  assert.ok(Math.abs(launcherBounds.y + launcherBounds.height / 2 - viewport.height / 2) <= 2);
  assert.ok(viewport.width - launcherBounds.x - launcherBounds.width >= 12);
  assert.ok(viewport.width - launcherBounds.x - launcherBounds.width <= 20);
  await launcher.click();
  await page.getByRole('region', { name: 'Ask VoiceStudio Agent' }).waitFor();
  for (const agent of agents) {
    const choice = page.getByRole('button', { name: agent.label, exact: true });
    await choice.waitFor();
    assert.equal(await choice.isDisabled(), !agent.available);
  }

  await page.getByRole('button', { name: 'Autopilot', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Audiobook', exact: true }).click();
  assert.ok((await page.getByRole('textbox').inputValue()).includes('Audiobook'));
  assert.equal(await page.getByRole('button', { name: 'Send', exact: true }).isEnabled(), true);
  if (process.env.VOICESTUDIO_AGENT_SCREENSHOT) await page.screenshot({ path: process.env.VOICESTUDIO_AGENT_SCREENSHOT });
  await page.evaluate(() => localStorage.setItem('voicestudio.locale', 'fr'));
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByText('Canal de mise à jour', { exact: true }).first().waitFor();
  await page.getByRole('button', { name: 'Aperçu', exact: true }).waitFor();
  assert.equal((await page.locator('body').innerText()).includes('about.channel_'), false);
  console.log('Repair-agent bridge, detected-agent dock, and localized update channels passed.');
} finally {
  await app.close();
}
