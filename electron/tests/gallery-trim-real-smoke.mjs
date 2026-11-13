import { chromium } from 'playwright';
import { readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
const ids = [];
const name = 'Trim verification ' + Date.now();
try {
  const upload = await page.request.post('http://127.0.0.1:3900/gallery/upload', {
    multipart: {
      name,
      category: 'import',
      audio: {
        name: 'fixture.wav',
        mimeType: 'audio/wav',
        buffer: readFileSync(resolve('backend/assets/samples/dictation/en_conversational.wav')),
      },
    },
  });
  assert.equal(upload.status(), 200);
  const original = await upload.json();
  ids.push(original.id);
  await page.goto('http://localhost:3912/#/gallery');
  await page.getByRole('button', { name: 'My Imports', exact: true }).click();
  const card = page
    .locator('article')
    .filter({ has: page.getByRole('heading', { name, exact: true }) });
  await card.getByRole('button', { name: 'Trim', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Start', exact: true }).waitFor({ timeout: 10000 });
  const region = page.locator('[part~="region"]');
  await region.waitFor();
  const bounds = await region.boundingBox();
  assert.ok(bounds && bounds.width > 50);
  await page.getByRole('spinbutton', { name: 'Start', exact: true }).fill('0.5');
  await page.getByRole('spinbutton', { name: 'End', exact: true }).fill('1.5');
  await page.getByRole('button', { name: 'Zoom in (+)', exact: true }).click();
  await page.getByRole('button', { name: 'Fit all (Home)', exact: true }).click();
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByRole('button', { name: 'Pause', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  const folder = mkdtempSync(join(tmpdir(), 'voicestudio-trim-check-'));
  await page.screenshot({ path: join(folder, 'trim.png') });
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith('/gallery/upload') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Use trimmed', exact: true }).click();
  const response = await saved;
  assert.equal(response.status(), 200);
  const clip = await response.json();
  ids.push(clip.id);
  assert.ok(Math.abs(clip.duration - 1) < 0.001);
  assert.ok(original.duration > clip.duration);
  await page.getByRole('heading', { name: name + ' - Trim', exact: true }).waitFor();
  console.log(
    'Real trim selection, zoom, Vidstack preview and exact one-second saved WAV passed. Screenshot: ' +
      join(folder, 'trim.png'),
  );
} finally {
  for (const id of ids)
    await page.request.delete('http://127.0.0.1:3900/gallery/voices/' + encodeURIComponent(id));
  await browser.close();
}
