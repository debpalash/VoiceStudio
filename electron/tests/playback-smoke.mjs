// Run against the development renderer: node electron/tests/playback-smoke.mjs
// All profile/audio/generation data is synthetic; no user data is changed.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { wave } from './test-wave.mjs';
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage();
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
    localStorage.setItem('voicestudio.language', 'en');
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const profile = {
    id: 'playback-fixture',
    name: 'Playback fixture',
    kind: 'clone',
    ref_audio_path: 'fixture.wav',
    created_at: 0,
  };
  await page.route('**/api/setup/status', (route) =>
    route.fulfill({ json: { models_ready: true, missing: [] } }),
  );
  await page.route('**/api/models/install/status', (route) =>
    route.fulfill({ json: { jobs: [] } }),
  );
  await page.route('**/api/engines', (route) =>
    route.fulfill({
      json: {
        tts: {
          active: 'omnivoice',
          active_model: 'playback-model',
          backends: [{ id: 'omnivoice', name: 'OmniVoice', available: true }],
        },
      },
    }),
  );
  await page.route('**/api/models', (route) =>
    route.fulfill({
      json: {
        target: 'local',
        models: [
          {
            repo_id: 'playback-model',
            label: 'Playback model',
            role: 'tts',
            size_gb: 1,
            installed: true,
            supported: true,
          },
        ],
      },
    }),
  );
  await page.route('**/api/profiles', (route) => route.fulfill({ json: [profile] }));
  const audio = (route) =>
    route.fulfill({
      body: wave,
      headers: { 'Content-Type': 'audio/wav', 'X-Audio-Id': 'fixture', 'X-Audio-Duration': '4' },
    });
  await page.route('**/api/profiles/playback-fixture/audio', audio);
  await page.route('**/api/generate', audio);
  await page.goto(
    new URL('/#/personas', process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902').href,
  );
  const profileButton = page.getByRole('button', { name: profile.name, exact: true }).first();
  await profileButton.waitFor({ timeout: 15_000 }).catch(async (error) => {
    throw new Error(
      `Saved profile did not render at ${page.url()}: ${(await page.locator('body').innerText()).slice(0, 500)}`,
      { cause: error },
    );
  });
  await profileButton.click();
  await page.waitForURL(/#\/clone$/);
  await page.getByRole('textbox', { name: 'Script', exact: true }).fill('Playback verification');
  await page.getByRole('button', { name: 'Synthesize audio', exact: true }).click();
  const latest = page.getByRole('region', { name: 'Latest take' });
  await latest.getByRole('button', { name: 'Play', exact: true }).click();
  await latest.getByRole('button', { name: 'Pause', exact: true }).click();
  // Wait for the provider's pause event before seeking (not merely native paused).
  await latest.getByRole('button', { name: 'Play', exact: true }).waitFor();
  await latest.getByRole('slider', { name: 'Seek' }).fill('1');
  await page.waitForFunction(() =>
    [...document.querySelectorAll('audio')].some((audio) => audio.currentTime >= 0.9),
  );
  await latest.getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByRole('button', { name: 'Preview voice', exact: true }).first().click();
  await latest.getByRole('button', { name: 'Play', exact: true }).waitFor();
  await page.waitForFunction(
    () => [...document.querySelectorAll('audio')].filter((audio) => !audio.paused).length === 1,
  );
  await latest.getByRole('button', { name: 'Close', exact: true }).click();
  assert.equal(await latest.count(), 0);
  assert.deepEqual(errors, []);
  console.log('PASS: Vidstack play, pause, seek, exclusive previews and dismiss');
} finally {
  await browser.close();
}
