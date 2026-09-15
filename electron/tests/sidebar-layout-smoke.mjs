import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const browser = await chromium.launch({
  ...(process.env.PLAYWRIGHT_BUNDLED === '1' ? {} : { channel: 'msedge' }),
  headless: true,
});
const page = await browser.newPage();
const out = mkdtempSync(join(tmpdir(), 'voicestudio-sidebar-'));
const ui = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
try {
  await page.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
  await page.goto(ui + '/#/clone');
  const sidebar = page.locator('aside').first();
  const navigation = sidebar.getByRole('navigation', { name: 'Workspaces', exact: true });
  await navigation.waitFor();
  assert.equal(await sidebar.locator('footer a:visible').count(), 6);
  await sidebar.getByRole('link', { name: 'Change engine TTS', exact: true }).hover();
  const engineTooltip = page.locator('[data-slot=tooltip-content]');
  await engineTooltip.waitFor();
  assert.ok((await engineTooltip.innerText()).includes('TTS'));
  await page.mouse.move(800, 100);
  await engineTooltip.waitFor({ state: 'detached' });
  for (const [width, height] of [
    [1280, 720],
    [960, 600],
  ]) {
    await page.setViewportSize({ width, height });
    assert.equal(await navigation.getByRole('link').count(), 9);
    assert.equal(await page.getByRole('menu').count(), 0);
    const bounds = await navigation.boundingBox();
    assert.ok(bounds && bounds.width <= (await sidebar.boundingBox()).width);
    if (height >= 700) {
      assert.ok(
        await navigation.evaluate((el) => el.scrollHeight <= el.clientHeight),
        'Collapsed workspace navigation should fit without scrolling at standard window height',
      );
    }
    await page.screenshot({ path: join(out, 'navigation-' + height + '.png') });
    const summary = sidebar.locator('[aria-controls=sidebar-engine-details]');
    await summary.click();
    await sidebar.locator('#sidebar-engine-details a').nth(5).waitFor();
    assert.equal(await sidebar.locator('#sidebar-engine-details a').count(), 6);
    const modelDetails = sidebar.locator('#sidebar-engine-details a p:nth-of-type(2)');
    assert.equal(await modelDetails.count(), 6);
    assert.ok(await modelDetails.first().isVisible());
    assert.ok(
      await sidebar
        .locator('#sidebar-engine-details')
        .evaluate((el) => el.scrollHeight <= el.clientHeight),
    );
    const footer = await sidebar.getByRole('link', { name: 'Settings', exact: true }).boundingBox();
    assert.ok(
      footer && footer.y + footer.height <= height,
      `Settings footer escaped ${width}x${height}: ${JSON.stringify(footer)}`,
    );
    assert.ok(!(await sidebar.innerText()).includes('\u00c2'));
    await page.screenshot({ path: join(out, 'engines-' + height + '.png') });
    await summary.click();
    await page.screenshot({ path: join(out, 'compact-' + height + '.png') });
  }
  await navigation.getByRole('link', { name: 'Stories', exact: true }).click();
  await page.waitForURL('**/#/stories');
  for (const width of [1920, 960]) {
    await page.setViewportSize({ width, height: 720 });
    await page.goto(ui + '/#/personas');
    const compactMain = page.locator('[data-slot=compact-main-sidebar]');
    await compactMain.waitFor();
    assert.equal(Math.round((await compactMain.boundingBox()).width), 48);
    const openMain = compactMain.getByRole('button', { name: 'Toggle Sidebar' });
    assert.equal(await openMain.locator('.lucide-panel-left-open').count(), 1);
    assert.equal(await openMain.locator('img').count(), 0);
    assert.equal(await page.getByRole('heading', { name: 'Saved voices', exact: true }).count(), 1);
    if (width === 1920) {
      await openMain.click();
      await compactMain.waitFor({ state: 'detached' });
      const expandedMain = page.locator('.brand-sidebar').filter({ hasText: 'Saved voices' });
      await expandedMain.waitFor();
      await expandedMain.getByRole('button', { name: 'Close' }).click();
      await compactMain.waitFor();
    }
  }
  const macPage = await browser.newPage();
  try {
    await macPage.addInitScript(() => {
      localStorage.setItem('voicestudio.setup.complete.v1', '1');
      Object.defineProperty(window, 'voicestudio', {
        value: {
          app: {
            version: 'test',
            platform: 'darwin',
            isDev: true,
            onNavigate: () => () => {},
            onPersistenceFlush: () => () => {},
          },
          repair: {
            list: async () => [],
            getState: async () => ({
              status: 'idle',
              output: '',
              workspaceAvailable: false,
            }),
            onEvent: () => () => {},
          },
        },
      });
    });
    await macPage.goto(ui + '/#/clone');
    const macSidebar = macPage.locator('aside').first();
    const macNotifications = macPage.locator('[data-slot=macos-system-notifications]');
    const macNotificationBounds = await macNotifications.boundingBox();
    assert.ok(
      macNotificationBounds &&
        macNotificationBounds.y < 20 &&
        macNotificationBounds.x + macNotificationBounds.width >=
          (await macPage.evaluate(() => window.innerWidth)) - 20,
      'macOS notifications must sit in the top-right titlebar corner',
    );
    const titlebarActions = await macPage.locator('main .workspace-titlebar button').all();
    const titlebarActionBounds = (
      await Promise.all(titlebarActions.map((action) => action.boundingBox()))
    ).filter(Boolean);
    assert.ok(
      macNotificationBounds &&
        titlebarActionBounds.every(
          (bounds) => bounds.x + bounds.width <= macNotificationBounds.x - 12,
        ),
      'macOS titlebar actions must leave space before notifications',
    );
    await macNotifications.getByRole('button').first().click();
    await macPage.locator('[data-slot=popover-content][data-open]').waitFor({ state: 'visible' });
    await macPage.keyboard.press('Escape');
    const brandLink = macSidebar.getByRole('link', { name: 'VoiceStudio', exact: true });
    const brandBounds = await brandLink.boundingBox();
    assert.ok(brandBounds && brandBounds.x >= 96, 'macOS brand must clear the traffic lights');
    assert.ok(
      await brandLink
        .locator('span')
        .evaluate((element) => element.scrollWidth <= element.clientWidth),
      'macOS titlebar must show the complete VoiceStudio wordmark',
    );
    const expandedSettings = macSidebar.getByRole('link', { name: 'Settings', exact: true });
    const expandedDevice = macSidebar.getByRole('button', { name: /Local device/ });
    const expandedSettingsBounds = await expandedSettings.boundingBox();
    const expandedDeviceBounds = await expandedDevice.boundingBox();
    assert.equal((await expandedSettings.innerText()).trim(), '');
    assert.ok(
      expandedSettingsBounds &&
        expandedDeviceBounds &&
        expandedDeviceBounds.x > expandedSettingsBounds.x,
      'expanded macOS Local device must sit right of icon-only Settings',
    );
    await expandedDevice.click();
    await macPage.locator('[data-slot=popover-content][data-open]').waitFor({ state: 'visible' });
    await macPage.keyboard.press('Escape');
    await macSidebar.getByRole('button', { name: 'Close', exact: true }).click();
    const compactMacSidebar = macPage.locator('[data-slot=compact-main-sidebar]');
    await compactMacSidebar.waitFor();
    assert.equal(Math.round((await compactMacSidebar.boundingBox()).width), 64);
    const compactDividerBounds = await compactMacSidebar
      .locator('[data-slot=compact-sidebar-divider]')
      .boundingBox();
    assert.ok(
      compactDividerBounds && compactDividerBounds.y >= 72,
      'macOS compact-sidebar divider must begin below the titlebar',
    );
    const compactToggleBounds = await compactMacSidebar
      .getByRole('button', { name: 'Toggle Sidebar', exact: true })
      .boundingBox();
    assert.ok(
      compactToggleBounds && compactToggleBounds.y >= 32,
      'macOS compact-sidebar toggle must sit below the traffic lights',
    );
    const compactSettingsBounds = await compactMacSidebar
      .getByRole('link', { name: 'Settings', exact: true })
      .boundingBox();
    const compactDeviceBounds = await compactMacSidebar
      .getByRole('button', { name: /Local device/ })
      .boundingBox();
    assert.ok(
      compactSettingsBounds &&
        compactDeviceBounds &&
        compactDeviceBounds.x > compactSettingsBounds.x &&
        Math.abs(
          compactDeviceBounds.y +
            compactDeviceBounds.height / 2 -
            (compactSettingsBounds.y + compactSettingsBounds.height / 2),
        ) <= 1,
      `macOS Local device must sit to the right of Settings: ${JSON.stringify({ compactSettingsBounds, compactDeviceBounds })}`,
    );
    const workspaceTitleBounds = await macPage
      .getByRole('heading', { name: 'Voice cloning' })
      .boundingBox();
    assert.ok(
      workspaceTitleBounds && workspaceTitleBounds.x >= 88,
      'macOS workspace title must clear the traffic lights',
    );
  } finally {
    await macPage.close();
  }
  console.log(
    'Sidebar compact/expanded, macOS titlebar clearance, 9 destinations, 6 engine links, visible models, non-duplicated Profiles, settings visibility and navigation passed. ' +
      out,
  );
} finally {
  await browser.close();
  if (process.env.VOICESTUDIO_KEEP_SMOKE_ARTIFACTS !== '1') {
    rmSync(out, { recursive: true, force: true });
  }
}
