import { readFileSync } from 'node:fs';
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
    Object.defineProperty(navigator.mediaDevices, 'enumerateDevices', {
      configurable: true,
      value: async () => [
        {
          deviceId: 'fixture-mic',
          kind: 'audioinput',
          label: 'Studio microphone',
          groupId: 'fixture',
        },
      ],
    });
  });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const config = {
    auto: false,
    smart_cleanup: true,
    self_correction: true,
    preserve_technical: true,
    llm_ready: true,
  };
  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902';
  await page.route('**/api/api/settings/dictation-refinement', (route) => {
    if (route.request().method() === 'PUT') Object.assign(config, route.request().postDataJSON());
    return route.fulfill({ json: config });
  });
  await page.goto(base + '/#/settings/models/dictation');
  const cleanup = page.getByRole('switch', { name: 'Dictation cleanup', exact: true });
  await cleanup.waitFor();
  const filler = page.getByRole('switch', { name: 'Remove filler words', exact: true });
  assert(await filler.isDisabled());
  await cleanup.click();
  await page.waitForTimeout(100);
  assert.equal(config.auto, true);
  assert(await filler.isEnabled());
  let dictationReady = false;
  let dictationInstalled = false;
  const installs = [];
  await page.route('**/api/dictation/readiness', (route) =>
    route.fulfill({
      json: {
        ready: dictationReady,
        missing: dictationReady
          ? null
          : {
              error: 'asr_model_missing',
              recommended: {
                repo_id: 'fixture/dictation',
                label: 'Fixture dictation',
                size_gb: 0.04,
                dictation_id: 'fixture-dictation',
              },
            },
      },
    }),
  );
  await page.route('**/api/dictation/models', (route) =>
    route.fulfill({
      json: {
        engine_available: true,
        models: [
          {
            id: 'fixture-dictation',
            repo_id: 'fixture/dictation',
            label: 'Fixture dictation',
            tag: 'streaming',
            recommended: true,
            size_gb: 0.04,
            languages: 'English',
            installed: dictationInstalled,
          },
        ],
      },
    }),
  );
  await page.route('**/api/dictation/prefs', (route) =>
    route.fulfill({ json: { enabled: true, model_id: 'fixture-dictation' } }),
  );
  await page.route('**/api/models/install', (route) => {
    installs.push(route.request().postDataJSON());
    dictationInstalled = true;
    dictationReady = true;
    return route.fulfill({ json: { status: 'started' } });
  });
  await page.route('**/api/models/install/status', (route) =>
    route.fulfill({ json: { jobs: [] } }),
  );
  let refine;
  await page.route('**/api/transcribe', (route) => {
    refine = route.request().postData();
    return route.fulfill({
      json: {
        text: 'um original',
        refined_text: 'Original.',
        language: 'en',
        segments: [{ start: 0, end: 2, text: 'um original' }],
      },
    });
  });
  await page.goto(base + '/#/transcriptions');
  // Hash navigation reuses the Settings QueryClient. Reload so this workflow
  // exercises the readiness fixtures as a fresh app mount.
  await page.reload();
  await page.getByText('No speech-to-text model is installed', { exact: false }).waitFor();
  await page.getByRole('button', { name: /Download Fixture dictation/ }).click();
  await page.getByRole('button', { name: 'Upload audio', exact: true }).click({ trial: true });
  assert.deepEqual(installs, [{ repo_id: 'fixture/dictation' }]);
  await page.locator('summary').filter({ hasText: 'Input device' }).click();
  await page.getByRole('combobox', { name: 'Input device', exact: true }).click();
  await page.getByRole('option', { name: 'Studio microphone', exact: true }).click();
  await page.getByRole('combobox', { name: 'Channels', exact: true }).click();
  await page.getByRole('option', { name: 'Mono', exact: true }).click();
  assert.match(
    await page.getByRole('combobox', { name: 'Input device', exact: true }).innerText(),
    /Studio microphone/,
  );

  await page
    .locator('input[type=file]')
    .setInputFiles({ name: 'sample.wav', mimeType: 'audio/wav', buffer: wave });
  await page.getByText('Original.', { exact: true }).first().waitFor();
  assert(/name="refine"\r\n\r\ntrue/.test(refine));
  await page.locator('summary').filter({ hasText: 'Original transcript' }).click();
  await page.getByText('um original', { exact: true }).waitFor();
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('omni_transcriptions')));
  assert.equal(stored[0].text, 'um original');
  assert.equal(stored[0].refined_text, 'Original.');
  await page
    .locator('summary')
    .filter({ hasText: /^Segments$/ })
    .click();
  await page.getByText(/0\.0s.*2\.0s/).waitFor();
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export all', exact: true }).click();
  const output = readFileSync(await (await downloaded).path(), 'utf8');
  assert.match(output, /\(en\)\nOriginal\./);
  await page.getByRole('button', { name: 'Clear all', exact: true }).click();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.equal(
    await page.evaluate(() => JSON.parse(localStorage.getItem('omni_transcriptions')).length),
    1,
  );
  await page.getByRole('button', { name: 'Clear all', exact: true }).click();
  await page.evaluate(() => {
    window.restoreStorageWrite = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === 'omni_transcriptions') throw new Error('storage unavailable');
      return window.restoreStorageWrite.call(this, key, value);
    };
  });
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  assert.equal(
    await page.evaluate(() => JSON.parse(localStorage.getItem('omni_transcriptions')).length),
    1,
  );
  await page.evaluate(() => {
    Storage.prototype.setItem = window.restoreStorageWrite;
  });
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  assert.deepEqual(
    await page.evaluate(() => JSON.parse(localStorage.getItem('omni_transcriptions'))),
    [],
  );
  assert.deepEqual(errors, []);
  console.log(
    'PASS: dictation cleanup settings, disabled options, opted-in request and preserved raw/refined history',
  );
} finally {
  await browser.close();
}
