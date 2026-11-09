import { chromium } from 'playwright';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
await page.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
let loads = 0;
let allowCatalogueLoad = false;
page.on('request', (request) => {
  if (request.url().includes('/community/items?')) loads++;
});
await page.route(
  (url) => url.pathname === '/api/community/items',
  async (route) => {
    if (!allowCatalogueLoad) {
      await route.fulfill({ status: 503, json: { detail: 'Temporary catalogue failure' } });
      return;
    }
    await route.continue();
  },
);
try {
  await page.goto('http://localhost:3912/#/gallery');
  await page.getByRole('button', { name: 'Community', exact: true }).waitFor();
  assert.equal(loads, 0);
  await page.getByRole('button', { name: 'Community', exact: true }).click();
  await page.getByRole('button', { name: 'Retry', exact: true }).waitFor();
  allowCatalogueLoad = true;
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  const card = page
    .locator('article')
    .filter({ has: page.getByRole('heading', { name: 'The Librarian', exact: true }) });
  await card.waitFor();
  assert.ok(loads > 0);
  const favorite = card.getByRole('button', { name: /Favorites:/ });
  await favorite.click();
  assert.equal(await favorite.getAttribute('aria-pressed'), 'true');
  await card.getByRole('button', { name: 'Preview voice', exact: true }).click();
  await card.getByRole('button', { name: 'Pause', exact: true }).waitFor({ timeout: 60000 });
  await card.getByRole('button', { name: 'Pause', exact: true }).click();
  const folder = mkdtempSync(join(tmpdir(), 'voicestudio-community-check-'));
  await page.screenshot({ path: join(folder, 'community.png') });
  await card.getByRole('button', { name: 'Open in Designer', exact: true }).click();
  await page.waitForURL('**/#/design');
  const draft = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('voicestudio.design.v1')),
  );
  assert.equal(draft.attrs.Gender, 'female');
  assert.equal(draft.attrs.EnglishAccent, 'british accent');
  assert.deepEqual(errors, []);
  console.log(
    'Real Community catalogue, favorite, preview and Designer handoff passed. Screenshot: ' +
      join(folder, 'community.png'),
  );
} finally {
  await browser.close();
}
