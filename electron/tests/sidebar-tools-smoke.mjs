import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';

const browser = await chromium.launch({
  ...(process.env.PLAYWRIGHT_BUNDLED === '1' ? {} : { channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' }),
  headless: true,
});
const ui = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
try {
  for (const platform of ['win32', 'darwin', 'linux']) {
    for (const locale of ['en', 'de', 'ar']) {
      const strings = JSON.parse(
        readFileSync(
          new URL(`../src/renderer/src/i18n/locales/${locale}.json`, import.meta.url),
          'utf8',
        ),
      );
      const page = await browser.newPage({ viewport: { width: 960, height: 600 } });
      await page.addInitScript(
        ({ platform, locale }) => {
          localStorage.setItem('voicestudio.setup.complete.v1', '1');
          localStorage.setItem('voicestudio.locale', locale);
          // The expanded navigation is under test, so keep it open beside the library.
          localStorage.setItem(
            'voicestudio.workspace-layout',
            JSON.stringify({ autoCollapseSidebar: false }),
          );
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
      await page.route('**/api/profiles', (route) =>
        route.fulfill({
          json: [
            {
              id: 'sidebar-layout-fixture',
              name: 'Sidebar layout fixture',
              kind: 'clone',
              ref_audio_path: 'fixture.wav',
              created_at: 0,
            },
          ],
        }),
      );
      await page.goto(ui + '/#/clone');
      const sidebar = page.locator('aside').first();
      const footer = sidebar.locator('footer');
      await footer.getByRole('slider', { name: strings.performanceProfile.title, exact: true }).waitFor();
      // The three-way view switch lives in the popover that the speed/quality label opens.
      const chooseView = async (level) => {
        const group = page.getByRole('radiogroup', { name: strings.sidebarTools.title, exact: true });
        if (!(await group.isVisible())) {
          await footer.locator('[data-slot=performance-trigger]').click();
          await group.waitFor();
        }
        await group
          .getByRole('radio', {
            name: level === 'models' ? strings.modelSettings.models : strings.sidebarTools[level],
            exact: true,
          })
          .click();
        const popup = page.locator('[data-slot=popover-content]');
        if (await popup.count()) {
          await page.keyboard.press('Escape');
          await popup.waitFor({ state: 'hidden' });
        }
      };
      assert.equal(
        await footer.locator('[data-slot=engine-row]').count(),
        0,
        'Simple keeps the sidebar bottom to the slider; engines open from its popover',
      );
      await chooseView('models');
      for (const family of ['tts', 'asr', 'llm', 'translation', 'dictation', 'diarisation']) {
        const tool = footer.locator(`a[href$="/settings/models/${family}"]`).first();
        assert.ok((await tool.innerText()).includes(strings.sidebarTools[family]));
        assert.ok(
          (await tool.innerText()).split('\n').length >= 2,
          'Status must be written, not color-only',
        );
        assert.ok((await tool.boundingBox()).height >= 44);
        assert.equal(await tool.evaluate((el) => el.scrollWidth <= el.clientWidth), true);
      }
      const speech = footer.locator('a[href$="/settings/models/tts"]').first();
      await speech.focus();
      assert.ok(await speech.evaluate((el) => el === document.activeElement));
      assert.equal(await page.locator('[data-slot=tooltip-content][data-open]').count(), 0);
      await speech.blur();
      // Speed / quality is a four-notch slider (Fast/Balanced/Quality/Max) with an Auto toggle.
      const tier = footer.getByRole('slider', {
        name: strings.performanceProfile.title,
        exact: true,
      });
      await tier.waitFor();
      assert.ok(await tier.getAttribute('aria-describedby'));
      assert.ok(await tier.getAttribute('aria-valuetext'), 'The selected tier is announced');
      for (const level of ['simple', 'models', 'details']) {
        await chooseView(level);
        if (level === 'simple') {
          assert.equal(await footer.locator('#sidebar-engine-details').count(), 0);
        } else {
          assert.equal(
            await footer.locator('#sidebar-engine-details').getAttribute('data-detail-level'),
            level,
          );
        }
        assert.equal(
          await footer.locator('[data-slot=engine-row]').count(),
          level === 'simple' ? 0 : 6,
          'Views replace one another; engine rows are never duplicated',
        );
        if (level === 'details') {
          const disclosure = footer.locator('[data-slot=engine-row] > button');
          await disclosure.nth(1).click();
          assert.equal(await footer.locator('[data-slot=engine-diagnostics]').count(), 1);
          assert.equal(await disclosure.first().getAttribute('aria-expanded'), 'false');
        }
        const settings = sidebar.locator('a[href$="/settings"]').first();
        const bounds = await settings.boundingBox();
        assert.ok(
          bounds && bounds.y >= 0 && bounds.y + bounds.height <= 600,
          `${platform}/${locale}: Settings must remain on-screen`,
        );
        // The library lives in its own column now, so a tall engine footer can no
        // longer squeeze it; it must stay usable beside the slim navigation.
        const preview = page
          .locator('[data-slot=secondary-sidebar]')
          .getByRole('button', { name: strings.clone.preview_voice, exact: true })
          .first();
        await preview.scrollIntoViewIfNeeded();
        assert.ok(
          await preview.isVisible(),
          `${platform}/${locale}/${level}: Voice preview must stay usable in the library column`,
        );
        assert.ok(
          await footer.evaluate((el) => el.scrollWidth <= el.clientWidth),
          platform +
            '/' +
            locale +
            ': ' +
            JSON.stringify(
              await footer.evaluate((el) => ({
                width: el.clientWidth,
                scroll: el.scrollWidth,
                children: [...el.querySelectorAll('*')]
                  .filter((x) => x.scrollWidth > x.clientWidth && x.clientWidth > 0)
                  .map((x) => [x.tagName, x.className, x.clientWidth, x.scrollWidth]),
              })),
            ),
        );
      }
      await page.reload();
      await footer.locator('[data-detail-level=details]').waitFor();
      if (platform === 'win32' && locale === 'en') {
        await page.setViewportSize({ width: 1280, height: 900 });
        for (const level of ['simple', 'models', 'details']) {
          await chooseView(level);
          await footer.evaluate((el) => {
            el.scrollTop = 0;
          });
          await page.mouse.move(800, 100);
          await page.keyboard.press('Escape');
          await footer.screenshot({
            path: `.tmp-sidebar-tools-${level}.png`,
            animations: 'disabled',
          });
        }
      }
      await chooseView('models');
      await speech.focus();
      await page.keyboard.press('Enter');
      await page.waitForURL('**/#/settings/models/tts');
      await page.close();
    }
  }
  console.log(
    'Three persistent views, no duplicate rows, text status, keyboard navigation, slider descriptions, and short-window layout passed for Windows/macOS/Linux renderer layouts in English/German/Arabic.',
  );
} finally {
  await browser.close();
}
