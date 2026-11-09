import { launchSmokeBrowser, openMockedPage } from './smoke-api-mock.mjs';
import assert from 'node:assert/strict';
const browser = await launchSmokeBrowser();
async function expectFocus(page, locator) {
  await page.waitForFunction(
    (element) => element === document.activeElement,
    await locator.elementHandle(),
    { timeout: 3000 },
  );
}
try {
  const page = await openMockedPage(browser, {
    viewport: { width: 1200, height: 800 },
    fixtures: {
      '/api/profiles': [],
      '/api/history': [],
      '/api/projects': [],
      '/api/export/history': [],
      '/api/longform/jobs': { jobs: [] },
    },
  });
  const routes = [
    ['Voice', 'Clone', '/clone'],
    ['Voice', 'Design', '/design'],
    ['Voice', 'Saved', '/personas'],
    ['Voice', 'Gallery', '/gallery'],
    ['Stories', 'Stories', '/stories'],
    ['Stories', 'Audiobook', '/audiobook'],
    ['Dubbing', 'Dubbing', '/dub'],
    ['Dubbing', 'Batch Dubbing', '/batch'],
  ];
  for (const [group, item, path] of routes) {
    await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912') + '/#/design');
    const rail = page.locator('[data-slot=compact-main-sidebar]');
    await rail.waitFor();
    await rail.getByRole('button', { name: group, exact: true }).click();
    const link = page.getByRole('link', { name: item, exact: true });
    await link.waitFor();
    if (process.env.VOICESTUDIO_SCREENSHOT && item === 'Clone')
      await page.screenshot({ path: process.env.VOICESTUDIO_SCREENSHOT, animations: 'disabled' });
    await link.click();
    await page.waitForURL('**/#' + path, { timeout: 3000 });
  }
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912') + '/#/design');
  const rail = page.locator('[data-slot=compact-main-sidebar]');
  await rail.waitFor();
  const voice = rail.getByRole('button', { name: 'Voice', exact: true });
  await page.mouse.move(1100, 400);
  await voice.hover();
  const clone = page.getByRole('link', { name: 'Clone', exact: true });
  await clone.waitFor();
  assert.equal(await voice.getAttribute('aria-expanded'), 'true');
  await clone.hover();
  await page.waitForTimeout(250);
  assert.equal(await clone.isVisible(), true, 'flyout should remain open while moving into it');
  await page.mouse.move(1100, 400);
  await clone.waitFor({ state: 'hidden' });
  await voice.focus();
  await page.keyboard.press('Enter');
  await clone.waitFor();
  await page.keyboard.press('Escape');
  await clone.waitFor({ state: 'hidden' });
  await expectFocus(page, voice);
  await page.keyboard.press('ArrowRight');
  await clone.waitFor();
  await expectFocus(page, clone);
  await page.keyboard.press('ArrowLeft');
  await clone.waitFor({ state: 'hidden' });
  await expectFocus(page, voice);
  await voice.dblclick();
  await rail.waitFor({ state: 'hidden' });
  assert.equal((await page.getByRole('button', { name: 'Close', exact: true }).count()) > 0, true);
  console.log('PASS: compact flyout hover transit, dismissal, and keyboard access');
  console.log(`PASS: ${routes.length} compact navigation destinations`);
} finally {
  await browser.close();
}
