import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// #2940: long localized step labels (ru "Конфиденциальность") overflowed the first-run
// sidebar into the content, and the header's Advanced button was clipped at the window edge.
const localeRoot = join(
  dirname(fileURLToPath(import.meta.url)),
  '..',
  'src',
  'renderer',
  'src',
  'i18n',
  'locales',
);
const locales = ['de', 'ru', 'ja', 'uk', 'fr'];
const viewports = [
  { width: 1117, height: 711 },
  { width: 800, height: 700 },
  { width: 420, height: 800 },
];
const baseUrl = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';

const browser = await chromium.launch({
  ...(process.env.CHROME_PATH
    ? { executablePath: process.env.CHROME_PATH }
    : { channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' }),
  headless: true,
});
try {
  for (const locale of locales) {
    const catalog = JSON.parse(await readFile(join(localeRoot, `${locale}.json`), 'utf8'));
    const page = await browser.newPage({ viewport: viewports[0] });
    await page.route('**/api/setup/status', (route) =>
      route.fulfill({ json: { models_ready: false, missing: [] } }),
    );
    await page.route('**/api/setup/preflight', (route) => route.fulfill({ json: { ok: false } }));
    await page.addInitScript(
      (selected) => localStorage.setItem('voicestudio.locale', selected),
      locale,
    );
    await page.goto(`${baseUrl}/#/clone`);
    const privacy = page.locator('nav button[title]', { hasText: catalog.settings.privacy });
    await privacy.waitFor();
    assert.equal(await privacy.getAttribute('title'), catalog.settings.privacy);

    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      const result = await page.evaluate(() => {
        const nav = document.querySelector('nav');
        const navBox = nav.getBoundingClientRect();
        const labels = [...nav.querySelectorAll('[data-setup-step-label]')].map((label) => {
          const box = label.getBoundingClientRect();
          const button = label.closest('button');
          return {
            text: label.textContent,
            inside: box.left >= navBox.left - 1 && box.right <= navBox.right + 1,
            fits:
              label.scrollWidth <= label.clientWidth + 1 &&
              button.scrollWidth <= button.clientWidth + 1,
          };
        });
        const advanced = document
          .querySelector('main button[aria-pressed]')
          .getBoundingClientRect();
        const main = document.querySelector('main').getBoundingClientRect();
        return {
          labels,
          advancedInside: advanced.left >= main.left - 1 && advanced.right <= main.right + 1,
          scrollWidth: document.documentElement.scrollWidth,
          innerWidth,
        };
      });
      const context = `${locale} at ${viewport.width}px`;
      assert.equal(result.labels.length, 4, context);
      for (const label of result.labels) {
        assert.ok(
          label.inside && label.fits,
          `${context}: step label "${label.text}" overflowed the sidebar`,
        );
      }
      assert.ok(result.advancedInside, `${context}: the Advanced button was clipped`);
      assert.ok(
        result.scrollWidth <= result.innerWidth + 1,
        `${context}: page overflowed horizontally`,
      );
    }
    await page.close();
  }
  console.log(`Setup sidebar labels and header actions fit in ${locales.join(', ')}.`);
} finally {
  await browser.close();
}
