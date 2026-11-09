import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { wave } from './test-wave.mjs';
const browser = await chromium.launch({
  ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
    : { channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' }),
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const segmentRows = page.locator('article[data-dub-row-id]');
  const editSegment = async (index) => {
    const row = segmentRows.nth(index);
    const editor = row.locator('textarea[data-segment-id]');
    if (!(await editor.count())) await row.locator('button[data-segment-id]').click();
    await editor.waitFor();
    return editor;
  };
  const waitForSegmentText = (index, text) =>
    page.waitForFunction(
      ({ index, text }) => {
        const row = document.querySelectorAll('article[data-dub-row-id]')[index];
        const field = row?.querySelector('[data-segment-id]');
        const value = field instanceof HTMLTextAreaElement ? field.value : field?.textContent;
        return value?.trim() === text;
      },
      { index, text },
    );
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
    localStorage.setItem('voicestudio.locale', 'en');
    globalThis.__dubAgentRequests = [];
    globalThis.__nativeSaveRequests = [];
    const bridge = {
      app: {
        platform: 'win32',
        version: 'test',
        onNavigate: () => () => {},
      },
      repair: {
        list: async () => [
          { id: 'codex', label: 'Codex', available: true, version: 'codex-cli test' },
        ],
        getState: async () => ({ status: 'idle', output: '', workspaceAvailable: true }),
        onEvent: () => () => {},
        translate: async (request) => {
          globalThis.__dubAgentRequests.push(request);
          return {
            agent: request.agent,
            translations: request.segments.map((segment) => ({
              id: segment.id,
              text: `Agent: ${segment.sourceText}`,
            })),
          };
        },
        stopTranslation: async () => {},
      },
      files: {
        saveAudio: async (request) => {
          const response = await fetch(request.url);
          if (!response.ok) throw new Error('Export failed');
          globalThis.__nativeSaveRequests.push(request);
          return { canceled: false, path: `C:\\Exports\\${request.suggestedName}` };
        },
      },
    };
    Object.defineProperty(window, 'voicestudio', {
      configurable: true,
      value: bridge,
    });
    if (sessionStorage.getItem('voicestudio.test.reset-dub') === '1') {
      localStorage.removeItem('voicestudio.dub.session.v1');
      sessionStorage.removeItem('voicestudio.test.reset-dub');
    }
    const timelineDraft = sessionStorage.getItem('voicestudio.test.timeline-draft');
    if (timelineDraft) {
      localStorage.setItem('voicestudio.dub.session.v1', timelineDraft);
      sessionStorage.removeItem('voicestudio.test.timeline-draft');
    }
  });
  const video = process.env.VOICESTUDIO_TEST_VIDEO
    ? readFileSync(process.env.VOICESTUDIO_TEST_VIDEO)
    : null;
  const errors = [];
  const vidstackWarnings = [];
  let mediaHeadRequests = 0;
  let releaseVideo;
  const videoReady = new Promise((resolve) => {
    releaseVideo = resolve;
  });
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'warning' && message.text().includes('[vidstack]'))
      vidstackWarnings.push(message.text());
  });
  await page.route('**/api/setup/status', (route) =>
    route.fulfill({ json: { models_ready: true, missing: [] } }),
  );
  await page.route('**/api/models/install/status', (route) =>
    route.fulfill({ json: { jobs: [] } }),
  );
  await page.route('**/api/engines', (route) =>
    route.fulfill({
      json: {
        tts: {
          active: 'omnivoice',
          active_model: 'dub-playback-model',
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
            repo_id: 'dub-playback-model',
            label: 'Dubbing playback model',
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
  await page.route('**/api/engines/translation/argos/packs/status', (route) =>
    route.fulfill({ json: { pairs: [] } }),
  );
  await page.route('**/api/glossary/fixture', (route) =>
    route.fulfill({ json: [{ source: 'VoiceStudio', target: 'VoiceStudio' }] }),
  );
  await page.route('**/api/settings/llm-skills', (route) =>
    route.fulfill({
      json: {
        skills: [
          { id: 'cinematic_translation', enabled: true, ready: false },
          { id: 'slot_fitting', enabled: true, ready: false },
        ],
      },
    }),
  );
  let rejectNoAudio = true;
  await page.route('**/api/dub/upload', (route) => {
    if (!rejectNoAudio)
      return route.fulfill({ json: { job_id: 'fixture', task_id: 'prep-fixture' } });
    rejectNoAudio = false;
    return route.fulfill({
      status: 422,
      json: {
        detail: {
          code: 'no_audio_track',
          docs_topic: 'NO_AUDIO_TRACK',
          message: 'This file has no audio track.',
          hint: 'Check that the file plays with sound.',
        },
      },
    });
  });
  await page.route('**/api/tasks/stream/prep-fixture', (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: 'data: {"type":"ready","duration":6}\n\n',
    }),
  );
  let failTranscription = false;
  let transcriptionCalls = 0;
  await page.route('**/api/dub/transcribe-stream/fixture*', (route) => {
    transcriptionCalls++;
    if (transcriptionCalls === 1)
      assert.equal(new URL(route.request().url()).searchParams.get('num_speakers'), '2');
    return route.fulfill({
      contentType: 'text/event-stream',
      body: failTranscription
        ? ': disconnected\n\n'
        : 'event: final\ndata: {"segments":[{"id":"1","start":0,"end":2,"text":"Hello there","speaker_id":"SPEAKER_00"},{"id":"2","start":3.004,"end":5,"text":"Second line","speaker_id":"SPEAKER_01"}],"source_lang":"en","cast_sources":{"SPEAKER_00":{},"SPEAKER_01":{}}}\n\nevent: done\ndata: {}\n\n',
    });
  });
  if (video) {
    await page.route('**/api/dub/thumb/fixture', (route) =>
      route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="teal"/></svg>',
      }),
    );
    await page.route('**/api/dub/media/fixture', async (route) => {
      await videoReady;
      if (route.request().method() === 'HEAD') mediaHeadRequests++;
      return route.fulfill({ contentType: 'video/mp4', body: video });
    });
    await page.route('**/api/dub/preview-video/fixture*', (route) => {
      if (route.request().method() === 'HEAD') mediaHeadRequests++;
      return route.fulfill({ contentType: 'video/mp4', body: video });
    });
  }
  await page.route('**/api/dub/audio/fixture', (route) =>
    route.fulfill({ contentType: 'audio/wav', body: wave }),
  );
  const translations = [];
  let fallback = false;
  await page.route('**/api/dub/translate', (route) => {
    translations.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        translated: [{ id: '1', text: 'Hola' }],
        cinematic_skipped: fallback ? 'no-llm-configured' : undefined,
      },
    });
  });
  await page.route('**/api/dub/import-srt/fixture', (route) =>
    route.fulfill({
      json: {
        segments: [
          { id: '1', start: 0.5, end: 2, text: 'Imported subtitle', profile_id: 'auto:speaker00' },
        ],
        stats: { imported: 1, skipped_malformed: 2, dropped_overlap: 0, clamped_to_duration: 0 },
      },
    }),
  );
  let urlRequest;
  await page.route('**/api/dub/ingest-url', (route) => {
    urlRequest = route.request().postDataJSON();
    return route.fulfill({ json: { job_id: 'fixture', task_id: 'prep-fixture' } });
  });
  let generation;
  let generationFailure = null;
  await page.route('**/api/dub/generate/fixture', (route) => {
    generation = route.request().postDataJSON();
    return route.fulfill({ json: { task_id: 'generation-fixture' } });
  });
  await page.route('**/api/tasks/stream/generation-fixture', (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: generationFailure
        ? `data: ${JSON.stringify({ type: 'error', stage: 'generate', ...generationFailure })}\n\n`
        : 'data: {"type":"progress","current":0,"total":1}\n\ndata: {"type":"done","tracks":["es"]}\n\n',
    }),
  );
  await page.goto(
    new URL('/#/dub', process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902').href,
  );
  // Spoken language and speaker count are compact rows, open without a disclosure.
  await page.getByRole('button', { name: 'Spoken language', exact: true }).click();
  await page.getByRole('combobox', { name: 'Spoken language', exact: true }).fill('French');
  await page.getByRole('option', { name: 'French', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Speakers', exact: true }).fill('2');

  // A file without sound is a choose-another-file prompt on the file row, not a failure banner.
  await page
    .locator('input[type=file]')
    .first()
    .setInputFiles({ name: 'Screen Recording.mov', mimeType: 'video/quicktime', buffer: wave });
  const sourceRow = page.locator('[data-slot="dub-source-row"]');
  await sourceRow
    .getByText('No audio track — choose a video or audio file with sound', { exact: true })
    .waitFor();
  assert.equal(await page.getByRole('alert').count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Copy diagnostic' }).count(), 0);
  assert.equal(await page.getByText('Video dubbing studio', { exact: true }).count(), 0);
  await page
    .locator('[data-slot="dub-source-status"]')
    .getByRole('button', { name: 'Change file', exact: true })
    .waitFor();

  await page
    .locator('input[type=file]')
    .first()
    .setInputFiles(
      video
        ? { name: 'sample.mp4', mimeType: 'video/mp4', buffer: video }
        : { name: 'sample.wav', mimeType: 'audio/wav', buffer: wave },
    );
  await segmentRows.first().waitFor();
  const translateWithAgent = page.locator('[data-slot="translate-with-agent"]');
  await translateWithAgent.waitFor();
  assert.equal((await translateWithAgent.innerText()).trim(), 'Translate with Agent');
  await page.waitForFunction(() =>
    document.querySelector('[data-slot="translate-with-agent"]')?.hasAttribute('title'),
  );
  assert.equal(await translateWithAgent.isEnabled(), true);
  assert.match(await translateWithAgent.getAttribute('title'), /Codex/);
  let script = await editSegment(0);
  if (video) {
    const poster = page.locator('[data-media-player] img[src*="/dub/thumb/fixture"]');
    await poster.waitFor({ state: 'visible', timeout: 5000 });
    await page.waitForFunction(() => {
      const image = document.querySelector('[data-media-player] img[src*="/dub/thumb/fixture"]');
      return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0;
    });
    await page.getByRole('button', { name: 'Play', exact: true }).click();
    await page.waitForTimeout(100);
    releaseVideo();
    await page.waitForFunction(() => {
      const image = document.querySelector('[data-media-player] img[src*="/dub/thumb/fixture"]');
      return image && getComputedStyle(image).opacity === '0';
    });
    await page.waitForFunction(() => {
      const video = document.querySelector('video');
      return video && !video.paused && video.currentTime > 0.1;
    });
    await page.getByRole('button', { name: 'Pause', exact: true }).click();
    await page.getByRole('button', { name: 'Mute', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('video')?.muted);
    await page.getByRole('slider', { name: 'Volume', exact: true }).fill('0.35');
    await page.waitForFunction(() => {
      const video = document.querySelector('video');
      return video && !video.muted && Math.abs(video.volume - 0.35) < 0.01;
    });
    await page.getByRole('button', { name: 'Full screen', exact: true }).click();
    await page.waitForFunction(() => Boolean(document.fullscreenElement));
    await page.getByRole('button', { name: 'Exit full screen', exact: true }).click();
    await page.waitForFunction(() => !document.fullscreenElement);
  }
  script = await editSegment(0);
  assert.equal(await script.inputValue(), 'Hello there');
  await editSegment(1);
  const timeline = page.getByRole('region', { name: 'Segment timeline', exact: true });
  await timeline.waitFor();
  assert.equal(await timeline.locator('[data-timeline-segment]').count(), 2);
  await timeline.locator('[data-timeline-segment]').first().press('Enter');
  await timeline.locator('[data-timeline-segment]').first().press('Control+ArrowRight');
  await segmentRows.first().locator('[data-segment-options-trigger]').click();
  assert.equal(
    await segmentRows.first().getByRole('spinbutton', { name: 'Start', exact: true }).inputValue(),
    '0.10',
  );
  await segmentRows.first().locator('[data-segment-options-trigger]').click();
  await segmentRows.nth(1).locator('[data-segment-options-trigger]').click();
  const secondStart = segmentRows.nth(1).getByRole('spinbutton', { name: 'Start', exact: true });
  // 3.004 shows as 3.00; tabbing through the untouched field must not commit
  // that rounding, so Undo still reverts the nudge above.
  await secondStart.focus();
  await secondStart.press('Tab');
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await segmentRows.first().locator('[data-segment-options-trigger]').click();
  assert.equal(
    await segmentRows.first().getByRole('spinbutton', { name: 'Start', exact: true }).inputValue(),
    '0.00',
  );
  await segmentRows.first().locator('[data-segment-options-trigger]').click();
  await page.getByRole('button', { name: 'Redo', exact: true }).click();
  await segmentRows.nth(1).locator('[data-segment-options-trigger]').click();
  await secondStart.fill('1.9');
  await secondStart.press('Tab');
  assert.equal(await secondStart.inputValue(), '2.00');
  assert.equal(
    await timeline
      .getByText('Overlaps an adjacent segment — both lines will play together', { exact: true })
      .count(),
    0,
  );
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await segmentRows.nth(1).locator('[data-segment-options-trigger]').click();
  await segmentRows.first().getByRole('button', { name: 'More actions', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Insert line below', exact: true }).click();
  assert.equal(await segmentRows.count(), 3);
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  assert.equal(await segmentRows.count(), 2);
  await page.getByRole('button', { name: 'Redo', exact: true }).click();
  assert.equal(await segmentRows.count(), 3);
  await segmentRows.first().locator('[data-segment-options-trigger]').click();
  await segmentRows.first().getByRole('spinbutton', { name: 'Start', exact: true }).press('Control+z');
  assert.equal(await segmentRows.count(), 3);
  await segmentRows.first().locator('[data-segment-options-trigger]').click();
  await segmentRows.nth(1).getByRole('button', { name: 'Delete', exact: true }).click();
  assert.equal(await segmentRows.count(), 2);
  await segmentRows.first().getByRole('button', { name: 'More actions', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Merge with next', exact: true }).click();
  assert.equal(await segmentRows.count(), 1);
  script = await editSegment(0);
  assert.equal(await script.inputValue(), 'Hello there Second line');
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  script = await editSegment(0);
  await script.evaluate((element) => {
    element.focus();
    element.setSelectionRange(5, 5);
  });
  await segmentRows.first().getByRole('button', { name: 'More actions', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Split at cursor', exact: true }).click();
  assert.equal(await segmentRows.count(), 3);
  assert.equal(await (await editSegment(0)).inputValue(), 'Hello');
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  assert.equal(await segmentRows.count(), 2);
  await page
    .locator('input[type=file]')
    .nth(1)
    .setInputFiles({
      name: 'captions.srt',
      mimeType: 'application/x-subrip',
      buffer: Buffer.from('1\n00:00:00,500 --> 00:00:02,000\nImported subtitle'),
    });
  await waitForSegmentText(0, 'Imported subtitle');
  await page.getByText('2 skipped (malformed)', { exact: true }).waitFor();
  const translatorPicker = page.getByRole('group', { name: 'Translator', exact: true });
  await translatorPicker.getByRole('button', { name: 'Agent', exact: true }).click();
  await page.getByRole('button', { name: 'Translate', exact: true }).click();
  await waitForSegmentText(0, 'Agent: Imported subtitle');
  const agentRequest = await page.evaluate(() => globalThis.__dubAgentRequests.at(-1));
  assert.equal(agentRequest.agent, 'codex');
  assert.equal(agentRequest.purpose, 'translate');
  // The user picked French under Spoken language before transcribing, and a
  // deliberate choice wins over the transcript's detected 'en' (#3442).
  assert.equal(agentRequest.sourceLanguage, 'fr');
  assert.equal(agentRequest.targetLanguage, 'Spanish');
  assert.deepEqual(agentRequest.glossary, [{ source: 'VoiceStudio', target: 'VoiceStudio' }]);
  assert.deepEqual(agentRequest.segments, [
    {
      id: '1',
      sourceText: 'Imported subtitle',
      start: 0.5,
      end: 2,
    },
  ]);
  assert.equal(generation, undefined, 'translation must remain reviewable before generation');
  await translatorPicker.getByRole('button', { name: 'Argos', exact: true }).click();
  // Translation controls stay expanded without an extra disclosure click.
  await page.getByRole('button', { name: 'Cinematic', exact: true }).waitFor({ state: 'visible' });
  await page.getByRole('button', { name: 'Cinematic', exact: true }).click();
  await page.getByRole('button', { name: 'Translate every segment', exact: true }).click();
  await waitForSegmentText(0, 'Hola');
  assert.equal(translations[0].quality, 'cinematic');
  await page
    .locator('summary')
    .filter({ hasText: /^Timing$/ })
    .click();
  await page.getByRole('button', { name: 'Stretch Video', exact: true }).click();
  await page.getByRole('button', { name: 'Consistent', exact: true }).click();
  await page.locator('summary').filter({ hasText: 'Voice controls' }).click();
  await page.getByRole('slider', { name: 'Steps', exact: true }).press('End');
  await page.getByRole('slider', { name: 'CFG', exact: true }).press('Home');
  await page.getByRole('slider', { name: 'Speed', exact: true }).press('Home');
  await page.getByRole('textbox', { name: 'Voice style', exact: true }).fill('calm narrator');
  await page.locator('[data-segment-options-trigger]').first().click();
  await page.getByRole('spinbutton', { name: 'Volume', exact: true }).fill('0');
  await page.getByRole('spinbutton', { name: 'Speed', exact: true }).fill('1.2');
  await page.getByRole('textbox', { name: 'Direction', exact: true }).fill('whispered');
  script = await editSegment(0);
  await script.fill('Hola mundo');
  await page.waitForTimeout(300);
  await page.reload();
  script = await editSegment(0);
  assert.equal(await script.inputValue(), 'Hola mundo');
  // Translation controls stay expanded without an extra disclosure click.
  await page.getByRole('button', { name: 'Cinematic', exact: true }).waitFor({ state: 'visible' });
  assert.equal(
    await page.getByRole('button', { name: 'Cinematic', exact: true }).getAttribute('aria-pressed'),
    'true',
  );
  await page
    .locator('summary')
    .filter({ hasText: /^Timing$/ })
    .click();
  assert.equal(
    await page
      .getByRole('button', { name: 'Stretch Video', exact: true })
      .getAttribute('aria-pressed'),
    'true',
  );
  assert.equal(
    await page
      .getByRole('button', { name: 'Consistent', exact: true })
      .getAttribute('aria-pressed'),
    'true',
  );
  fallback = true;
  await page.getByRole('button', { name: 'Autofit', exact: true }).click();
  await page.getByRole('button', { name: 'Translate every segment', exact: true }).click();
  await page
    .getByRole('button', { name: 'Fast', exact: true })
    .and(page.locator('[aria-pressed="true"]'))
    .waitFor();
  assert.equal(translations[1].quality, 'autofit');
  await page
    .getByText('This quality needs an LLM provider. Configure one in LLM Providers to continue.', {
      exact: true,
    })
    .waitFor();
  assert.match(
    await page.getByRole('status').getByRole('link').getAttribute('href'),
    /\/settings\/models\/llm$/,
  );
  await page.getByRole('button', { name: 'Generate Dub', exact: true }).click();
  const dubbedPreview = page.getByRole('button', { name: 'es dubbed audio', exact: true });
  await dubbedPreview.waitFor();
  if (video) {
    await page.evaluate(async () => {
      const media = document.querySelector('video');
      if (!media) throw new Error('missing Dubbing preview video');
      media.currentTime = 0.75;
      await media.play();
    });
    await dubbedPreview.click();
    await page.waitForFunction(() => {
      const media = document.querySelector('video');
      return Boolean(
        media &&
        media.currentSrc.includes('/preview-video/fixture') &&
        media.currentTime >= 0.7 &&
        !media.paused,
      );
    });
  }
  assert.equal(generation.segments[0].text, 'Hola');
  assert.equal(generation.segments[0].gain, 0);
  assert.equal(generation.segments[0].speed, 1.2);
  assert.equal(generation.segments[0].direction, 'whispered');
  assert.equal(generation.segments[0].profile_id, 'auto:speaker00');
  assert.equal(generation.language_code, 'es');
  assert.equal(generation.timing_strategy, 'stretch_video');
  assert.equal(generation.voice_match, 'consistent');
  assert.equal(generation.num_step, 64);
  assert.equal(generation.guidance_scale, 0);
  assert.equal(generation.speed, 0.5);
  assert.equal(generation.instruct, 'calm narrator');
  await page.locator('summary').filter({ hasText: 'Export options' }).click();
  assert.equal(await page.getByRole('button', { name: 'Export', exact: true }).isEnabled(), true);
  if (video) {
    assert.equal(
      await page
        .getByRole('switch', { name: 'Burn into video', exact: true })
        .isDisabled(),
      true,
    );
    await page.getByText('Stretch Video: export subtitles separately.', { exact: true }).waitFor();
  }
  const exportRequests = [];
  let rejectExport = false;
  await page.route('**/api/dub/download-mp3/fixture?*', (route) => {
    exportRequests.push(new URL(route.request().url()));
    return route.fulfill(
      rejectExport
        ? { status: 500, body: 'private backend trace' }
        : { contentType: 'audio/mpeg', body: wave },
    );
  });
  await page.route('**/api/export/record', (route) => route.fulfill({ json: { ok: true } }));
  await page.getByRole('button', { name: 'MP3 · compressed', exact: true }).click();
  await page.getByRole('button', { name: '320 kbps', exact: true }).click();
  // #2964: the background bed level reaches the export while the background is mixed in.
  const bedLevel = page.getByRole('slider', { name: 'Background volume', exact: true });
  await bedLevel.focus();
  await bedLevel.press('ArrowRight');
  await page.getByText('110%', { exact: true }).waitFor();
  rejectExport = true;
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  await page.getByRole('alert').waitFor();
  assert.equal(await page.getByText('private backend trace').count(), 0);
  assert.equal(exportRequests.at(-1).searchParams.get('preserve_bg'), 'true');
  assert.equal(exportRequests.at(-1).searchParams.get('bed_level'), '1.1');
  await page.getByRole('switch', { name: 'Background audio', exact: true }).click();
  await bedLevel.waitFor({ state: 'hidden' });
  rejectExport = false;
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  await page.waitForFunction(() => globalThis.__nativeSaveRequests.length === 1);
  let savedExport = await page.evaluate(() => globalThis.__nativeSaveRequests.at(-1));
  assert.match(savedExport.suggestedName, /-es-mp3\.mp3$/);
  assert.equal(exportRequests.at(-1).searchParams.get('lang'), 'es');
  assert.equal(exportRequests.at(-1).searchParams.get('bitrate'), '320k');
  assert.equal(exportRequests.at(-1).searchParams.get('preserve_bg'), 'false');
  assert.equal(exportRequests.at(-1).searchParams.get('bed_level'), null);
  let subtitleRequest;
  await page.route('**/api/dub/srt/fixture?*', (route) => {
    subtitleRequest = new URL(route.request().url());
    return route.fulfill({
      contentType: 'application/x-subrip',
      body: '1\n00:00:00,000 --> 00:00:02,000\nHola',
    });
  });
  await page.getByRole('button', { name: 'SRT', exact: true }).click();
  await page.getByRole('switch', { name: 'Translation + original', exact: true }).click();
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  await page.waitForFunction(() => globalThis.__nativeSaveRequests.length === 2);
  savedExport = await page.evaluate(() => globalThis.__nativeSaveRequests.at(-1));
  assert.match(savedExport.suggestedName, /-es-srt\.srt$/);
  assert.equal(subtitleRequest.searchParams.get('dual'), 'true');
  assert.equal(subtitleRequest.searchParams.get('lang'), 'es');
  await page.reload();
  await page.locator('summary').filter({ hasText: 'Export options' }).click();
  assert.equal(
    await page.getByRole('button', { name: 'SRT', exact: true }).getAttribute('aria-pressed'),
    'true',
  );
  assert.equal(
    await page
      .getByRole('switch', { name: 'Translation + original', exact: true })
      .getAttribute('aria-checked'),
    'true',
  );
  await page.getByRole('button', { name: 'MP3 · compressed', exact: true }).click();
  assert.equal(
    await page
      .getByRole('switch', { name: 'Background audio', exact: true })
      .getAttribute('aria-checked'),
    'false',
  );
  assert.equal(
    await page.getByRole('button', { name: '320 kbps', exact: true }).getAttribute('aria-pressed'),
    'true',
  );
  // A prepared upload can be replaced with a URL without first removing it.
  // The file row keeps Change file / Use link / Remove in one overflow menu.
  const useLink = async () => {
    await page.getByRole('button', { name: 'File actions', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Use link', exact: true }).click();
  };
  assert.equal(
    await page.locator('[data-slot="dub-source-row"]').getByRole('button').count(),
    1,
    'the file row has a single actions trigger instead of wrapping text buttons',
  );
  const linkInput = page.getByRole('textbox', { name: /paste a video URL/ });
  await useLink();
  await linkInput.fill('https://example.com/cancelled.mp4');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.equal(await linkInput.count(), 0);
  assert.equal(urlRequest, undefined);
  await useLink();
  await linkInput.fill('https://example.com/stale.mp4');
  await page.evaluate(async () => {
    const { dubSession } = await import('/src/features/dub/dub-session.ts');
    dubSession.setState((current) => ({
      ...current,
      jobId: 'replacement',
      filename: 'replacement.mp4',
    }));
  });
  await linkInput.waitFor({ state: 'hidden' });
  await useLink();
  assert.equal(await linkInput.inputValue(), '');
  // Cancellation can outlive the active stream; retain the replacement form.
  let finishAbort;
  const abortReady = new Promise((resolve) => {
    finishAbort = resolve;
  });
  await page.route('**/api/dub/abort/replacement', async (route) => {
    await abortReady;
    await route.fulfill({ json: {} });
  });
  await linkInput.fill('https://example.com/pending.mp4');
  const abortRequest = page.waitForRequest('**/api/dub/abort/replacement');
  await page.evaluate(async () => {
    const { dubSession, cancelDub } = await import('/src/features/dub/dub-session.ts');
    dubSession.setState((current) => ({ ...current, batchProgress: { current: 1, total: 2 } }));
    void cancelDub();
  });
  await abortRequest;
  await page.waitForFunction(() =>
    [...document.querySelectorAll('button')].some(
      (button) => button.textContent?.trim() === 'Ingest' && button.disabled,
    ),
  );
  await linkInput.evaluate((input) =>
    input.closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  );
  assert.equal(await linkInput.inputValue(), 'https://example.com/pending.mp4');
  assert.equal(urlRequest, undefined);
  finishAbort();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('button')].some(
      (button) => button.textContent?.trim() === 'Ingest' && !button.disabled,
    ),
  );
  // A completed transcript longer than the virtual window must reveal cues
  // without scrolling the timeline being operated out of the workspace.
  await page.evaluate(() => {
    const draft = JSON.parse(localStorage.getItem('voicestudio.dub.session.v1'));
    draft.jobId = 'fixture';
    draft.segments = Array.from({ length: 120 }, (_, index) => ({
      id: `long-${index}`,
      start: index * 3,
      end: index * 3 + 2,
      text: `Translated cue ${index + 1}`,
      text_original: `Original cue ${index + 1}`,
      speaker_id: 'SPEAKER_00',
      translations: { es: `Translated cue ${index + 1}` },
    }));
    draft.duration = 360;
    draft.phase = 'done';
    sessionStorage.setItem('voicestudio.test.timeline-draft', JSON.stringify(draft));
  });
  await page.reload();
  const assertTimelineCueVisible = async (id) => {
    await page.waitForFunction((id) => {
      const transcript = document.querySelector('[data-slot="dub-transcript"]');
      const scroll = [...(transcript?.children || [])].find(
        (element) => getComputedStyle(element).overflowY === 'auto',
      );
      const row = document.querySelector(`article[data-dub-row-id="${id}"]`);
      if (!scroll || !row) return false;
      const viewport = scroll.getBoundingClientRect();
      const bounds = row.getBoundingClientRect();
      return bounds.top >= viewport.top - 1 && bounds.bottom <= viewport.bottom + 1;
    }, id);
    const layout = await page.evaluate((id) => {
      const transcript = document.querySelector('[data-slot="dub-transcript"]');
      const timeline = transcript.querySelector('section[aria-label="Segment timeline"]');
      const toolbar = transcript.querySelector('[data-slot="dub-transcript-toolbar"]');
      const scroll = [...transcript.children].find(
        (element) => getComputedStyle(element).overflowY === 'auto',
      );
      const row = transcript.querySelector(`article[data-dub-row-id="${id}"]`);
      const bounds = transcript.getBoundingClientRect();
      const controls = timeline.getBoundingClientRect();
      const cue = row.getBoundingClientRect();
      return {
        timelineVisible: controls.top >= bounds.top - 1 && controls.bottom <= bounds.bottom + 1,
        cueBelowTimeline: cue.top >= controls.bottom - 1,
        toolbarClear: !toolbar || toolbar.getBoundingClientRect().bottom <= controls.top + 1,
        cueViewportHeight: scroll.clientHeight,
      };
    }, id);
    assert.equal(
      layout.timelineVisible,
      true,
      `${id}: timeline must stay in the transcript viewport`,
    );
    assert.equal(layout.cueBelowTimeline, true, `${id}: controls must not cover the revealed cue`);
    assert.equal(layout.toolbarClear, true, `${id}: timeline must not overlap the toolbar`);
    assert(layout.cueViewportHeight >= 140, 'compact layouts must retain usable transcript space');
  };
  for (const [index, viewport] of [
    { width: 1400, height: 900 },
    { width: 1000, height: 600 },
  ].entries()) {
    await page.setViewportSize(viewport);
    await timeline.locator('[data-timeline-segment="long-90"]').waitFor();
    assert((await segmentRows.count()) < 120, 'long transcript must remain virtualized');
    assert.equal(await page.locator('article[data-dub-row-id="long-90"]').count(), 0);
    await timeline.locator('[data-timeline-segment="long-90"]').click();
    await assertTimelineCueVisible('long-90');
    // Use the actual focused control, not locator.press (which can auto-scroll
    // an offscreen timeline and hide the regression).
    await page.keyboard.press('ArrowRight');
    await assertTimelineCueVisible('long-91');
    assert.equal(
      await page.evaluate(() => document.activeElement?.getAttribute('data-timeline-segment')),
      'long-91',
    );
    await page.keyboard.press('Enter');
    await page.keyboard.press('Control+ArrowRight');
    const selectedRow = page.locator('article[data-dub-row-id="long-91"]');
    await selectedRow.locator('[data-segment-options-trigger]').click();
    assert.equal(
      await selectedRow.getByRole('spinbutton', { name: 'Start', exact: true }).inputValue(),
      (273 + (index + 1) * 0.1).toFixed(2),
    );
    await page.waitForFunction(
      (expectedStart) => {
        const draft = JSON.parse(localStorage.getItem('voicestudio.dub.session.v1'));
        return Math.abs(draft.segments[91].start - expectedStart) < 0.0001;
      },
      273 + (index + 1) * 0.1,
    );
    const timings = await page.evaluate(() =>
      JSON.parse(localStorage.getItem('voicestudio.dub.session.v1')).segments.map(
        ({ start, end }) => ({ start, end }),
      ),
    );
    for (const [cueIndex, timing] of timings.entries()) {
      assert(
        Math.abs(timing.start - (cueIndex * 3 + (cueIndex === 91 ? (index + 1) * 0.1 : 0))) <
          0.0001,
        `nudge must only change long-91, not long-${cueIndex}`,
      );
      assert.equal(timing.end, cueIndex * 3 + 2);
    }
    // Expanded row measurements and a resized content prefix must not corrupt
    // subsequent virtualized reveals.
    await timeline.locator('[data-timeline-segment="long-5"]').click();
    await assertTimelineCueVisible('long-5');
    await page.locator('article[data-dub-row-id="long-5"] input[type="checkbox"]').check();
    await timeline.locator('[data-timeline-segment="long-110"]').click();
    await assertTimelineCueVisible('long-110');
    await timeline.locator('[data-timeline-segment="long-91"]').click();
    await selectedRow.locator('[data-segment-options-trigger]').click();
    await timeline.locator('[data-timeline-segment="long-0"]').click();
    await assertTimelineCueVisible('long-0');
  }
  // Error notices sit above the same virtualized list without resizing the
  // scrollport. Appearing, growing and dismissing one must keep reveals accurate.
  const generationNotice = page.locator('[data-slot="dub-transcript-scroll"] [role="alert"]');
  for (const hint of [
    'Retry generation after the model is ready.',
    'Retry generation after the model is ready. Confirm that the selected voice model is installed and its reference clip can be read. The existing transcript remains editable while the model problem is resolved. No cue edits need to be discarded, and generation can be retried when the local engine is available again.',
  ]) {
    generationFailure = { reason: 'The voice model could not generate this dub.', hint };
    await page.getByRole('button', { name: 'Generate Dub', exact: true }).click();
    await generationNotice.getByText(hint, { exact: true }).waitFor();
    await timeline.locator('[data-timeline-segment="long-90"]').click();
    await assertTimelineCueVisible('long-90');
  }
  await generationNotice.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await generationNotice.waitFor({ state: 'hidden' });
  await timeline.locator('[data-timeline-segment="long-0"]').click();
  await assertTimelineCueVisible('long-0');
  generationFailure = null;
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.evaluate(() => sessionStorage.setItem('voicestudio.test.reset-dub', '1'));
  await page.reload();
  failTranscription = true;
  await page.getByRole('textbox', { name: /paste a video URL/ }).fill('https://example.com/movie.mp4');
  await page
    .locator('summary')
    .filter({ hasText: /^Advanced$/ })
    .click();
  await page.getByRole('switch', { name: 'Download available captions', exact: true }).click();
  await page.getByLabel('Choose a cookies.txt export', { exact: true }).setInputFiles({
    name: 'large.txt',
    mimeType: 'text/plain',
    buffer: Buffer.alloc(1024 * 1024 + 1),
  });
  await page.getByText('The cookie export must be 1 MB or smaller.', { exact: true }).waitFor();
  await page.getByLabel('Choose a cookies.txt export', { exact: true }).setInputFiles({
    name: 'cookies.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('# Netscape HTTP Cookie File\nfixture-secret'),
  });
  const ingestResponse = page.waitForResponse('**/api/dub/ingest-url');
  await page.getByRole('button', { name: 'Ingest', exact: true }).click();
  await ingestResponse;
  assert.equal(urlRequest.fetch_subs, true);
  assert.equal(urlRequest.cookie_file, '# Netscape HTTP Cookie File\nfixture-secret');
  assert.equal(await page.getByText('cookies.txt', { exact: true }).count(), 0);
  assert.equal(
    await page.evaluate(() => JSON.stringify(localStorage).includes('fixture-secret')),
    false,
  );
  await page.getByRole('button', { name: 'Retry', exact: true }).waitFor();
  const attempts = transcriptionCalls;
  await page.reload();
  await page.getByRole('button', { name: 'Retry', exact: true }).waitFor();
  assert.equal(transcriptionCalls, attempts);
  failTranscription = false;
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await waitForSegmentText(0, 'Hello there');
  assert.equal(transcriptionCalls, attempts + 1);

  // Issue navigation must reach unmounted rows, not just the visible transcript.
  await page.evaluate(async () => {
    const { dubSession } = await import('/src/features/dub/dub-session.ts');
    const segments = Array.from({ length: 1000 }, (_, index) => ({
      id: `issue-nav-${index}`,
      start: index * 2,
      end: index * 2 + 1,
      text: `Transcript line ${index + 1}`,
      text_original: `Transcript line ${index + 1}`,
    }));
    segments[3].plan = { status: 'tight', est_dur_s: 1.2, available_s: 1, est_overrun_s: 0.2 };
    segments[499].plan = { status: 'impossible', est_dur_s: 3, available_s: 1, est_overrun_s: 2 };
    segments[995].fit_status = { status: 'overflows', overflow_s: 1 };
    dubSession.setState((current) => ({
      ...current,
      segments,
      duration: 2000,
      phase: 'editing',
      tracks: [],
    }));
  });
  const issues = page.getByRole('group', { name: 'Transcript issues', exact: true });
  const nextIssue = issues.getByRole('button', { name: 'Next issue', exact: true });
  const previousIssue = issues.getByRole('button', { name: 'Previous issue', exact: true });
  await issues.getByText('Flagged segments: 3', { exact: true }).waitFor();
  assert.equal(await page.locator('[data-dub-row-id="issue-nav-995"]').count(), 0);
  const waitForIssue = (id) =>
    page.waitForFunction((targetId) => {
      const row = document.querySelector(`[data-dub-row-id="${targetId}"]`);
      if (!row || row.getAttribute('aria-current') !== 'true') return false;
      const rect = row.getBoundingClientRect();
      const scroll = row.closest('.studio-scrollbar')?.getBoundingClientRect();
      const controls = document
        .querySelector('[aria-label="Transcript issues"]')
        ?.getBoundingClientRect();
      return (
        scroll &&
        controls &&
        rect.top >= Math.max(scroll.top, controls.bottom) &&
        rect.bottom <= scroll.bottom
      );
    }, id);
  await nextIssue.focus();
  await nextIssue.press('Enter');
  await waitForIssue('issue-nav-3');
  assert.equal(await nextIssue.evaluate((button) => button === document.activeElement), true);
  await issues.getByText('Issue 1 of 3', { exact: true }).waitFor();
  await previousIssue.click();
  await waitForIssue('issue-nav-995');
  await issues.getByText('Issue 3 of 3', { exact: true }).waitFor();
  assert.ok((await segmentRows.count()) < 60, 'issue navigation must preserve virtualization');
  await nextIssue.click();
  await waitForIssue('issue-nav-3');
  await nextIssue.click();
  await waitForIssue('issue-nav-499');
  // A resolved current row is still the cursor: next must advance, not skip.
  await page.evaluate(async () => {
    const { editDubSegment } = await import('/src/features/dub/dub-session.ts');
    editDubSegment('issue-nav-499', { text: 'Shorter line' });
  });
  await issues.getByText('Flagged segments: 2', { exact: true }).waitFor();
  await nextIssue.click();
  await waitForIssue('issue-nav-995');
  await page.evaluate(async () => {
    const { dubSession } = await import('/src/features/dub/dub-session.ts');
    dubSession.setState((current) => ({ ...current, phase: 'generating' }));
  });
  assert.equal(await nextIssue.isDisabled(), true);
  assert.equal(await previousIssue.isDisabled(), true);
  await page.evaluate(async () => {
    const { dubSession } = await import('/src/features/dub/dub-session.ts');
    dubSession.setState((current) => ({
      ...current,
      phase: 'editing',
      segments: current.segments.map(({ plan, fit_status, ...segment }) => segment),
    }));
  });
  await issues.getByText('Flagged segments: 0', { exact: true }).waitFor();
  assert.equal(await nextIssue.isDisabled(), true);
  assert.equal(await previousIssue.isDisabled(), true);

  // A later successful translation clears the scalar error, but an earlier
  // language's failure must remain discoverable when switching back to it.
  await page.evaluate(async () => {
    const { dubSession, setDubTarget } = await import('/src/features/dub/dub-session.ts');
    dubSession.setState((current) => ({
      ...current,
      segments: current.segments.map((segment, index) =>
        index === 123
          ? {
              ...segment,
              translate_error: undefined,
              translate_errors: { fr: 'French translation failed' },
              translations: { de: 'Guten Tag' },
            }
          : segment,
      ),
    }));
    setDubTarget('German', 'de');
  });
  await issues.getByText('Flagged segments: 0', { exact: true }).waitFor();
  await page.evaluate(async () => {
    const { setDubTarget } = await import('/src/features/dub/dub-session.ts');
    setDubTarget('French', 'fr');
  });
  await issues.getByText('Flagged segments: 1', { exact: true }).waitFor();
  await nextIssue.click();
  await waitForIssue('issue-nav-123');
  const translationRow = page.locator('[data-dub-row-id="issue-nav-123"]');
  await translationRow.locator('[title*="French translation failed"]').waitFor();
  await page.evaluate(async () => {
    const { setDubTarget } = await import('/src/features/dub/dub-session.ts');
    setDubTarget('German', 'de');
  });
  await issues.getByText('Flagged segments: 0', { exact: true }).waitFor();
  assert.equal(await translationRow.locator('[title*="French translation failed"]').count(), 0);
  await page.evaluate(async () => {
    const { setDubTarget } = await import('/src/features/dub/dub-session.ts');
    setDubTarget('French', 'fr');
  });
  await issues.getByText('Issue 1 of 1', { exact: true }).waitFor();
  await page.evaluate(async () => {
    const { applyDubTranslationRows } = await import('/src/features/dub/dub-session.ts');
    applyDubTranslationRows('fr', [
      {
        id: 'issue-nav-123',
        index: 123,
        start: 246,
        end: 247,
        before: 'Transcript line 124',
        after: 'Bonjour',
        matched: true,
      },
    ]);
  });
  await issues.getByText('Flagged segments: 0', { exact: true }).waitFor();
  assert.equal(await nextIssue.isDisabled(), true);
  assert.equal(await translationRow.locator('[title*="French translation failed"]').count(), 0);
  assert.deepEqual(errors, []);
  assert.deepEqual(vidstackWarnings, []);
  if (video) assert.equal(mediaHeadRequests, 0);
  if (process.env.VOICESTUDIO_SCREENSHOT)
    await page.screenshot({ path: process.env.VOICESTUDIO_SCREENSHOT });
  console.log(
    'PASS: dub upload, streamed transcription, subtitle import, translate, edit, reload recovery, generate, export readiness, long virtualized timeline visibility, error-notice resizing and dismissal, and URL import',
  );
} finally {
  await browser.close();
}
