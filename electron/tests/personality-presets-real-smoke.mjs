import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
try {
  await page.goto('http://localhost:3912/#/design');
  await page.locator('#design-script').fill('Preserve this script');
  await page.getByRole('button', { name: 'Narrator', exact: true }).click();
  await page.waitForFunction(
    () =>
      JSON.parse(localStorage.getItem('voicestudio.design.v1') || '{}').attrs?.Pitch ===
      'low pitch',
  );
  const before = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('voicestudio.design.v1')),
  );
  await page.getByRole('button', { name: 'News Anchor', exact: true }).click();
  await page.waitForFunction(
    () =>
      JSON.parse(localStorage.getItem('voicestudio.design.v1') || '{}').attrs?.EnglishAccent ===
      'american accent',
  );
  const after = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('voicestudio.design.v1')),
  );
  assert.equal(after.attrs.Pitch, 'Medium');
  assert.equal(after.attrs.Gender, 'Auto');
  assert.equal(after.seed, before.seed);
  assert.equal(after.text, 'Preserve this script');
  await page.reload();
  await page.locator('#design-script').waitFor();
  assert.equal(await page.locator('#design-script').inputValue(), 'Preserve this script');
  console.log(
    'Live personality catalogue, conflicting-attribute replacement and script/seed/reload preservation passed.',
  );
} finally {
  await browser.close();
}
