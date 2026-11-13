import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
await page.addInitScript(() => {
  localStorage.setItem('voicestudio.setup.complete.v1', '1');
});
const broken = process.env.VOICESTUDIO_TEST_BROKEN_FALLBACK === '1';
let canonical = 0;
let fallback = 0;
// The listing is mocked so the smoke stays offline (the real one comes from the CDN manifest).
await page.route('**/api/community/items?*', (route) =>
  route.fulfill({
    json: {
      total: 1,
      limit: 60,
      offset: 0,
      items: [
        {
          id: 'feat_00_the_librarian',
          type: 'preset',
          name: 'The Librarian',
          icon: '📚',
          use_case: 'narration',
          facets: {},
          instruct: 'Calm, precise narration',
          language: 'English',
          is_community: true,
        },
      ],
    },
  }),
);
await page.route('**/api/community/items/feat_00_the_librarian/preview*', async (route) => {
  const local = new URL(route.request().url()).searchParams.get('local') === 'true';
  // Count loads, not reads: a playing media element may fetch further byte ranges of the same
  // clip (slower CI runners do), but every new load starts at byte 0.
  if (!/^bytes=[1-9]/.test(route.request().headers().range ?? '')) {
    if (local) fallback++;
    else canonical++;
  }
  const audio =
    local && !broken
      ? readFileSync(
          new URL('../../backend/assets/samples/dictation/en_conversational.wav', import.meta.url),
        )
      : Buffer.from('invalid cached audio');
  // Like the real backend, honour byte ranges: a media element seeks with them, and a server
  // that ignores Range makes the player treat the clip as unseekable and pause it.
  const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range ?? '');
  if (range) {
    const start = Number(range[1]);
    const end = range[2] ? Math.min(Number(range[2]), audio.length - 1) : audio.length - 1;
    await route.fulfill({
      status: 206,
      contentType: 'audio/wav',
      headers: {
        'accept-ranges': 'bytes',
        'content-range': `bytes ${start}-${end}/${audio.length}`,
      },
      body: audio.subarray(start, end + 1),
    });
    return;
  }
  await route.fulfill({
    status: 200,
    contentType: 'audio/wav',
    headers: { 'accept-ranges': 'bytes' },
    body: audio,
  });
});
try {
  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
  await page.goto(base + '/#/gallery');
  await page.getByRole('button', { name: 'Community', exact: true }).click();
  const card = page
    .locator('article')
    .filter({ has: page.getByRole('heading', { name: 'The Librarian', exact: true }) });
  await card.getByRole('button', { name: 'Preview voice', exact: true }).click();
  await card
    .getByRole('button', { name: broken ? 'Playback unavailable · Retry' : 'Pause', exact: true })
    .waitFor({ timeout: 5000 });
  assert.equal(canonical, 1);
  assert.equal(fallback, 1);
  if (!broken) await card.getByRole('button', { name: 'Pause', exact: true }).click();
  console.log(
    broken
      ? 'Failed local preview stops without a retry loop.'
      : 'Malformed gallery audio recovers once through local preview.',
  );
} finally {
  await browser.close();
}
