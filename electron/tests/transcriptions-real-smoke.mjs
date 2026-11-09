import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const ui = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const fixture = join(workspaceRoot, 'backend/assets/samples/dictation/en_conversational.wav');
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
    localStorage.removeItem('omni_transcriptions');
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('response', (response) => {
    if (response.url().includes('/api/') && response.status() >= 500) {
      errors.push(`${response.status()} ${response.url()}`);
    }
  });

  await page.goto(ui + '/#/transcriptions');
  await page.getByRole('complementary', { name: 'History', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Accurate', exact: true }).click();
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/transcribe') && response.request().method() === 'POST',
    { timeout: 120_000 },
  );
  await page.locator('input[type=file]').setInputFiles(fixture);
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  const result = await response.json();
  assert.equal(result.engine, 'faster-whisper');
  assert.ok(result.text?.trim());
  assert.ok(result.segments?.length);

  await page.getByText(result.text, { exact: true }).first().waitFor();
  const stored = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('omni_transcriptions') || '[]'),
  );
  assert.equal(stored.length, 1);
  assert.equal(stored[0].text, result.text);
  assert.equal(stored[0].language, result.language);

  await page
    .locator('summary')
    .filter({ hasText: /^Segments$/ })
    .click();
  await page.getByText(result.segments[0].text, { exact: true }).last().waitFor();

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  const download = await downloadPromise;
  assert.match(download.suggestedFilename(), /^transcriptions_\d{4}-\d{2}-\d{2}\.txt$/);
  const exported = readFileSync(await download.path(), 'utf8');
  assert.match(exported, new RegExp(`\\(${result.language}\\)`));
  assert.ok(exported.includes(result.text));
  await page.getByText('Transcript exported.', { exact: true }).waitFor();

  await page.getByRole('button', { name: 'Reuse script', exact: true }).click();
  await page.waitForURL(/#\/clone$/);
  const handoff = await page.evaluate(async () => {
    const { cloneSettingsStore } = await import('/src/lib/store/clone-settings.ts');
    return {
      text: cloneSettingsStore.state.text,
      language: cloneSettingsStore.state.language,
    };
  });
  assert.deepEqual(handoff, { text: result.text, language: result.language });

  await page.goto(ui + '/#/transcriptions');
  await page.getByText(result.text, { exact: true }).first().waitFor();
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await page.getByRole('heading', { name: 'No transcripts saved yet', exact: true }).waitFor();
  assert.deepEqual(
    await page.evaluate(() => JSON.parse(localStorage.getItem('omni_transcriptions') || '[]')),
    [],
  );
  assert.deepEqual(errors, []);
  console.log(
    `PASS: real ${result.engine} upload, segments, export, Clone handoff and cleanup (${result.duration_s}s)`,
  );
} finally {
  await browser.close();
}
