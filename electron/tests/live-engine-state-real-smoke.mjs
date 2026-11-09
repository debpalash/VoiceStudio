import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const ui = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902';
const backend = process.env.VOICESTUDIO_BACKEND_URL || 'http://127.0.0.1:3900';

async function json(path) {
  const response = await fetch(backend + path, { signal: AbortSignal.timeout(15_000) });
  assert.equal(response.status, 200, `${path} returned ${response.status}`);
  return response.json();
}

const [
  health,
  engines,
  translation,
  diarisation,
  dictationPrefs,
  dictationModels,
  readiness,
  catalogue,
  setup,
  sysinfo,
  profiles,
  modelStatus,
] = await Promise.all([
  json('/health'),
  json('/engines'),
  json('/engines/translation'),
  json('/engines/diarisation'),
  json('/dictation/prefs'),
  json('/dictation/models'),
  json('/dictation/readiness'),
  json('/models'),
  json('/setup/status'),
  json('/sysinfo'),
  json('/profiles'),
  json('/model/status'),
]);

assert.equal(health.status, 'ok');
assert.equal(setup.models_ready, true);
assert.deepEqual(setup.missing, []);
if (modelStatus.status === 'ready') {
  assert.equal(modelStatus.loaded, true);
  assert.equal(modelStatus.checkpoint, engines.tts.active_model);
  assert.match(modelStatus.loaded_at || '', /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
}

const models = Array.isArray(catalogue) ? catalogue : catalogue.models || [];
const installed = new Set(models.filter((model) => model.installed).map((model) => model.repo_id));
assert(installed.has(engines.tts.active_model), 'Selected TTS model is not installed');
assert(installed.has(engines.asr.active_model), 'Selected ASR model is not installed');

const activeTranslation = translation.engines.find((engine) => engine.id === translation.active);
assert(activeTranslation?.ready, 'Selected translation engine is not ready');
assert.equal(diarisation.installed, true, 'Selected diarisation engine is not installed');
const activeDictation = dictationModels.models.find(
  (model) => model.id === dictationPrefs.model_id,
);
assert(activeDictation?.installed, 'Selected dictation model is not installed');
assert.equal(readiness.ready, true, 'Dictation is not ready');
assert(profiles.length > 0, 'No saved profiles are available for the live audit');
assert.deepEqual(
  profiles.filter((profile) => !String(profile.ref_text || '').trim()).map((profile) => profile.id),
  [],
  'A saved profile has no reference transcript',
);

const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
    localStorage.setItem('voicestudio.language', 'en');
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      const location = message.location().url;
      errors.push(location ? `${message.text()} (${location})` : message.text());
    }
  });
  page.on('response', (response) => {
    if (response.status() >= 500) errors.push(`${response.status()} ${response.url()}`);
  });

  await page.goto(ui + '/#/clone');
  const device = page.getByRole('button', { name: 'Local: Engine ready', exact: true });
  await device.waitFor({ timeout: 15_000 });
  await device.click();
  if (sysinfo.cpu_model)
    await page.getByText(sysinfo.cpu_model, { exact: true }).waitFor({ timeout: 10_000 });
  if (sysinfo.gpu_name)
    await page.getByText(sysinfo.gpu_name, { exact: true }).waitFor({ timeout: 10_000 });
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Models', exact: true }).click();
  const details = page.locator('#sidebar-engine-details');
  await details.getByText(engines.tts.active_model, { exact: true }).waitFor();
  await details.getByText(engines.asr.active_model, { exact: true }).waitFor();
  await details.getByText(activeTranslation.display_name, { exact: true }).waitFor();
  await details.getByText(activeDictation.label, { exact: true }).waitFor();
  await details.getByText(diarisation.model, { exact: true }).waitFor();

  for (const [family, expected] of [
    ['tts', engines.tts.active_model],
    ['asr', engines.asr.active_model],
    ['dictation', activeDictation.label],
    ['diarisation', diarisation.model],
    ['translation', activeTranslation.display_name],
  ]) {
    await page.goto(`${ui}/#/settings/models/${family}`);
    await page.getByText(expected, { exact: true }).first().waitFor({ timeout: 15_000 });
    await page
      .getByText('Loading…', { exact: true })
      .waitFor({ state: 'detached', timeout: 15_000 });
  }

  await page.goto(ui + '/#/transcriptions');
  await page.getByText(activeDictation.label, { exact: true }).waitFor({ timeout: 15_000 });
  assert.equal(await page.getByText('No speech-to-text model is installed').count(), 0);

  await page.goto(ui + '/#/personas');
  await page.getByText(profiles[0].name, { exact: true }).first().waitFor({ timeout: 15_000 });
  assert.deepEqual(errors, []);
  console.log(
    `PASS: live ${health.device}; TTS/ASR/translation/dictation/diarisation ready; ${profiles.length} transcript-backed profiles`,
  );
} finally {
  await browser.close();
}
