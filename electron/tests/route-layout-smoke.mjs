import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const routes = [
  '/',
  '/clone',
  '/design',
  '/personas',
  '/gallery',
  '/transcriptions',
  '/calls',
  '/stories',
  '/audiobook',
  '/dub',
  '/batch',
  '/projects',
  '/tools',
  '/integrations',
  '/settings',
  '/settings/general',
  '/settings/appearance',
  '/settings/models',
  ...['tts', 'asr', 'dictation', 'diarisation', 'translation', 'llm'].map(
    (family) => `/settings/models/${family}`,
  ),
  '/settings/performance',
  '/settings/pronunciation',
  '/settings/media',
  '/settings/network',
  '/settings/sharing',
  '/settings/credentials',
  '/settings/workers',
  '/settings/permissions',
  '/settings/privacy',
  '/settings/storage',
  '/settings/usage',
  '/settings/logs',
  '/settings/diagnostics',
  '/settings/openapi',
  '/settings/updates',
  '/settings/support',
];
const viewports = [
  { width: 1440, height: 900 },
  { width: 640, height: 720 },
];

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const failures = [];

page.on('pageerror', (error) => failures.push(`page: ${error.message}`));
page.on('console', (message) => {
  if (message.type() === 'error') failures.push(`console: ${message.text()}`);
});
page.on('requestfailed', (request) => {
  if (!request.url().startsWith(base)) return;
  // Route transitions deliberately abort React Query reads owned by the view
  // being unmounted. Transport failures that were not cancelled still fail.
  if (request.failure()?.errorText === 'net::ERR_ABORTED') return;
  failures.push(`request: ${request.method()} ${request.url()} (${request.failure()?.errorText})`);
});
page.on('response', (response) => {
  if (response.status() < 500 || !response.url().startsWith(base)) return;
  failures.push(`response: ${response.status()} ${response.request().method()} ${response.url()}`);
});

try {
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
  });
  await page.goto(`${base}/#/`, { waitUntil: 'domcontentloaded' });

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const route of routes) {
      const start = failures.length;
      await page.evaluate((path) => {
        window.location.hash = path;
      }, route);
      await page.waitForFunction(
        (path) =>
          path === '/settings'
            ? window.location.hash.startsWith('#/settings/')
            : window.location.hash === `#${path}`,
        route,
      );
      await page.waitForTimeout(120);

      assert.equal(
        await page.getByText('This tab hit a snag.', { exact: true }).count(),
        0,
        `${route} rendered the route recovery view at ${viewport.width}px`,
      );
      const width = await page.evaluate(() => ({
        client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
      }));
      assert.ok(
        width.scroll <= width.client + 1,
        `${route} overflows horizontally at ${viewport.width}px (${width.scroll} > ${width.client})`,
      );
      assert.deepEqual(
        failures.slice(start),
        [],
        `${route} produced runtime failures at ${viewport.width}px`,
      );
    }
  }

  console.log(
    `${routes.length} routes passed at ${viewports.map((item) => item.width).join('/')}px.`,
  );
} finally {
  await context.close();
  await browser.close();
}
