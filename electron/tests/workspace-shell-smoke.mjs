import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const baseUrl = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(message.text());
});
try {
  // The GitHub star badge fetches a public count; keep the smoke offline and deterministic.
  await page.route('https://api.github.com/**', (route) =>
    route.fulfill({ json: { stargazers_count: 1 } }),
  );
  await page.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
  await page.goto(baseUrl + '/#/stories');
  const compactSidebar = page.locator('[data-slot=compact-main-sidebar]');
  await compactSidebar.waitFor();
  const nav = compactSidebar.getByRole('navigation', { name: 'Workspaces', exact: true });
  await nav.waitFor();
  await nav.evaluate((element) => {
    window.originalWorkspaceNavigation = element;
  });
  for (const path of [
    '/stories',
    '/audiobook',
    '/projects',
    '/tools',
    '/batch',
    '/gallery',
    '/dub',
    '/design',
    '/transcriptions',
  ]) {
    await page.goto(baseUrl + '/#' + path);
    await page.waitForURL(`**/#${path}`);
    assert.equal(await nav.count(), 1, `Navigation disappeared on ${path}`);
    assert.ok(
      await nav.evaluate((el) => el === window.originalWorkspaceNavigation),
      `Sidebar remounted on ${path}`,
    );
    const bounds = await compactSidebar.boundingBox();
    assert.equal(Math.round(bounds.x), 0);
    assert.equal(Math.round(bounds.width), 48);
    assert.equal(await nav.locator('[aria-current=page], [data-active=true]').count(), 1);
    assert.equal(await page.locator('main h1').count(), 1);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  await nav.getByRole('button', { name: 'Voice', exact: true }).click();
  await page.getByRole('link', { name: 'Clone', exact: true }).click();
  await page.waitForURL('**/#/clone');
  // Clone brings its library as a context pane, so navigation stays a rail beside it.
  await page.getByRole('tab', { name: 'Saved voices', exact: true }).waitFor();
  assert.equal(await compactSidebar.count(), 1);
  // One click on the rail's toggle opens the full navigation.
  await compactSidebar.getByRole('button', { name: 'Toggle sidebar' }).click();
  await compactSidebar.waitFor({ state: 'detached' });
  const expandedSidebar = page.locator('.brand-sidebar:not([data-slot=compact-main-sidebar])');
  await expandedSidebar.waitFor();
  const expandedWidth = (await expandedSidebar.boundingBox()).width;
  assert.ok(expandedWidth >= 196 && expandedWidth <= 288, `Expanded navigation width ${expandedWidth}`);
  assert.equal(
    await expandedSidebar.getByRole('navigation', { name: 'Workspaces', exact: true }).count(),
    1,
  );
  assert.deepEqual(errors, [], `Workspace routes emitted renderer errors:\n${errors.join('\n')}`);
  console.log(
    'Shared compact rail stays mounted across all nine secondary workspaces; Clone keeps it beside the library and one click opens the full navigation.',
  );
} finally {
  await browser.close();
}
