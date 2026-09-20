import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({
  ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  headless: true,
});
try {
  const page = await browser.newPage({ bypassCSP: true });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    (route) => {
      const path = new URL(route.request().url()).pathname;
      const fixtures = {
        '/api/health': { status: 'ok' },
        '/api/setup/status': { ready: true },
        '/api/models/install/status': { jobs: [] },
        '/api/workers/target': {
          target: 'local',
          op: 'tts',
          active: { remote: false, label: 'Local', reason: '' },
          targets: [],
          remote_operations: [],
        },
        '/api/profiles': [],
      };
      return path in fixtures
        ? route.fulfill({ json: fixtures[path] })
        : route.fulfill({ status: 404, json: { detail: 'Not mocked' } });
    },
  );
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912') + '/#/stories');
  await page.locator('[data-slot=generate-panel]').waitFor({ timeout: 30000 });
  await page.evaluate(async () => {
    const { longformSession } = await import('/src/features/longform/longform-session.ts');
    longformSession.setState((state) => ({
      ...state,
      active: 'stories',
      stage: 'rendering',
      total: 100,
      drafts: {
        ...state.drafts,
        stories: {
          ...state.drafts.stories,
          lines: Array.from({ length: 50 }, (_, i) => ({
            id: String(i),
            text: `Line ${i + 1}`,
            profileId: null,
          })),
        },
      },
      chapters: Array.from({ length: 100 }, (_, i) => ({
        title: `Chapter ${i + 1}`,
        status: 'pending',
      })),
    }));
  });
  for (const width of [1400, 640]) {
    await page.setViewportSize({ width, height: 720 });
    await page.waitForTimeout(200);
    const button = page.getByRole('button', { name: 'Stop', exact: true });
    const box = await button.boundingBox();
    const sidebar = await page.locator('[data-slot=secondary-sidebar]').boundingBox();
    assert(
      box && box.y >= sidebar.y && box.y + box.height <= sidebar.y + sidebar.height + 1,
      `Stop escapes the setup pane at ${width}px`,
    );
    await page.locator('[data-slot=secondary-sidebar-header] button').click();
    const editor = page
      .locator('[data-slot=secondary-sidebar]')
      .locator('xpath=following-sibling::section');
    for (const atEnd of [false, true]) {
      await editor.evaluate((element, end) => {
        element.scrollTop = end ? element.scrollHeight : 0;
      }, atEnd);
      const collapsedBox = await button.boundingBox();
      const editorBox = await editor.boundingBox();
      assert(
        collapsedBox &&
          collapsedBox.y >= editorBox.y &&
          collapsedBox.y + collapsedBox.height <= editorBox.y + editorBox.height + 1,
        `Stop is unreachable with collapsed setup at ${width}px (end=${atEnd})`,
      );
    }
    await page.locator('[data-slot=secondary-sidebar-header] button').click();
  }
  assert.deepEqual(errors, []);
  console.log('Longform Stop stays inside the setup pane at desktop and compact widths.');
} finally {
  await browser.close();
}
