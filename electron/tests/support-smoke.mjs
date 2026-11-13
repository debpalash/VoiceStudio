import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1100, height: 760 } });
try {
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912') + '/#/settings/appearance');
  await page.getByRole('link', { name: 'Support', exact: true }).click();
  await page.getByRole('heading', { name: 'Donate to VoiceStudio Project', exact: true }).waitFor();
  await page.getByRole('progressbar').waitFor();
  assert.equal(await page.getByRole('progressbar').getAttribute('aria-valuenow'), '5');
  assert.equal(
    await page.getByRole('link', { name: 'PayPal', exact: true }).getAttribute('href'),
    'https://paypal.me/palashCoder',
  );
  assert.equal(
    await page.getByRole('link', { name: 'GitHub Sponsors', exact: true }).first().getAttribute('href'),
    'https://github.com/sponsors/debpalash',
  );
  await page.getByRole('button', { name: '$50', exact: true }).click();
  assert.equal(
    await page.getByRole('link', { name: 'PayPal', exact: true }).getAttribute('href'),
    'https://paypal.me/palashCoder/50',
  );
  await page.getByRole('button', { name: 'Custom', exact: true }).click();
  assert.equal(
    await page.getByRole('link', { name: 'Report privately', exact: true }).getAttribute('href'),
    'https://github.com/debpalash/VoiceStudio/security/advisories/new',
  );
  console.log(
    'Support navigation, amount selection and private security destination passed; no external page opened.',
  );
} finally {
  await browser.close();
}
