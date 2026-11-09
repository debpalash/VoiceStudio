import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
});
const ui = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3927';
const timings = [];
try {
  for (const [platform, locale, width, columns] of [
    ['win32', 'en', 1200, 3],
    ['darwin', 'de', 620, 2],
    ['linux', 'ar', 390, 1],
  ]) {
    const strings = JSON.parse(
      readFileSync(
        new URL(`../src/renderer/src/i18n/locales/${locale}.json`, import.meta.url),
        'utf8',
      ),
    );
    const page = await browser.newPage({ viewport: { width, height: 800 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.addInitScript(
      ({ platform, locale }) => {
        localStorage.setItem('voicestudio.setup.complete.v1', '1');
        localStorage.setItem('voicestudio.locale', locale);
        window.voicestudio = {
          app: {
            version: 'test',
            platform,
            isDev: true,
            onNavigate: () => () => {},
            onPersistenceFlush: () => () => {},
          },
          repair: {
            list: async () => [],
            getState: async () => ({ status: 'idle', output: '', workspaceAvailable: false }),
            onEvent: () => () => {},
          },
        };
      },
      { platform, locale },
    );
    await page.route('**/api/engines', (route) =>
      route.fulfill({
        json: {
          tts: {
            active: 'fixture',
            active_model: 'Multilingual model',
            backends: [
              {
                id: 'fixture',
                display_name: 'OmniVoice',
                available: true,
                supported_language_names: [
                  'english',
                  'german',
                  'french',
                  'japanese',
                  'spanish',
                  'italian',
                  'portuguese (brazil)',
                  'portuguese (portugal)',
                  'korean',
                ],
              },
            ],
          },
        },
      }),
    );
    await page.route('**/api/setup/status', (route) =>
      route.fulfill({ json: { models_ready: true, missing: [] } }),
    );
    await page.route('**/api/profiles', (route) =>
      route.fulfill({
        json: [
          {
            id: 'language-fixture',
            name: 'Language fixture',
            kind: 'clone',
            ref_audio_path: 'fixture.wav',
            created_at: 0,
          },
        ],
      }),
    );
    await page.goto(ui + '/#/clone');
    await page.getByRole('button', { name: 'Language fixture', exact: true }).first().click();
    const trigger = page.getByRole('button', { name: strings.clone.language, exact: true }).first();
    await trigger.waitFor();
    const openMs = await trigger.evaluate(async (element) => {
      const start = performance.now();
      element.click();
      await new Promise(requestAnimationFrame);
      return Math.round(performance.now() - start);
    });
    const menu = page.locator('[data-slot=language-menu]');
    await menu.waitFor();
    const input = menu.getByRole('combobox');
    await input.focus();
    assert.equal(await menu.getByRole('listbox').getAttribute('data-columns'), String(columns));
    // Unsupported languages start collapsed behind a single row; supported ones lead.
    assert.equal(
      await menu.locator('[role=option][aria-disabled=true]').count(),
      0,
      'unsupported languages start collapsed',
    );
    const showAll = menu.getByRole('option', { name: strings.languagePicker.showAll, exact: true });
    // The row ends the supported list, so narrow one-column layouts virtualize it out of view.
    await menu.getByRole('listbox').evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await showAll.click();
    await menu
      .getByRole('option', { name: strings.languagePicker.hideUnsupported, exact: true })
      .waitFor();
    assert.ok(await menu.locator('[role=option][aria-disabled=true]').count(), 'expands');
    assert.equal(await menu.isVisible(), true, 'expanding keeps the menu open');
    await input.focus();
    const count = await menu.getByRole('option').count();
    assert.ok(count > 0 && count < 80, `virtualized: ${count} mounted options`);
    assert.equal(
      await menu
        .locator('[data-language-flag]')
        .evaluateAll((flags) =>
          flags.every(
            (flag) =>
              flag.getBoundingClientRect().width <= 24 && flag.getBoundingClientRect().height <= 24,
          ),
        ),
      true,
      'flags stay compact',
    );
    const bounds = await menu.boundingBox();
    assert.ok(
      bounds.x >= 0 &&
        bounds.x + bounds.width <= width &&
        bounds.y >= 0 &&
        bounds.y + bounds.height <= 800,
    );
    assert.equal(
      await menu.evaluate((element) => element.scrollWidth <= element.clientWidth),
      true,
    );
    // Measure the input event through the next painted frame, excluding automation round-trips.
    const latency = await input.evaluate(async (element) => {
      const start = performance.now();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(
        element,
        'Deutsch',
      );
      element.dispatchEvent(new Event('input', { bubbles: true }));
      await new Promise(requestAnimationFrame);
      return performance.now() - start;
    });
    timings.push({ platform, locale, openMs, filterMs: Math.round(latency) });
    await page.waitForFunction(
      () => document.querySelectorAll('[data-slot=language-menu] [role=option]').length === 1,
    );
    assert.equal(await menu.getByRole('option').getAttribute('aria-disabled'), 'false');
    await input.press('Enter');
    await menu.waitFor({ state: 'hidden' });
    await page.waitForFunction(
      (element) => element === document.activeElement,
      await trigger.elementHandle(),
    );
    await trigger.click();
    await input.fill('zu');
    const unavailable = menu.getByRole('option').first();
    await unavailable.waitFor();
    assert.equal(await unavailable.getAttribute('aria-disabled'), 'true');
    await input.press('Enter');
    assert.equal(await menu.isVisible(), true);
    await input.fill('ja');
    await input.press('Enter');
    await trigger.click();
    await menu.getByRole('listbox').evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    const activeId = await input.getAttribute('aria-activedescendant');
    assert.ok(
      await page.locator(`[id="${activeId}"]`).count(),
      'active option stays mounted when scrolled away',
    );
    await input.fill('');
    await input.press('Escape');
    await page.waitForFunction(
      (element) => element === document.activeElement,
      await trigger.elementHandle(),
    );
    if (locale === 'en' && process.env.UPDATE_LANGUAGE_SCREENSHOT) {
      await trigger.click();
      await menu.screenshot({
        path: fileURLToPath(new URL('../../docs/assets/language-picker.png', import.meta.url)),
        animations: 'disabled',
      });
    }
    if (!(await menu.isVisible())) await trigger.click();
    await input.press('Tab');
    const modelLink = menu.locator('a[href="#/settings/models/tts"]');
    await page.waitForFunction(
      (element) => element === document.activeElement,
      await modelLink.elementHandle(),
    );
    await modelLink.press('Tab');
    await menu.waitFor({ state: 'hidden' });
    assert.equal(
      await trigger.evaluate((element) => element === document.activeElement),
      false,
      'Tab leaves without trapping focus',
    );
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log(JSON.stringify({ passed: true, timings }));
} finally {
  await browser.close();
}
