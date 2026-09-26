import { expect, test } from '@playwright/test';

for (const { width, shellMini } of [
  { width: 1280, shellMini: false },
  { width: 390, shellMini: false },
  { width: 1280, shellMini: true },
]) {
  const scenario = shellMini ? `${width}px shell-mini` : `${width}px`;
  test(`Stories tabs stay usable without horizontal overflow at ${scenario}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/src/test/visual/harness.html?component=StoriesWorkspaceLayout');
    await page.waitForFunction(
      () => document.documentElement.getAttribute('data-visual-ready') === 'true',
    );
    if (shellMini) {
      await page.locator('#visual-root').evaluate((root) => root.classList.add('shell-mini'));
    }

    const tabs = ['Script', 'Cast', 'Export', 'Projects'];
    await expect(page.getByRole('tab', { name: 'Script' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    for (const tab of tabs) {
      const trigger = page.getByRole('tab', { name: tab });
      await trigger.click();
      await expect(trigger).toHaveAttribute('aria-selected', 'true');
      await expect(page.getByRole('tabpanel', { name: tab })).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      const suffix = shellMini ? `${width}-shell-mini` : String(width);
      await page.screenshot({
        path: `/tmp/stories-tabs-${suffix}-${tab.toLowerCase()}.png`,
        animations: 'disabled',
      });
    }
  });
}
