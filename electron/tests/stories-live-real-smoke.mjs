import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
    localStorage.setItem('voicestudio.language', 'en');
  });
  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
  const profiles = await (await page.request.get(base + '/api/profiles')).json();
  const [tts, models] = await Promise.all(
    ['/engines/tts', '/models'].map(async (p) =>
      (await page.request.get(base + '/api' + p)).json(),
    ),
  );
  assert.ok(models.models.some((m) => m.repo_id === tts.active_model && m.installed));
  const narrator = profiles.find((p) => p.kind === 'design');
  const actor = profiles.find((p) => p.kind === 'clone' && p.ref_audio_path);
  assert.ok(narrator && actor, 'Two existing voice profiles required');
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(base + '/#/stories');
  const side = page.locator('[data-slot=secondary-sidebar]');
  await side.getByRole('button', { name: narrator.name, exact: true }).click();
  await page.getByRole('button', { name: /Add (?:First )?Line/, exact: true }).click();
  await page.getByRole('textbox', { name: /Enter dialogue/ }).fill('Welcome to our short story.');
  await side.locator('summary').filter({ hasText: /^Cast/ }).click();
  await side.getByRole('button', { name: 'Add character', exact: true }).click();
  await side.getByLabel('Character name', { exact: true }).fill('Guest');
  const character = side
    .locator('[data-slot=cast-member]')
    .filter({ has: page.getByLabel('Character name', { exact: true }) })
    .first();
  await character.getByRole('button', { name: 'Voice: Default', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: actor.name, exact: true }).click();
  await page.getByRole('button', { name: /Add (?:First )?Line/, exact: true }).click();
  const line = page.getByRole('textbox', { name: /Enter dialogue/ }).last();
  await line.fill('Thank you. It is lovely to be here.');
  await page
    .locator('[data-slot=story-block]')
    .last()
    .getByRole('button', { name: 'Speaker: Narrator', exact: true })
    .click();
  await page.getByRole('dialog').getByRole('button', { name: 'Guest', exact: true }).click();
  const request = page.waitForRequest((r) => r.url().endsWith('/longform/render'));
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const body = (await request).postDataJSON();
  assert.equal(body.chapters[0].spans[1].voice_id, actor.id);
  const ready = page.getByRole('heading', { name: 'Audiobook ready', exact: true });
  await ready.waitFor({ timeout: 120000 });
  const output = page.locator('section').filter({ has: ready }).last();
  await output.getByRole('button', { name: 'Play', exact: true }).click();
  await page.waitForFunction(() =>
    Array.from(document.querySelectorAll('audio')).some(
      (a) => a.currentSrc.includes('/audio/') && a.currentTime > 0,
    ),
  );
  assert.deepEqual(errors, []);
  console.log('Two-voice story rendered and played with existing local profiles.');
} finally {
  await browser.close();
}
