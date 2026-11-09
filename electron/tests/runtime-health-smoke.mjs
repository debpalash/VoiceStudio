import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const strings = JSON.parse(
  await readFile(new URL('../src/renderer/src/i18n/locales/en.json', import.meta.url), 'utf8'),
);
const t = (key) => key.split('.').reduce((value, part) => value[part], strings);
let available = true;
let modelStatus = { status: 'loading', sub_stage: 'loading_weights', error: null };
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1180, height: 760 } });
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/engines', (route) =>
    route.fulfill({
      json: {
        tts: {
          active: 'omnivoice',
          active_model: 'k2-fsa/OmniVoice',
          backends: [
            {
              id: 'omnivoice',
              display_name: 'VoiceStudio',
              available,
              reason: available ? null : 'Model missing',
            },
          ],
        },
        asr: { active: null, active_model: null, backends: [] },
        llm: { active: null, active_model: null, backends: [] },
      },
    }),
  );
  await page.route('**/api/model/status', (route) => route.fulfill({ json: modelStatus }));
  await page.route('**/api/engines/translation', (route) =>
    route.fulfill({ json: { active: null, engines: [] } }),
  );
  await page.route('**/api/dictation/prefs', (route) =>
    route.fulfill({ json: { enabled: false, model_id: '' } }),
  );
  await page.route('**/api/dictation/models', (route) =>
    route.fulfill({ json: { engine_available: false, models: [] } }),
  );

  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902';
  await page.goto(base + '/#/clone');
  const localDevice = (state) =>
    page.getByRole('button', {
      name: `${t('engineSidebar.localShort')}: ${state}`,
      exact: true,
    });
  await localDevice(t('engineRuntime.loading')).waitFor();

  modelStatus = { status: 'ready', sub_stage: 'ready', error: null };
  await page.reload();
  await localDevice(t('backend.ready')).waitFor();

  available = false;
  modelStatus = { status: 'idle', sub_stage: null, error: null };
  await page.reload();
  await localDevice(`${t('engineSidebar.tts')} · ${t('modelSettings.unavailable')}`).waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS: sidebar reports loading, ready, and unavailable TTS states');
} finally {
  await browser.close();
}
