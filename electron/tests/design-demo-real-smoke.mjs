import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
try {
  await page.goto('http://localhost:3912/#/design');
  const card = page
    .locator('article')
    .filter({ has: page.getByRole('heading', { name: 'The Librarian', exact: true }) });
  await card.waitFor();
  await card.getByRole('button', { name: 'Preview voice', exact: true }).click();
  await card.getByRole('button', { name: 'Pause', exact: true }).waitFor();
  await card.getByRole('button', { name: 'Pause', exact: true }).click();
  await card.getByRole('button', { name: /Use this design/ }).click();
  await page.waitForFunction(
    () =>
      JSON.parse(localStorage.getItem('voicestudio.design.v1') || '{}').attrs?.EnglishAccent ===
      'british accent',
  );
  assert.match(await page.locator('#design-script').inputValue(), /clock tower struck thirteen/);
  console.log('Bundled demo playback and script/attribute handoff passed without generation.');
} finally {
  await browser.close();
}
