import { expect, test } from '@playwright/test';
import { COMPONENTS, THEMES } from './manifest';

// Visual-regression baseline for the CSS → Tailwind v4 migration.
//
// Each presentational leaf component is rendered in isolation by the Vite
// harness (no backend) and snapshotted in every theme. Run with
// `bun run test:visual`; regenerate after an INTENTIONAL visual change with
// `bun run test:visual:update`. This suite is local/manual by design — see
// ./README.md.

for (const component of COMPONENTS) {
  for (const theme of THEMES) {
    test(`${component} — ${theme}`, async ({ page }) => {
      await page.goto(`/src/test/visual/harness.html?component=${component}&theme=${theme}`);

      // Wait for webfonts so we never snapshot a fallback-font frame.
      await page.waitForFunction(
        () => document.documentElement.getAttribute('data-visual-ready') === 'true',
      );

      const root = page.locator('#visual-root');
      await expect(root).toHaveScreenshot(`${component}-${theme}.png`);
    });
  }
}
