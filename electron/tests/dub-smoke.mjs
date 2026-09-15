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
  });
  const video = process.env.VOICESTUDIO_TEST_VIDEO
    ? readFileSync(process.env.VOICESTUDIO_TEST_VIDEO)
    : null;
  const errors = [];
  const vidstackWarnings = [];
  let mediaHeadRequests = 0;
  let releaseVideo;
  const videoReady = new Promise((resolve) => { releaseVideo = resolve; });
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
  await page.route('**/api/workers/target**', (route) =>
    route.fulfill({
      json: {
        target: 'local',
        op: 'dub',
        active: { remote: false, label: 'Local device', reason: '' },
        remote_operations: [],
        targets: [],
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
  await page.route('**/api/dub/upload', (route) =>
    route.fulfill({ json: { job_id: 'fixture', task_id: 'prep-fixture' } }),
  );
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
        : 'event: final\ndata: {"segments":[{"id":"1","start":0,"end":2,"text":"Hello there","speaker_id":"SPEAKER_00"},{"id":"2","start":3,"end":5,"text":"Second line","speaker_id":"SPEAKER_01"}],"source_lang":"en","cast_sources":{"SPEAKER_00":{},"SPEAKER_01":{}}}\n\nevent: done\ndata: {}\n\n',
    });
  });
  if (video) {
    await page.route('**/api/dub/thumb/fixture', (route) =>
      route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="teal"/></svg>' }),
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
  await page.route('**/api/dub/generate/fixture', (route) => {
    generation = route.request().postDataJSON();
    return route.fulfill({ json: { task_id: 'generation-fixture' } });
  });
  await page.route('**/api/tasks/stream/generation-fixture', (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: 'data: {"type":"progress","current":0,"total":1}\n\ndata: {"type":"done","tracks":["es"]}\n\n',
    }),
  );
  await page.goto(
    new URL('/#/dub', process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902').href,
  );
  await page.locator('summary').filter({ hasText: 'Spoken language' }).click();
  const analysis = page
    .locator('details')
    .filter({ has: page.locator('summary').filter({ hasText: 'Spoken language' }) });
  await analysis.getByRole('button', { name: 'Language', exact: true }).click();
  await page.getByRole('combobox', { name: 'Language', exact: true }).fill('French');
  await page.getByRole('option', { name: 'French', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Speakers', exact: true }).fill('2');

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
  await translatorPicker.getByRole('button', { name: 'Translate with Agent', exact: true }).click();
  await page.getByRole('button', { name: 'Translate', exact: true }).click();
  await waitForSegmentText(0, 'Agent: Imported subtitle');
  const agentRequest = await page.evaluate(() => globalThis.__dubAgentRequests.at(-1));
  assert.equal(agentRequest.agent, 'codex');
  assert.equal(agentRequest.purpose, 'translate');
  assert.equal(agentRequest.sourceLanguage, 'en');
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
  await page.locator('summary').filter({ hasText: 'Translation quality' }).click();
  await page.getByRole('button', { name: 'Cinematic', exact: true }).click();
  await page.getByRole('button', { name: 'Translate All', exact: true }).click();
  await waitForSegmentText(0, 'Hola');
  assert.equal(translations[0].quality, 'cinematic');
  await page
    .locator('summary')
    .filter({ hasText: /^Timing$/ })
    .click();
  await page.getByRole('button', { name: 'Stretch Video', exact: true }).click();
  await page.getByRole('button', { name: 'Consistent', exact: true }).click();
  await page.locator('summary').filter({ hasText: 'Production overrides' }).click();
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
  await page.locator('summary').filter({ hasText: 'Translation quality' }).click();
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
  await page.getByRole('button', { name: 'Translate All', exact: true }).click();
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
  const dubbedPreview = page.getByRole('button', { name: 'Dubbed (es)', exact: true });
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
        .getByRole('switch', { name: 'Burn subtitles into picture (hardsub)', exact: true })
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
  await page.getByRole('button', { name: 'MP3 (compressed)', exact: true }).click();
  await page.getByRole('button', { name: '320 kbps', exact: true }).click();
  await page.getByRole('switch', { name: 'Background audio', exact: true }).click();
  rejectExport = true;
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  await page.getByRole('alert').waitFor();
  assert.equal(await page.getByText('private backend trace').count(), 0);
  rejectExport = false;
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  await page.waitForFunction(() => globalThis.__nativeSaveRequests.length === 1);
  let savedExport = await page.evaluate(() => globalThis.__nativeSaveRequests.at(-1));
  assert.match(savedExport.suggestedName, /-es-mp3\.mp3$/);
  assert.equal(exportRequests.at(-1).searchParams.get('lang'), 'es');
  assert.equal(exportRequests.at(-1).searchParams.get('bitrate'), '320k');
  assert.equal(exportRequests.at(-1).searchParams.get('preserve_bg'), 'false');
  let subtitleRequest;
  await page.route('**/api/dub/srt/fixture?*', (route) => {
    subtitleRequest = new URL(route.request().url());
    return route.fulfill({
      contentType: 'application/x-subrip',
      body: '1\n00:00:00,000 --> 00:00:02,000\nHola',
    });
  });
  await page.getByRole('button', { name: 'SRT', exact: true }).click();
  await page.getByRole('switch', { name: 'Dual (translated + original)', exact: true }).click();
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
      .getByRole('switch', { name: 'Dual (translated + original)', exact: true })
      .getAttribute('aria-checked'),
    'true',
  );
  await page.getByRole('button', { name: 'MP3 (compressed)', exact: true }).click();
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
  await page.evaluate(() => sessionStorage.setItem('voicestudio.test.reset-dub', '1'));
  await page.reload();
  failTranscription = true;
  await page.getByRole('textbox', { name: /paste YouTube/ }).fill('https://example.com/movie.mp4');
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
  assert.deepEqual(errors, []);
  assert.deepEqual(vidstackWarnings, []);
  if (video) assert.equal(mediaHeadRequests, 0);
  if (process.env.VOICESTUDIO_SCREENSHOT)
    await page.screenshot({ path: process.env.VOICESTUDIO_SCREENSHOT });
  console.log(
    'PASS: dub upload, streamed transcription, subtitle import, translate, edit, reload recovery, generate, export readiness and URL import',
  );
} finally {
  await browser.close();
}
