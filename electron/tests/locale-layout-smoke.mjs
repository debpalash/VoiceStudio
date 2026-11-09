import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const localeRoot = join(root, 'src', 'renderer', 'src', 'i18n', 'locales');
const locales = [
  'en',
  'zh-CN',
  'es',
  'fr',
  'de',
  'ja',
  'pt',
  'it',
  'ru',
  'ko',
  'hi',
  'tr',
  'pl',
  'nl',
  'sv',
  'th',
  'vi',
  'id',
  'uk',
  'ar',
  'zh-TW',
  'gl',
];
const longStringLocales = ['de', 'fr', 'ru', 'uk', 'hi', 'ar', 'zh-TW'];
const routes = ['settings/general', 'settings/performance', 'settings/models', 'clone', 'dub'];
const baseUrl = process.env.VOICESTUDIO_UI_URL || process.env.VOICESTUDIO_SMOKE_URL || 'http://localhost:3912';

async function catalog(locale) {
  return JSON.parse(await readFile(join(localeRoot, `${locale}.json`), 'utf8'));
}

function assertNoHorizontalDocumentOverflow(measurement, context) {
  assert.ok(
    measurement.scrollWidth <= measurement.innerWidth + 1,
    `${context} overflowed horizontally: ${measurement.scrollWidth}px > ${measurement.innerWidth}px`,
  );
}

const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
try {
  for (const locale of locales) {
    const expected = await catalog(locale);
    const page = await browser.newPage({ viewport: { width: 960, height: 800 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    await page.addInitScript((selectedLocale) => {
      localStorage.setItem('voicestudio.setup.complete.v1', '1');
      localStorage.setItem('voicestudio.locale', selectedLocale);
    }, locale);
    await page.goto(`${baseUrl}/#/settings/general`);
    await page.locator('main h1').waitFor();
    await page.waitForFunction(
      ({ language, heading }) =>
        document.documentElement.lang === language &&
        document.querySelector('main h1')?.textContent?.trim() === heading,
      { language: locale, heading: expected.preferences.general },
    );
    assert.equal(await page.locator('html').getAttribute('dir'), locale === 'ar' ? 'rtl' : 'ltr');
    assertNoHorizontalDocumentOverflow(
      await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth,
      })),
      `${locale} settings/general at 960px`,
    );
    assert.deepEqual(errors, [], `${locale} emitted renderer errors:\n${errors.join('\n')}`);
    await page.close();
  }

  for (const locale of longStringLocales) {
    for (const width of [960, 640]) {
      const page = await browser.newPage({ viewport: { width, height: 800 } });
      const errors = [];
      page.on('pageerror', (error) => errors.push(String(error)));
      page.on('console', (message) => {
        if (message.type() === 'error') errors.push(message.text());
      });
      await page.addInitScript((selectedLocale) => {
        localStorage.setItem('voicestudio.setup.complete.v1', '1');
        localStorage.setItem('voicestudio.locale', selectedLocale);
      }, locale);
      for (const route of routes) {
        await page.goto(`${baseUrl}/#/${route}`);
        await page.locator('main h1').waitFor();
        await page.waitForFunction(
          (language) => document.documentElement.lang === language,
          locale,
        );
        assertNoHorizontalDocumentOverflow(
          await page.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            innerWidth,
          })),
          `${locale} ${route} at ${width}px`,
        );
      }
      assert.deepEqual(
        errors,
        [],
        `${locale} at ${width}px emitted renderer errors:\n${errors.join('\n')}`,
      );
      await page.close();
    }
  }
  console.log('Rendered language switching and representative long-string layouts passed.');
} finally {
  await browser.close();
}
