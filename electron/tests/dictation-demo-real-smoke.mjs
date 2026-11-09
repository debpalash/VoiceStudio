import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
let requests = 0;
try {
  page.on('request', (req) => {
    if (req.url().endsWith('/transcribe')) requests++;
  });
  await page.goto('http://localhost:3912/#/settings/models/dictation');
  const card = page.locator('article').filter({ hasText: 'Schedule a meeting with Pat' });
  await card.waitFor();
  assert.equal(requests, 0);
  await card.getByRole('button', { name: 'Preview voice', exact: true }).click();
  await card.getByRole('button', { name: 'Pause', exact: true }).waitFor();
  await card.getByRole('button', { name: 'Pause', exact: true }).click();
  const response = page.waitForResponse((res) => res.url().endsWith('/transcribe'), {
    timeout: 120000,
  });
  await card.getByRole('button', { name: /Replay/ }).click();
  const result = await response;
  assert.equal(result.status(), 200, await result.text());
  const payload = await result.json();
  assert.ok(payload.text.trim());
  await card.getByRole('status').waitFor();
  assert.equal(await card.getByRole('status').textContent(), payload.text);
  assert.equal(requests, 1);
  console.log('Bundled Vidstack playback and real ASR replay passed without microphone capture.');
} finally {
  await browser.close();
}
