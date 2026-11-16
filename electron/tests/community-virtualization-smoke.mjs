import assert from 'node:assert/strict';
import { launchSmokeBrowser, openMockedPage } from './smoke-api-mock.mjs';
import { DEFAULT_FIXTURES } from './fake-health-backend.mjs';
import { wave } from './test-wave.mjs';

// Run by the offline renderer-smoke inventory. Real Chromium owns focus and media;
// only the catalogue and preview HTTP responses are fixtures.
const voices = Array.from({ length: 1100 }, (_, index) => ({
  id: `fixture/${index}`,
  type: 'voice',
  name: `Fixture voice ${String(index).padStart(4, '0')}`,
  language: 'English',
  facets: {},
  audio: { ref_text: 'A controlled preview. '.repeat(1 + (index % 4)) },
}));
const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const fixtures = Object.fromEntries(
  Object.entries(DEFAULT_FIXTURES).map(([path, value]) => ['/api' + path, value]),
);
const browser = await launchSmokeBrowser();
const errors = [];
const results = [];

async function catalogue() {
  const held = new Set();
  const page = await openMockedPage(browser, {
    viewport: { width: 1440, height: 900 },
    fixtures,
    handle(route, request, path) {
      if (path === '/api/community/items') {
        const offset = Number(new URL(request.url()).searchParams.get('offset'));
        return route.fulfill({
          json: {
            total: voices.length,
            offset,
            limit: 100,
            items: voices.slice(offset, offset + 100),
          },
        });
      }
      if (path.startsWith('/api/community/items/') && path.endsWith('/preview')) {
        // A remains playable while B is loading. B's request deliberately never
        // completes; cancelling its loading button must leave A alone.
        if (!path.includes('fixture%2F0/')) {
          held.add(route);
          return true;
        }
        return route.fulfill({ contentType: 'audio/wav', body: wave });
      }
      return false;
    },
  });
  await page.route('**/*', (route) => {
    const url = route.request().url();
    return url.startsWith(new URL(base).origin + '/') ||
      url.startsWith('data:') ||
      url.startsWith('blob:')
      ? route.fallback()
      : route.abort();
  });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(base + '/#/gallery');
  await page.getByRole('button', { name: 'Community', exact: true }).click();
  await page.getByRole('heading', { name: voices[0].name, exact: true }).waitFor();
  for (let offset = 100; offset < 1000; offset += 100) {
    const loaded = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === '/api/community/items' && url.searchParams.get('offset') === String(offset)
      );
    });
    await page.getByRole('button', { name: 'Load more', exact: true }).click();
    await loaded;
  }
  const scroller = page.locator('section').filter({ has: page.getByRole('searchbox') });
  await scroller.evaluate((element) => {
    element.scrollTop = 0;
  });
  await page.getByRole('heading', { name: voices[0].name, exact: true }).waitFor();
  return { page, scroller, held };
}

function card(page, index) {
  return page.locator('article').filter({
    has: page.getByRole('heading', { name: voices[index].name, exact: true }),
  });
}

async function scrollFar(page, scroller) {
  await scroller.evaluate((element) => {
    element.scrollTop = 30000;
  });
  await page.waitForFunction(() =>
    [...document.querySelectorAll('[aria-posinset]')].some(
      (element) => Number(element.getAttribute('aria-posinset')) > 100,
    ),
  );
}

async function check(name, run) {
  if (process.argv.length > 2 && !process.argv.slice(2).some((filter) => name.includes(filter)))
    return;
  try {
    await run();
    results.push({ name, passed: true });
  } catch (error) {
    results.push({ name, passed: false, error: String(error) });
  }
}

try {
  await check('native Tab and Shift+Tab follow logical adjacent rows', async () => {
    const { page, scroller } = await catalogue();
    try {
      // At this viewport the production gallery has three columns. Use native
      // Tab, without locator.press()/click() refocusing the destination for us.
      const endOfRow = card(page, 2).getByRole('button', { name: 'More actions', exact: true });
      await endOfRow.focus();
      await scrollFar(page, scroller);
      assert(await endOfRow.evaluate((element) => document.activeElement === element));
      await page.keyboard.press('Tab');
      assert.equal(
        await page.evaluate(() => document.activeElement?.getAttribute('aria-label')),
        'Favorites: ' + voices[3].name,
        'Tab skipped the next logical row after scrolling away from the focused row',
      );
      await scrollFar(page, scroller);
      await page.keyboard.press('Shift+Tab');
      assert(
        await endOfRow.evaluate((element) => document.activeElement === element),
        'Shift+Tab skipped the preceding logical row',
      );
    } finally {
      await page.close();
    }
  });

  await check('pending and cancelled replacement preserve the real playing preview', async () => {
    const { page, scroller, held } = await catalogue();
    try {
      await card(page, 0).getByRole('button', { name: 'Preview voice', exact: true }).click();
      await page.locator('audio').waitFor({ state: 'attached' });
      await page.locator('audio').evaluate((audio) => {
        audio.muted = true;
        audio.loop = true;
        window.__galleryOriginalAudio = audio;
      });
      await page.waitForFunction(
        () =>
          !window.__galleryOriginalAudio.paused && window.__galleryOriginalAudio.currentTime > 0,
      );
      await scrollFar(page, scroller);
      const candidate = page
        .locator('article')
        .filter({
          has: page.getByRole('heading', { name: /Fixture voice (?!000)[0-9]{4}$/ }),
        })
        .first();
      const name = await candidate.getByRole('heading').innerText();
      const replacement = page.locator('article').filter({
        has: page.getByRole('heading', { name, exact: true }),
      });
      await replacement.getByRole('button', { name: 'Preview voice', exact: true }).click();
      assert(
        await page.evaluate(
          () => window.__galleryOriginalAudio.isConnected && !window.__galleryOriginalAudio.paused,
        ),
        'Pending B unmounted still-playing A',
      );
      await replacement.getByRole('button', { name: 'Loading… · Cancel', exact: true }).waitFor();
      await replacement.locator('audio').waitFor({ state: 'attached' });
      await replacement.locator('audio').evaluate((audio) => {
        window.__galleryPendingAudio = audio;
      });
      for (const width of [640, 900, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        await page.evaluate(() => new Promise(requestAnimationFrame));
        assert(
          await page.evaluate(
            () =>
              window.__galleryOriginalAudio.isConnected &&
              !window.__galleryOriginalAudio.paused &&
              window.__galleryPendingAudio.isConnected,
          ),
          'Column changes replaced one of the current and pending media owners',
        );
      }
      await replacement.getByRole('button', { name: 'Loading… · Cancel', exact: true }).click();
      await replacement.getByRole('button', { name: 'Preview voice', exact: true }).waitFor();
      assert(
        await page.evaluate(
          () => window.__galleryOriginalAudio.isConnected && !window.__galleryOriginalAudio.paused,
        ),
        'Cancelling B interrupted A',
      );
      const position = await page.evaluate(() => {
        window.__galleryOriginalAudio.pause();
        return window.__galleryOriginalAudio.currentTime;
      });
      await scroller.evaluate((element) => {
        element.scrollTop = 60000;
      });
      for (const width of [640, 900, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        await page.evaluate(() => new Promise(requestAnimationFrame));
        assert(
          await page.evaluate(
            (time) =>
              window.__galleryOriginalAudio.isConnected &&
              window.__galleryOriginalAudio.paused &&
              Math.abs(window.__galleryOriginalAudio.currentTime - time) < 0.05,
            position,
          ),
          'Resize or scrolling replaced the paused media element',
        );
        assert(
          (await page.locator('article').count()) < 70,
          'Retained cards grew with visited rows',
        );
      }
      await page.getByRole('searchbox').fill(voices[999].name);
      await card(page, 999).waitFor();
      assert.equal(await page.locator('article').count(), 1);
      await page.getByRole('searchbox').fill('');
      await page.getByRole('button', { name: 'Load more', exact: true }).click();
      await page
        .getByRole('button', { name: 'Load more', exact: true })
        .waitFor({ state: 'detached' });
      await scroller.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      await card(page, 1099).waitFor();
    } finally {
      for (const route of held) await route.abort().catch(() => {});
      await page.close();
    }
  });
  console.log(JSON.stringify(results));
  assert(results.length > 0, 'No browser regression matched the requested filter');
  assert.deepEqual(errors, [], 'Unexpected renderer errors');
  assert(
    results.every((result) => result.passed),
    'Community virtualization browser regressions failed',
  );
} finally {
  await browser.close();
}
