import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
let ready = false;
let requests = 0;
try {
  const engines = await (await page.request.get('http://127.0.0.1:3900/engines')).json();
  await page.route('**/api/engines', (route) => {
    const data = structuredClone(engines);
    data.tts.backends = data.tts.backends.map((backend) => ({ ...backend, available: ready }));
    return route.fulfill({ json: data });
  });
  await page.route('**/api/profiles', (route) =>
    route.fulfill({
      json: [{ id: 'demo0001', name: 'Demo', kind: 'clone', ref_audio_path: 'demo.wav' }],
    }),
  );
  await page.route('**/api/generate', (route) => {
    requests++;
    return route.abort();
  });
  await page.goto('http://localhost:3912/#/clone');
  await page.evaluate(async () => {
    const { patchCloneSettings } = await import('/src/lib/store/clone-settings.ts');
    patchCloneSettings({ selectedProfileId: 'demo0001', text: 'Preserved user text' });
  });
  await page.getByRole('heading', { name: 'Hear demo', exact: true }).waitFor();
  const section = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: 'Hear demo', exact: true }) })
    .last();
  await section.getByRole('button', { name: 'Play', exact: true }).click();
  await section.getByRole('button', { name: 'Pause', exact: true }).waitFor();
  await section.getByRole('button', { name: 'Pause', exact: true }).click();
  assert.ok(await page.getByRole('button', { name: 'Synthesize audio', exact: true }).isDisabled());
  await page.keyboard.press('Control+Enter');
  assert.equal(requests, 0);
  ready = true;
  await page.evaluate(async () => {
    const { queryClient } = await import('/src/lib/query.ts');
    await queryClient.invalidateQueries({ queryKey: ['engines'] });
  });
  await page.getByRole('heading', { name: 'Hear demo', exact: true }).waitFor({ state: 'hidden' });
  assert.equal(await page.locator('[data-clone-script]').inputValue(), 'Preserved user text');
  console.log(
    'No-engine demo playback, synthesis/shortcut guard, and engine-ready transition passed.',
  );
} finally {
  await browser.close();
}
