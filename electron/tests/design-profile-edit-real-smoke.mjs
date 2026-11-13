import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
const name = 'Design editor verification ' + Date.now();
let id;
let importedId;
let previewId;
try {
  const response = await page.request.post('http://127.0.0.1:3900/profiles', {
    multipart: {
      name,
      kind: 'design',
      seed: '1234',
      vd_states: '{"Gender":"female"}',
      instruct: 'female',
      language: 'English',
    },
  });
  assert.equal(response.status(), 200);
  id = (await response.json()).id;
  const initial = await (await page.request.get('http://127.0.0.1:3900/profiles/' + id)).json();
  await page.goto('http://localhost:3912/#/design');
  await page.getByText('Saved voices', { exact: true }).click();
  await page.getByRole('button', { name: 'Edit voice profile: ' + name, exact: true }).click();
  const pane = page.getByRole('complementary', { name: 'Edit voice profile', exact: true });
  if (process.env.VOICESTUDIO_TEST_PREVIEW === '1') {
    await page.locator('#design-script').fill('Preserved design draft');
    await pane.locator('summary', { hasText: 'Test this voice' }).click();
    await pane
      .getByRole('textbox', { name: 'Test phrase', exact: true })
      .fill('This is a short voice preview.');
    const generated = page.waitForResponse(
      (res) => res.url().endsWith('/generate') && res.request().method() === 'POST',
      { timeout: 120000 },
    );
    await pane.getByRole('button', { name: 'Create preview', exact: true }).click();
    const result = await generated;
    assert.equal(result.status(), 200);
    previewId = result.headers()['x-audio-id'];
    await pane.getByRole('button', { name: 'Pause', exact: true }).waitFor();
    await pane.getByRole('button', { name: 'Pause', exact: true }).click();
    assert.equal(await page.locator('#design-script').inputValue(), 'Preserved design draft');
    await pane.getByRole('button', { name: 'Lock voice identity', exact: true }).click();
    await pane.getByRole('button', { name: 'Locked', exact: true }).waitFor();
    const locked = await (await page.request.get('http://127.0.0.1:3900/profiles/' + id)).json();
    assert.equal(locked.is_locked, 1);
    assert.ok(locked.locked_audio_path);
    assert.equal(locked.seed, Number(result.headers()['x-seed']));
    const messages = JSON.parse(
      readFileSync('electron/src/renderer/src/i18n/locales/en.json', 'utf8'),
    ).voice_profile;
    await pane.locator('summary', { hasText: messages.consent_title }).click();
    await pane.getByRole('button', { name: 'Unlock', exact: true }).click();
    await pane.getByRole('button', { name: 'Confirm', exact: true }).click();
    await pane.getByRole('button', { name: 'Unlock', exact: true }).waitFor({ state: 'hidden' });
    const unlocked = await (await page.request.get('http://127.0.0.1:3900/profiles/' + id)).json();
    assert.equal(unlocked.is_locked, 0);
    assert.equal(unlocked.seed, null);
    assert.equal(unlocked.locked_audio_path, '');
    initial.seed = null;
  }
  await pane.locator('form input[required]').fill(name + ' edited');
  await pane.getByRole('button', { name: 'Language', exact: true }).click({ timeout: 3000 });
  await page.getByPlaceholder(/Search .* languages/).fill('French');
  await page.getByRole('option', { name: 'French', exact: true }).click();
  await pane.locator('summary', { hasText: 'Export persona' }).click();
  await pane.getByRole('checkbox', { name: 'Include voice clip', exact: true }).waitFor();
  const download = page.waitForEvent('download');
  await pane.getByRole('button', { name: 'Export persona', exact: true }).click();
  const bundle = await download;
  const file = {
    name: 'design.ovsvoice',
    mimeType: 'application/zip',
    buffer: readFileSync(await bundle.path()),
  };
  const imported = await page.request.post('http://127.0.0.1:3900/personas/import', {
    multipart: { file },
  });
  assert.equal(imported.status(), 200);
  importedId = (await imported.json()).profile_id;
  const restored = await (
    await page.request.get('http://127.0.0.1:3900/profiles/' + importedId)
  ).json();
  assert.equal(restored.kind, initial.kind);
  assert.equal(restored.seed, initial.seed);
  assert.deepEqual(JSON.parse(restored.vd_states), JSON.parse(initial.vd_states));
  await pane.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('button', { name: name + ' edited', exact: true }).waitFor();
  const updated = await (await page.request.get('http://127.0.0.1:3900/profiles/' + id)).json();
  assert.equal(updated.seed, initial.seed);
  assert.equal(updated.kind, 'design');
  assert.deepEqual(JSON.parse(updated.vd_states), JSON.parse(initial.vd_states));
  assert.equal(updated.name, name + ' edited');
  assert.equal(updated.language, 'French');
  console.log(
    'Designed voice editing and exported bundle re-import preserve kind, seed and normalized attributes.',
  );
} finally {
  if (previewId)
    await page.request.delete('http://127.0.0.1:3900/history/' + encodeURIComponent(previewId));
  if (importedId)
    await page.request.delete('http://127.0.0.1:3900/profiles/' + encodeURIComponent(importedId));
  if (id) await page.request.delete('http://127.0.0.1:3900/profiles/' + encodeURIComponent(id));
  await browser.close();
}
