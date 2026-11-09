import { chromium } from 'playwright';

export const BASE_API_FIXTURES = {
  '/api/health': { status: 'ok' },
  '/api/setup/status': { ready: true },
  '/api/models/install/status': { jobs: [] },
};

export function launchSmokeBrowser({ fallbackChannel } = {}) {
  const launchOptions = { headless: true };
  if (process.env.CHROMIUM_PATH) launchOptions.executablePath = process.env.CHROMIUM_PATH;
  else if (fallbackChannel)
    launchOptions.channel = process.env.PLAYWRIGHT_CHANNEL || fallbackChannel;
  return chromium.launch(launchOptions);
}

export async function openMockedPage(browser, { viewport, fixtures = {}, handle } = {}) {
  const page = await browser.newPage(viewport ? { viewport } : {});
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
  });
  const responses = { ...BASE_API_FIXTURES, ...fixtures };
  await page.route('**/api/**', async (route, request) => {
    const path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (handle) {
      const handled = handle(route, request, path);
      if (handled && typeof handled.then === 'function') {
        await handled;
        return;
      }
      if (handled) return;
    }
    if (Object.prototype.hasOwnProperty.call(responses, path)) {
      const value = responses[path];
      return route.fulfill({ json: typeof value === 'function' ? value() : value });
    }
    return route.fulfill({
      status: 404,
      json: { detail: `${path} is not mocked` },
    });
  });
  return page;
}
