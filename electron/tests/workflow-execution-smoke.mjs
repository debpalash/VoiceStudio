import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const browser = await chromium.launch({
  ...(process.env.CHROMIUM_PATH
    ? { executablePath: process.env.CHROMIUM_PATH }
    : { channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' }),
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
let generated = 0;
let transcribed = 0;
let converted = 0;
let translated = 0;
let ttsAvailable = true;
let normalized = 0;
let failNormalization = true;
// Small valid PCM fixture, not real inference.
const wav = Buffer.alloc(48);
wav.write('RIFF');
wav.writeUInt32LE(40, 4);
wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16);
wav.writeUInt16LE(1, 20);
wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(24000, 24);
wav.writeUInt32LE(48000, 28);
wav.writeUInt16LE(2, 32);
wav.writeUInt16LE(16, 34);
wav.write('data', 36);
wav.writeUInt32LE(4, 40);
await page.route(
  (url) => url.pathname.startsWith('/api/'),
  async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api(?:\/api)?/, '');
    if (path === '/dictation/readiness') return route.fulfill({ json: { ready: true } });
    if (path === '/transcribe') {
      transcribed++;
      return route.fulfill({ json: { text: 'Welcome to the lesson.' } });
    }
    if (path === '/dub/translate') {
      translated++;
      const body = route.request().postDataJSON();
      assert.equal(body.provider, 'argos');
      assert.equal(body.segments[0].text, 'Welcome to the lesson.');
      return route.fulfill({
        json: { translated: [{ id: 'workflow', text: 'Bienvenidos a la lección.' }] },
      });
    }
    if (path === '/convert') {
      converted++;
      return route.fulfill({ json: { audio_url: '/audio/converted.wav' } });
    }
    if (path === '/audio/converted.wav')
      return route.fulfill({ contentType: 'audio/wav', body: wav });
    if (path === '/generate') {
      generated++;
      assert(route.request().postData().includes('demo-voice'));
      return route.fulfill({ contentType: 'audio/wav', body: wav });
    }
    if (path === '/tools/normalize-speech') {
      normalized++;
      if (failNormalization)
        return route.fulfill({ status: 503, json: { detail: 'Test normalization unavailable' } });
      return route.fulfill({ contentType: 'audio/wav', body: wav });
    }
    const json =
      path === '/setup/status'
        ? { models_ready: true, missing: [] }
        : path === '/health'
          ? { status: 'ok' }
          : path === '/settings/analytics'
            ? { available: false, prompted: true, opted_in: false }
            : path === '/models/install/status'
              ? { jobs: [] }
              : path === '/engines'
                ? {
                    tts: {
                      active: 'omnivoice',
                      backends: [
                        { id: 'omnivoice', available: ttsAvailable, supports_cloning: true },
                      ],
                    },
                    asr: { backends: [] },
                    llm: { backends: [] },
                  }
                : path === '/engines/translation'
                  ? { engines: [], active: '' }
                  : path === '/profiles'
                    ? [{ id: 'demo-voice', name: 'Demo voice', kind: 'clone' }]
                    : path === '/models'
                      ? { models: [] }
                      : path === '/model/loaded'
                        ? { models: [], count: 0 }
                        : path === '/history' || path === '/batch/jobs'
                          ? []
                          : undefined;
    return route.fulfill({
      status: json === undefined ? 404 : 200,
      json: json ?? { detail: 'Unavailable in fixture' },
    });
  },
);
async function fromTemplate(name) {
  await page.getByRole('button', { name: 'New from template' }).click();
  const picker = page.getByRole('dialog', { name: 'Start from a template' });
  await picker.getByRole('button', { name, exact: true }).click();
  await picker.waitFor({ state: 'detached' });
}
try {
  await page.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
  await page.goto(base + '/#/workflows');
  await fromTemplate('Batch narration · Normalize audio');
  await page.getByLabel('Scripts', { exact: true }).fill('First lesson\n---\nSecond lesson');
  await page.locator('.react-flow__node').filter({ hasText: 'Speech' }).click();
  await page.getByRole('button', { name: 'Target voice', exact: true }).click();
  await page.getByRole('option').filter({ hasText: 'Demo voice' }).click();
  await page.locator('.workflow-intro').getByRole('button', { name: 'Run workflow' }).click();
  await page
    .locator('.workflow-runner')
    .getByRole('button', { name: 'Run workflow', exact: true })
    .click();
  await page.getByText('Test normalization unavailable', { exact: true }).waitFor();
  assert.equal(generated, 1);
  assert.equal(normalized, 1);
  await page.reload();
  await page.locator('.workflow-intro').getByRole('button', { name: 'Run workflow' }).click();
  await page.getByText('Test normalization unavailable', { exact: true }).waitFor();
  failNormalization = false;
  await page
    .locator('.workflow-runner')
    .getByRole('button', { name: 'Retry', exact: true })
    .click();
  await page.getByText('2 / 2 clips ready', { exact: true }).waitFor();
  assert.equal(generated, 2, 'resume must reuse the first generated clip');
  assert.equal(normalized, 3, 'failed step retried, then second clip normalized');
  assert.equal(await page.locator('.workflow-run-output audio').count(), 2);
  await page.screenshot({ path: join(tmpdir(), 'voicestudio-workflow-execution.png') });
  const download = page.waitForEvent('download');
  await page
    .locator('.workflow-run-output')
    .first()
    .getByRole('button', { name: 'Export' })
    .click();
  assert((await download).suggestedFilename().endsWith('-01-1.wav'));
  await page.reload();
  await page.locator('.workflow-intro').getByRole('button', { name: 'Run workflow' }).click();
  await page.getByText('2 / 2 clips ready', { exact: true }).waitFor();
  assert.equal(generated, 2, 'reopening never reruns inference');
  // An ASR-only recipe must not require a working TTS engine.
  ttsAvailable = false;
  await page.reload();
  await fromTemplate('Audio to transcript');
  await page
    .getByLabel('Add audio', { exact: true })
    .setInputFiles({ name: 'lesson.wav', mimeType: 'audio/wav', buffer: wav });
  await page.getByText('lesson.wav', { exact: true }).waitFor();
  await page.reload(); // The source file must survive a reload before any run.
  await page.locator('.workflow-intro').getByRole('button', { name: 'Run workflow' }).click();
  await page
    .locator('.workflow-runner')
    .getByRole('button', { name: 'Run workflow', exact: true })
    .click();
  await page.getByText('1 / 1 clips ready', { exact: true }).waitFor();
  await page
    .locator('.workflow-run-output pre')
    .getByText('Welcome to the lesson.', { exact: true })
    .waitFor();
  const textDownload = page.waitForEvent('download');
  await page.locator('.workflow-run-output').getByRole('button', { name: 'Export' }).click();
  assert((await textDownload).suggestedFilename().endsWith('.txt'));
  assert.equal(transcribed, 1);

  ttsAvailable = true;
  await page.reload();
  await fromTemplate('Change the voice');
  await page
    .getByLabel('Add audio', { exact: true })
    .setInputFiles({ name: 'lesson.wav', mimeType: 'audio/wav', buffer: wav });
  await page.getByText('lesson.wav', { exact: true }).waitFor();
  await page.locator('.react-flow__node').filter({ hasText: 'Convert' }).click();
  await page.getByRole('button', { name: 'Target voice', exact: true }).click();
  await page.getByRole('option').filter({ hasText: 'Demo voice' }).click();
  await page.locator('.workflow-intro').getByRole('button', { name: 'Run workflow' }).click();
  await page
    .locator('.workflow-runner')
    .getByRole('button', { name: 'Run workflow', exact: true })
    .click();
  await page.getByText('1 / 1 clips ready', { exact: true }).waitFor();
  assert.equal(converted, 1);

  await fromTemplate('Translate and narrate');
  await page
    .getByLabel('Add audio', { exact: true })
    .setInputFiles({ name: 'lesson.wav', mimeType: 'audio/wav', buffer: wav });
  await page.getByText('lesson.wav', { exact: true }).waitFor();
  await page.locator('.react-flow__node').filter({ hasText: 'Speech' }).click();
  await page.getByRole('button', { name: 'Target voice', exact: true }).click();
  await page.getByRole('option').filter({ hasText: 'Demo voice' }).click();
  await page.locator('.workflow-intro').getByRole('button', { name: 'Run workflow' }).click();
  await page
    .locator('.workflow-runner')
    .getByRole('button', { name: 'Run workflow', exact: true })
    .click();
  await page.getByText('1 / 1 clips ready', { exact: true }).waitFor();
  assert.equal(transcribed, 2);
  assert.equal(translated, 1);
  assert.equal(generated, 3);
  await page.screenshot({ path: join(tmpdir(), 'voicestudio-workflow-localization.png') });
  // Removing a source releases its blob only after the last duplicated reference.
  await fromTemplate('Audio to transcript');
  await page
    .getByLabel('Add audio', { exact: true })
    .setInputFiles({ name: 'remove-me.wav', mimeType: 'audio/wav', buffer: wav });
  await page.getByText('remove-me.wav', { exact: true }).waitFor();
  await page.getByLabel('Workflow name', { exact: true }).fill('Source cleanup');
  const sourceId = await page.evaluate(() => {
    const library = JSON.parse(localStorage.getItem('voicestudio.workflows.v1'));
    return library.documents.find((item) => item.id === library.activeId).steps[0].media[0].id;
  });
  const hasSource = (id) =>
    page.evaluate(
      (id) =>
        new Promise((resolve, reject) => {
          const open = indexedDB.open('voicestudio.workflow-runs', 1);
          open.onerror = () => reject(open.error);
          open.onsuccess = () => {
            const db = open.result;
            const request = db
              .transaction('runs')
              .objectStore('runs')
              .get('media:' + id);
            request.onsuccess = () => {
              resolve(Boolean(request.result));
              db.close();
            };
          };
        }),
      id,
    );
  await page.getByRole('button', { name: 'Duplicate', exact: true }).click();
  await page
    .locator('.workflow-intro')
    .getByRole('button', { name: 'Delete', exact: true })
    .click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  assert.equal(await hasSource(sourceId), true, 'a shared source survives deleting one duplicate');
  await page.getByRole('button', { name: 'Source cleanup', exact: true }).click();
  await page.locator('.react-flow__node').filter({ hasText: 'Source clip' }).click();
  await page
    .getByRole('complementary', { name: 'Step details' })
    .getByRole('button', { name: 'Delete', exact: true })
    .click();
  for (let i = 0; i < 40 && (await hasSource(sourceId)); i++) await page.waitForTimeout(50);
  assert.equal(
    await hasSource(sourceId),
    false,
    'removing the final reference deletes the source blob',
  );
  await page
    .getByLabel('Add audio', { exact: true })
    .setInputFiles({ name: 'delete-workflow.wav', mimeType: 'audio/wav', buffer: wav });
  await page.getByText('delete-workflow.wav', { exact: true }).waitFor();
  const deletedId = await page.evaluate(() => {
    const library = JSON.parse(localStorage.getItem('voicestudio.workflows.v1'));
    return library.documents.find((item) => item.id === library.activeId).steps[0].media[0].id;
  });
  await page.evaluate(() => {
    IDBObjectStore.prototype.delete = function () {
      throw new Error('Temporary storage failure');
    };
  });
  await page
    .getByRole('complementary', { name: 'Step details' })
    .getByRole('button', { name: 'Delete', exact: true })
    .click();
  await page.getByRole('alert').filter({ hasText: 'Temporary storage failure' }).waitFor();
  assert.equal(await hasSource(deletedId), true);
  assert(
    await page.evaluate(
      () => JSON.parse(localStorage.getItem('voicestudio.workflows.v1')).cleanup.length > 0,
    ),
  );
  await page.reload(); // Reload restores IndexedDB and retries the persisted deletion queue.
  await page.getByRole('textbox', { name: 'Workflow name' }).waitFor();
  for (let i = 0; i < 40 && (await hasSource(deletedId)); i++) await page.waitForTimeout(50);
  assert.equal(await hasSource(deletedId), false, 'queued deletions are retried after a reload');
  await page.locator('.react-flow__node').filter({ hasText: 'Source clip' }).click();
  await page
    .getByLabel('Add audio', { exact: true })
    .setInputFiles({ name: 'last.wav', mimeType: 'audio/wav', buffer: wav });
  await page.getByText('last.wav', { exact: true }).waitFor();
  const lastId = await page.evaluate(() => {
    const library = JSON.parse(localStorage.getItem('voicestudio.workflows.v1'));
    return library.documents.find((item) => item.id === library.activeId).steps[0].media[0].id;
  });
  await page
    .locator('.workflow-intro')
    .getByRole('button', { name: 'Delete', exact: true })
    .click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  for (let i = 0; i < 40 && (await hasSource(lastId)); i++) await page.waitForTimeout(50);
  assert.equal(await hasSource(lastId), false, 'deleting the final workflow releases its source');
  assert.deepEqual(errors, []);
  console.log('Workflow execution, recovery, persisted audio and export smoke passed');
} finally {
  await browser.close();
}
