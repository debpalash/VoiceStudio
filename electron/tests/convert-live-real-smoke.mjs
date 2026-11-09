import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
    localStorage.setItem('voicestudio.locale', 'en');
  });
  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
  const [engines, models, profiles] = await Promise.all(
    ['/engines/tts', '/models', '/profiles'].map(async (path) => {
      const response = await page.request.get(base + '/api' + path);
      assert.ok(response.ok());
      return response.json();
    }),
  );
  assert.ok(
    models.models.some((m) => m.repo_id === engines.active_model && m.installed),
    'TTS must already be installed',
  );
  const profile = profiles.find(
    (p) =>
      p.kind === 'clone' &&
      p.ref_audio_path &&
      (!process.env.VOICESTUDIO_LIVE_PROFILE || p.id === process.env.VOICESTUDIO_LIVE_PROFILE),
  );
  assert.ok(profile, 'An existing clone profile is required');
  await page.goto(base + '/#/tools');
  await page
    .locator('[data-slot=secondary-sidebar]')
    .getByRole('button', { name: 'Convert', exact: true })
    .click();
  const source = page.getByRole('region', { name: 'Source clip', exact: true });
  const sample = await page.request.get(
    base + '/api/demo_audio/dictation/en_conversational.wav',
  );
  assert.ok(sample.ok());
  await source
    .locator('input[type=file]')
    .setInputFiles({ name: 'sample.wav', mimeType: 'audio/wav', buffer: await sample.body() });
  const target = page.getByRole('region', { name: 'Target voice', exact: true });
  await target.getByRole('button', { name: profile.name, exact: true }).click();
  const response = page.waitForResponse((r) => r.url().endsWith('/api/convert'), {
    timeout: 120000,
  });
  await page.locator('main').getByRole('button', { name: 'Convert', exact: true }).last().click();
  const res = await response;
  const data = await res.json();
  console.log(JSON.stringify({ status: res.status(), data }));
  assert.ok(res.status() === 200 || res.status() === 409);
  if (res.status() === 409) {
    assert.equal(data.detail.error, 'asr_model_missing');
    const alert = page.getByRole('alert');
    await alert.getByRole('link', { name: 'Models', exact: true }).waitFor();
    assert.ok(!(await alert.innerText()).includes('{'));
    console.log(
      'Missing selected ASR recovery verified; real conversion success remains unverified.',
    );
  }
  if (res.status() === 200) {
    assert.ok(data.text && data.duration_s > 0);
    const result = page.getByRole('region', { name: 'Converted take', exact: true });
    await result.getByRole('button', { name: 'Play', exact: true }).click();
    await result.getByRole('button', { name: 'Pause', exact: true }).waitFor();
    const advanced = await result.locator('input[type=range]').evaluate(async (slider) => {
      const deadline = performance.now() + 10_000;
      while (Number(slider.value) <= 0 && performance.now() < deadline)
        await new Promise((resolve) => setTimeout(resolve, 50));
      return Number(slider.value) > 0;
    });
    assert.ok(advanced, 'converted playback clock did not advance');
    console.log('Real converted take playback passed.');
  }
} finally {
  await browser.close();
}
