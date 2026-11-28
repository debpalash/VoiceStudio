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
async function expectTransparentTitlebars(page) {
  const result = await page.waitForFunction(() => {
    const styles = Array.from(document.querySelectorAll('main .workspace-titlebar')).flatMap(
      (element) => {
        const row = getComputedStyle(element);
        if (!row.backgroundColor || element.getBoundingClientRect().height === 0) return [];
        const edge = getComputedStyle(element, '::after');
        return [
          [
            row.backgroundColor,
            row.backgroundImage,
            row.borderBottomWidth,
            row.backdropFilter,
            edge.backdropFilter,
            edge.pointerEvents,
          ],
        ];
      },
    );
    return styles.length > 0 ? styles : false;
  });
  const renderedBars = await result.jsonValue();
  assert.ok(renderedBars, page.url());
  for (const styles of renderedBars)
    assert.deepEqual(
      styles,
      ['rgba(0, 0, 0, 0)', 'none', '0px', 'none', 'blur(5px)', 'none'],
      page.url(),
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
  const touchContext = await browser.newContext({ hasTouch: true, viewport: { width: 1200, height: 800 } });
  try {
    const touchPage = await touchContext.newPage();
    await touchPage.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
    await touchPage.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912') + '/#/design');
    const group = touchPage.getByRole('button', { name: 'Voice', exact: true });
    const destination = touchPage.getByRole('link', { name: 'Clone', exact: true });
    await group.tap();
    await destination.waitFor();
    await group.tap();
    await destination.waitFor({ state: 'hidden', timeout: 3000 });
  } finally {
    await touchContext.close();
  }
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
    await expectTransparentTitlebars(page);
    if (path === '/clone') {
      const chooser = page.locator('[data-slot=voice-chooser]');
      for (const control of [
        chooser.getByRole('button', { name: 'Upload audio', exact: true }),
        chooser.getByRole('button', { name: 'Record', exact: true }),
        chooser.getByLabel('Input device', { exact: true }),
        chooser.getByLabel('Channels', { exact: true }),
      ]) {
        await control.waitFor();
        const box = await control.boundingBox();
        assert.ok(box && box.y >= 0 && box.y + box.height <= page.viewportSize().height);
      }
    }
  }
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912') + '/#/design');
  const rail = page.locator('[data-slot=compact-main-sidebar]');
  await rail.waitFor();
  const sidebarSurface = (sidebar) =>
    sidebar.evaluate((element) => {
      const railStyle = getComputedStyle(element);
      const titleStyle = getComputedStyle(element.querySelector('[data-slot=titlebar-row]'));
      return {
        railColor: railStyle.backgroundColor,
        railImage: railStyle.backgroundImage,
        railBlur: railStyle.backdropFilter,
        railShadow: railStyle.boxShadow,
        railBorder: railStyle.borderRightWidth,
        titleColor: titleStyle.backgroundColor,
        titleImage: titleStyle.backgroundImage,
      };
    });
  const transparentSidebar = {
    railColor: 'rgba(0, 0, 0, 0)',
    railImage: 'none',
    railBlur: 'none',
    railShadow: 'none',
    railBorder: '0px',
    titleColor: 'rgba(0, 0, 0, 0)',
    titleImage: 'none',
  };
  assert.deepEqual(await sidebarSurface(rail), transparentSidebar);
  await page.evaluate(() => {
    document.documentElement.dataset.glass = 'true';
  });
  assert.deepEqual(await sidebarSurface(rail), transparentSidebar);
  await page.evaluate(() => {
    delete document.documentElement.dataset.glass;
  });
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
  const toggle = rail.getByRole('button', { name: 'Toggle sidebar', exact: true });
  await toggle.focus();
  await page.keyboard.press('Enter');
  const close = page.locator('.brand-sidebar').getByRole('button', { name: 'Close', exact: true });
  await expectFocus(page, close);
  await page.keyboard.press('Space');
  await expectFocus(page, toggle);
  await voice.dblclick();
  await rail.waitFor({ state: 'hidden' });
  const expanded = page.locator('.brand-sidebar:not([data-slot=compact-main-sidebar])');
  await expanded.getByRole('button', { name: 'Close', exact: true }).waitFor();
  assert.deepEqual(await sidebarSurface(expanded), transparentSidebar);
  const handle = expanded.getByRole('separator');
  const lineOpacity = () =>
    handle.evaluate((element) => getComputedStyle(element, '::after').opacity);
  assert.equal(await lineOpacity(), '0');
  await handle.hover();
  assert.equal(await lineOpacity(), '0.48');
  const bounds = await handle.boundingBox();
  assert.ok(bounds);
  const width = Number(await handle.getAttribute('aria-valuenow'));
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2 + 24, bounds.y + bounds.height / 2);
  assert.equal(await lineOpacity(), '0.8');
  assert.ok(Number(await handle.getAttribute('aria-valuenow')) > width);
  await page.mouse.up();
  await page.mouse.move(1100, 400);
  assert.equal(await lineOpacity(), '0');
  await handle.focus();
  await page.keyboard.press('ArrowLeft');
  assert.equal(await lineOpacity(), '0.8');
  await page.evaluate(() => {
    document.documentElement.dataset.glass = 'true';
  });
  assert.deepEqual(await sidebarSurface(expanded), transparentSidebar);
  await expectTransparentTitlebars(page);
  await expanded.getByRole('link', { name: 'Settings', exact: true }).click();
  await page.waitForURL('**/#/settings/**');
  await expectTransparentTitlebars(page);
  const settings = page.getByRole('navigation', { name: 'Settings', exact: true });
  await settings
    .getByRole('group', { name: 'System', exact: true })
    .getByRole('link', { name: 'Models', exact: true })
    .waitFor();
  await settings
    .getByRole('group', { name: 'Diagnostics', exact: true })
    .getByRole('link', { name: 'Diagnostics', exact: true })
    .waitFor();
  console.log('PASS: compact flyout hover transit, dismissal, and keyboard access');
  console.log(`PASS: ${routes.length} compact navigation destinations`);
} finally {
  await browser.close();
}
