import { launchSmokeBrowser } from './smoke-api-mock.mjs';
import assert from 'node:assert/strict';
const browser = await launchSmokeBrowser({ fallbackChannel: 'msedge' });
try {
  const page = await browser.newPage();
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
  });
  let submits = 0;
  let cancels = 0;
  let deletes = 0;
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const job = {
    id: 'fixture',
    filename: 'one.mp4',
    created_at: 1_700_000_000,
    status: 'running',
    langs: ['es', 'fr', 'de'],
    progress: { stage: 'transcribe', percent: 35 },
    outputs: { es: 'output-es.mp4', fr: 'output-fr.mp4', de: 'output-de.mp4' },
  };
  // Batch refuses to queue until a TTS engine and a translation engine are ready.
  await page.route('**/api/engines', (route) =>
    route.fulfill({
      json: {
        tts: {
          active: 'omnivoice',
          active_model: 'batch-model',
          backends: [{ id: 'omnivoice', name: 'OmniVoice', available: true }],
        },
      },
    }),
  );
  await page.route('**/api/models', (route) =>
    route.fulfill({
      json: {
        target: 'local',
        models: [
          {
            repo_id: 'batch-model',
            label: 'Batch model',
            role: 'tts',
            size_gb: 1,
            installed: true,
            supported: true,
          },
        ],
      },
    }),
  );
  await page.route('**/api/engines/translation', (route) =>
    route.fulfill({
      json: {
        active: 'argos',
        engines: [{ id: 'argos', display_name: 'Argos', installed: true, category: 'offline' }],
      },
    }),
  );
  await page.route('**/api/batch/jobs?*', (route) =>
    route.fulfill({
      json: submits
        ? [
            {
              ...job,
              status:
                new URL(route.request().url()).searchParams.get('status') === 'done'
                  ? 'done'
                  : job.status,
            },
          ]
        : [],
    }),
  );
  await page.route('**/api/batch/enqueue', async (route) => {
    const request = route.request();
    const body = await new Response(request.postDataBuffer(), {
      headers: { 'content-type': request.headers()['content-type'] },
    }).formData();
    assert.equal(body.get('langs'), 'es,fr,de');
    submits++;
    return route.fulfill({ json: { job_id: 'fixture' } });
  });
  await page.route('**/api/batch/jobs/fixture/cancel', (route) => {
    cancels++;
    job.status = 'cancelled';
    return route.fulfill({ json: { cancelled: true } });
  });
  await page.route('**/api/batch/jobs/fixture', (route) => {
    assert.equal(route.request().method(), 'DELETE');
    deletes++;
    return route.fulfill({ json: { deleted: true } });
  });
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902') + '/#/batch');
  // #3628: exercise the actual browser interactions, including the scrollable
  // menu; a DOM-only test cannot detect a modal blocking pointer/focus events.
  const language = page.getByRole('button', { name: 'Language', exact: true });
  await language.click();
  const search = page.getByRole('combobox', { name: 'Language', exact: true });
  await search.clear();
  await search.pressSequentially('French');
  assert.equal(await search.inputValue(), 'French');
  await page.getByRole('option', { name: 'French', exact: true }).click();
  await page.getByRole('button', { name: 'French', exact: true }).waitFor();
  await language.click();
  const list = page.getByRole('listbox', { name: 'Language', exact: true });
  await list.evaluate((element) => { element.scrollTop = 0; });
  await list.hover();
  await page.mouse.wheel(0, 400);
  await page.waitForFunction(() => document.querySelector('[role="listbox"]')?.scrollTop > 0);
  await search.clear();
  await search.pressSequentially('German');
  assert.equal(await search.inputValue(), 'German');
  await page.getByRole('option', { name: 'German', exact: true }).click();
  await page.getByRole('button', { name: 'German', exact: true }).waitFor();
  assert(await page.getByRole('button', { name: 'Add to Queue', exact: true }).isDisabled());
  await page
    .locator('input[type=file]')
    .setInputFiles({ name: 'one.mp4', mimeType: 'video/mp4', buffer: Buffer.from('fixture') });
  await page.getByRole('button', { name: 'Add to Queue', exact: true }).click();
  await page.getByRole('heading', { name: 'one.mp4' }).waitFor();
  assert.equal(submits, 1);
  assert.equal(await page.locator('progress').getAttribute('value'), '35');
  await page.reload();
  await page.getByRole('heading', { name: 'one.mp4' }).waitFor();
  assert.equal(submits, 1, 'Reload must not enqueue again');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByText('cancelled', { exact: true }).waitFor();
  assert.equal(cancels, 1);
  await page.getByRole('button', { name: 'Completed', exact: true }).click();
  for (const lang of job.langs) {
    await page.getByRole('button', { name: `Export ${lang}`, exact: true }).waitFor();
  }
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  assert.equal(deletes, 0);
  const deleted = page.waitForResponse(
    (response) =>
      response.request().method() === 'DELETE' &&
      new URL(response.url()).pathname === '/api/batch/jobs/fixture',
  );
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  assert.equal((await deleted).status(), 200);
  assert.equal(deletes, 1);
  assert.deepEqual(errors, []);
  console.log(
    'PASS: batch enqueue, progress, reload, cancellation, export availability and confirmed deletion',
  );
} finally {
  await browser.close();
}
