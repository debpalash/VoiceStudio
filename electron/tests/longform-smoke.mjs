import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { wave } from './test-wave.mjs';
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
  });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const bodies = [];
  await page.route('**/api/profiles', (route) =>
    route.fulfill({
      json: [
        { id: 'voice', name: 'Narrator fixture', kind: 'clone' },
        { id: 'actor', name: 'Actor fixture', kind: 'clone' },
      ],
    }),
  );
  await page.route('**/api/audiobook/jobs', (route) => route.fulfill({ json: { jobs: [] } }));
  // Creating an audiobook stays disabled until a TTS engine and model are ready.
  await page.route('**/api/engines', (route) =>
    route.fulfill({
      json: {
        tts: {
          active: 'omnivoice',
          active_model: 'longform-model',
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
            repo_id: 'longform-model',
            label: 'Longform model',
            role: 'tts',
            size_gb: 1,
            installed: true,
            supported: true,
          },
        ],
      },
    }),
  );
  await page.route('**/api/audio/longform-fixture.m4b', (route) =>
    route.fulfill({ body: wave, contentType: 'audio/wav' }),
  );
  const render = (route) => {
    assert.match(route.request().headers()['content-type'] || '', /application\/json/);
    bodies.push(route.request().postDataJSON());
    return route.fulfill({
      contentType: 'text/event-stream',
      body: 'data: {"type":"started","chapters":1}\n\ndata: {"type":"chapter","index":0}\n\ndata: {"type":"done","output":"longform-fixture.m4b"}\n\n',
    });
  };
  await page.route('**/api/audiobook', render);
  await page.route('**/api/longform/render', render);
  await page.route('**/api/audiobook/cover', (route) =>
    route.fulfill({ json: { path: '/covers/fixture.png' } }),
  );
  let previewBody;
  await page.route('**/api/audiobook/plan', (route) =>
    route.fulfill({ json: { chapters: [{ title: 'Chapter one', char_count: 25 }] } }),
  );
  await page.route('**/api/audiobook/preview', (route) => {
    previewBody = route.request().postDataJSON();
    return route.fulfill({ json: { output: 'longform-fixture.m4b', title: 'Chapter one' } });
  });
  let auditions = 0;
  await page.route('**/api/generate', (route) => {
    auditions++;
    return route.fulfill({ contentType: 'audio/wav', body: wave });
  });
  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902';
  await page.goto(base + '/#/audiobook');
  assert(await page.getByRole('button', { name: 'Create audiobook', exact: true }).isDisabled());
  await page
    .locator('[data-slot=secondary-sidebar]')
    .getByRole('button', { name: 'Narrator fixture', exact: true })
    .click();
  await page
    .getByRole('textbox', { name: 'Script', exact: true })
    .fill('# Chapter one\n[voice:Mara] Hello from this book.');
  await page.getByText('Mara', { exact: true }).click();
  await page
    .getByRole('group', { name: 'Cast: Mara', exact: true })
    .getByRole('button', { name: 'Actor fixture', exact: true })
    .click();
  await page.getByText('Cover & details', { exact: true }).click();
  await page.getByLabel('Author', { exact: true }).fill('Test author');
  await page
    .locator('input[accept="image/png,image/jpeg"]')
    .setInputFiles({ name: 'cover.png', mimeType: 'image/png', buffer: Buffer.from('fixture') });
  await page.getByText('cover.png', { exact: true }).waitFor();
  await page.getByText('Loudness', { exact: true }).click();
  await page.getByRole('button', { name: 'Normalize (ACX)', exact: true }).click();
  await page.getByText('Pronunciation', { exact: true }).click();
  await page.getByRole('button', { name: 'Add word', exact: true }).click();
  await page.getByLabel('Word', { exact: true }).fill('SQL');
  await page.getByLabel(/Say it as/).fill('sequel');
  await page.getByText('Production overrides', { exact: true }).click();
  await page.getByLabel('Seed', { exact: true }).fill('0');
  await page.getByRole('button', { name: 'Preview plan', exact: true }).click();
  await page.getByRole('button', { name: 'Preview chapter: Chapter one', exact: true }).click();
  await page.getByRole('button', { name: 'Play', exact: true }).waitFor();
  assert.deepEqual(previewBody.voice_map, { Mara: 'actor' });
  assert.deepEqual(previewBody.lexicon, { SQL: 'sequel' });
  assert.equal(previewBody.chapter_index, 0);
  assert.equal(previewBody.seed, 0);
  // Editing synthesis inputs clears the audition and chapter plan.
  await page
    .getByRole('textbox', { name: 'Script', exact: true })
    .fill('# Chapter one\n[voice:Mara] Hello from this book. ');
  await page
    .getByRole('textbox', { name: 'Script', exact: true })
    .fill('# Chapter one\n[voice:Mara] Hello from this book.');
  await page.getByRole('button', { name: 'Create audiobook', exact: true }).click();
  await page.getByRole('heading', { name: 'Audiobook ready', exact: true }).waitFor();
  assert.equal(bodies[0].default_voice, 'voice');
  assert.equal(bodies[0].seed, 0);
  assert.deepEqual(bodies[0].voice_map, { Mara: 'actor' });
  assert.equal(bodies[0].metadata.author, 'Test author');
  assert.equal(bodies[0].loudness, 'acx');
  assert.equal(bodies[0].cover_path, '/covers/fixture.png');
  assert.deepEqual(bodies[0].lexicon, { SQL: 'sequel' });
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.waitForFunction(() => {
    const audio = document.querySelector('audio');
    return audio && audio.currentTime > 0;
  });
  assert.equal(bodies.length, 1);
  await page.reload();
  await page.getByRole('heading', { name: 'Audiobook ready', exact: true }).waitFor();
  assert.equal(
    await page.getByRole('textbox', { name: 'Script', exact: true }).inputValue(),
    '# Chapter one\n[voice:Mara] Hello from this book.',
  );
  assert.equal(await page.getByLabel('Author', { exact: true }).inputValue(), 'Test author');
  assert.equal(await page.getByLabel('Word', { exact: true }).inputValue(), 'SQL');
  // Projects is a tab beside the controls; Audiobook (the pane title) switches back.
  await page.getByRole('tab', { name: 'Projects', exact: true }).click();
  await page.getByLabel('Project name', { exact: true }).fill('Saved book fixture');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('button', { name: 'Saved book fixture', exact: true }).waitFor();
  await page.getByRole('tab', { name: 'Audiobook', exact: true }).click();
  await page.getByRole('textbox', { name: 'Script', exact: true }).fill('Unsaved edit');
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902') + '/#/projects');
  await page.getByRole('button', { name: 'Rename Saved book fixture', exact: true }).click();
  await page.getByLabel('Project name', { exact: true }).fill('Renamed book fixture');
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await page.getByRole('button', { name: 'Renamed book fixture', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('button', { name: 'Renamed book fixture', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await page.waitForFunction(() =>
    document.querySelector('textarea[aria-label="Script"]')?.value.includes('[voice:Mara]'),
  );
  await page
    .locator('[data-slot=secondary-sidebar]')
    .getByText('Projects', { exact: true })
    .click();
  await page.getByRole('button', { name: 'Delete Renamed book fixture', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await page
    .getByRole('button', { name: 'Renamed book fixture', exact: true })
    .waitFor({ state: 'detached' });
  // The icon rail shows Stories as a group flyout; open it, then pick the Stories editor.
  await page
    .getByRole('navigation', { name: 'Workspaces', exact: true })
    .getByRole('button', { name: 'Stories', exact: true })
    .click();
  await page.getByRole('dialog').getByRole('link', { name: 'Stories', exact: true }).click();
  await page
    .locator('[data-slot=secondary-sidebar]')
    .getByRole('button', { name: 'Narrator fixture', exact: true })
    .click();
  await page.getByRole('button', { name: 'Add First Line', exact: true }).click();
  await page.getByRole('textbox', { name: /Enter dialogue/ }).fill('A story line [pause 0.5s]');
  await page.locator('aside summary').filter({ hasText: /^Cast/ }).click();
  await page.getByRole('button', { name: 'Add character', exact: true }).click();
  await page.getByLabel('Character name', { exact: true }).fill('Mara');
  const character = page
    .locator('aside [data-slot=cast-member]')
    .filter({ has: page.getByLabel('Character name', { exact: true }) });
  await character.getByRole('button', { name: 'Voice: Default', exact: true }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Actor fixture', exact: true })
    .click();
  // Each script line carries one row of chips; the speaker chip opens a picker.
  await page
    .locator('[data-slot=story-block]')
    .first()
    .getByRole('button', { name: 'Speaker: Narrator', exact: true })
    .click();
  await page.getByRole('dialog').getByRole('button', { name: 'Mara', exact: true }).click();
  await page.getByRole('button', { name: 'Add Line', exact: true }).click();
  await page
    .getByRole('textbox', { name: /Enter dialogue/ })
    .last()
    .fill('Second line');
  await page.getByRole('button', { name: 'Move up', exact: true }).last().click();
  await page.locator('summary').filter({ hasText: 'Auto-cast' }).click();
  await page
    .getByRole('textbox', { name: 'Auto-cast', exact: true })
    .fill('[Mara] Hello.\n[Guest] Welcome.');
  await page.getByRole('button', { name: 'Auto-cast', exact: true }).click();
  assert.equal(await page.getByRole('textbox', { name: /Enter dialogue/ }).count(), 4);
  await page.getByLabel('Import', { exact: true }).setInputFiles({
    name: 'captions.srt',
    mimeType: 'text/plain',
    buffer: Buffer.from('1\n00:00:01,000 --> 00:00:02,000\nImported cue'),
  });
  await page.getByRole('textbox', { name: 'Auto-cast', exact: true }).waitFor();
  assert.equal(
    await page.getByRole('textbox', { name: 'Auto-cast', exact: true }).inputValue(),
    'Imported cue',
  );
  await page.getByRole('button', { name: 'Split into lines', exact: true }).click();
  assert.equal(await page.getByRole('textbox', { name: /Enter dialogue/ }).count(), 5);
  await page.getByRole('button', { name: 'Preview this line', exact: true }).first().click();
  // The line audition renders its own player; it does not autoplay.
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.waitForFunction(() =>
    Array.from(document.querySelectorAll('audio')).some((audio) => audio.currentTime > 0),
  );
  assert.equal(auditions, 1);
  await page
    .locator('summary')
    .filter({ hasText: /^Stems$/ })
    .click();
  await page.getByRole('button', { name: 'Stems', exact: true }).click();
  // Stems are save buttons; without the desktop bridge they fall back to a browser download.
  const stemButtons = page
    .locator('details')
    .filter({ has: page.locator('summary', { hasText: /^Stems$/ }) })
    .locator('li button');
  await stemButtons.nth(2).waitFor();
  assert.equal(await stemButtons.count(), 3);
  assert.equal(auditions, 6);
  const downloaded = page.waitForEvent('download');
  await stemButtons.first().click();
  const download = await downloaded;
  assert.match(download.suggestedFilename(), /^story-1-.+\.wav$/);
  const bytes = await readFile(await download.path());
  assert.equal(bytes.toString('ascii', 0, 4), 'RIFF');
  assert.ok(bytes.length > 44);

  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await page.getByRole('heading', { name: 'Audiobook ready', exact: true }).waitFor();
  assert.equal(bodies[1].chapters[0].spans[1].pause_ms_after, 500);
  assert.equal(bodies[1].chapters[0].spans[0].text, 'Second line');
  assert.equal(bodies[1].chapters[0].spans[1].voice_id, 'actor');
  assert.deepEqual(errors, []);
  console.log(
    'PASS: audiobook and story rendering, voice guard, shared pause parser, independent playback and reload persistence',
  );
} finally {
  await browser.close();
}
