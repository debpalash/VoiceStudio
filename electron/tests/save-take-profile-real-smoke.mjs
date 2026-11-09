import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
const ui = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902';
await page.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
const name = 'Saved take fixture ' + Date.now();
let takeId;
let profileId;
try {
  const generated = await page.request.post('http://127.0.0.1:3900/generate', {
    timeout: 120000,
    multipart: {
      text: 'This is a saved voice sample.',
      instruct: 'female',
      language: 'English',
      seed: '0',
      num_step: '16',
    },
  });
  assert.equal(generated.status(), 200);
  takeId = generated.headers()['x-audio-id'];
  const history = await (await page.request.get('http://127.0.0.1:3900/history')).json();
  const item = history.find((take) => take.id === takeId);
  assert.ok(item);
  await page.goto(ui + '/#/clone');
  const before = await page.evaluate(async (item) => {
    const { cloneSettingsStore, patchCloneSettings } =
      await import('/src/lib/store/clone-settings.ts');
    patchCloneSettings({ text: 'Preserved script', language: 'German', instruct: 'whisper' });
    const { openTake } = await import('/src/lib/store/takes.ts');
    openTake(item);
    return cloneSettingsStore.state;
  }, item);
  const pane = page.getByRole('complementary', { name: 'Recent takes', exact: true });
  await pane.getByRole('button', { name: 'Save as voice profile', exact: true }).click();
  await pane.getByRole('textbox', { name: /^Profile name/ }).fill(name);
  const saved = page.waitForResponse(
    (res) => res.url().endsWith('/profiles') && res.request().method() === 'POST',
  );
  await pane.getByRole('button', { name: 'Save', exact: true }).click();
  const response = await saved;
  assert.equal(response.status(), 200);
  const profile = await response.json();
  profileId = profile.id;
  assert.equal(profile.ref_text, item.text);
  assert.equal(profile.language, item.language);
  assert.equal(profile.seed, item.seed);
  assert.equal(profile.instruct, item.instruct);
  const after = await page.evaluate(
    async () => (await import('/src/lib/store/clone-settings.ts')).cloneSettingsStore.state,
  );
  assert.deepEqual(after, before);
  console.log(
    'Real generated take saved as a profile with take metadata; active script/voice/settings unchanged.',
  );
} finally {
  if (profileId) await page.request.delete('http://127.0.0.1:3900/profiles/' + profileId);
  if (takeId) await page.request.delete('http://127.0.0.1:3900/history/' + takeId);
  await browser.close();
}
