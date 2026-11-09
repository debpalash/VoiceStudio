import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const takes = [];
try {
  page.on('response', async (response) => {
    if (response.url().endsWith('/generate') && response.status() === 200) {
      const id = response.headers()['x-audio-id'];
      if (id) takes.push(id);
    }
  });
  await page.goto('http://localhost:3912/#/tools');
  await page.getByRole('button', { name: 'A/B Voice Comparison', exact: true }).click();
  await page
    .getByRole('textbox', { name: 'Test phrase', exact: true })
    .fill('A short voice comparison.');
  // Each comparison side exposes an explicit list of voices and presets.
  const first = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: 'Voice A', exact: true }) })
    .last();
  const second = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: 'Voice B', exact: true }) })
    .last();
  await first
    .getByRole('button', { name: /\(Preset\)/ })
    .first()
    .click();
  await second
    .getByRole('button', { name: /\(Preset\)/ })
    .nth(1)
    .click();
  await page.getByRole('button', { name: 'Compare', exact: true }).click();
  await page.getByRole('button', { name: 'Compare', exact: true }).waitFor({ timeout: 120000 });
  assert.equal(takes.length, 2);
  await page.getByRole('button', { name: 'Probe file', exact: true }).click();
  await page.getByRole('button', { name: 'A/B Voice Comparison', exact: true }).click();
  assert.equal(
    await page.getByRole('textbox', { name: 'Test phrase', exact: true }).inputValue(),
    'A short voice comparison.',
  );

  await first.getByRole('button', { name: 'Play', exact: true }).click();
  await first.getByRole('button', { name: 'Pause', exact: true }).waitFor();
  await second.getByRole('button', { name: 'Play', exact: true }).click();
  await second.getByRole('button', { name: 'Pause', exact: true }).waitFor();
  await first.getByRole('button', { name: 'Play', exact: true }).waitFor();
  await second.getByRole('button', { name: 'Pause', exact: true }).click();
  const path = join(mkdtempSync(join(tmpdir(), 'vs-compare-')), 'comparison.png');
  await page.screenshot({ path });
  console.log('Real preset comparison and exclusive Vidstack playback passed. ' + path);
} finally {
  for (const id of takes) await page.request.delete('http://127.0.0.1:3900/history/' + id);
  await browser.close();
}
