import { expect, test } from '@playwright/test';
for (const width of [1280, 390]) {
  test(`audiobook manuscript fills its workspace at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/src/test/visual/harness.html?component=AudiobookWorkspace');
    const editor = page.getByRole('textbox', { name: 'Script', exact: true });
    await expect(editor).toBeVisible();
    const box = await editor.boundingBox();
    expect(box!.height).toBeGreaterThan(300);
    expect(await editor.evaluate((el) => getComputedStyle(el).resize)).toBe('none');
    for (const name of ['Voices', 'Book', 'Script']) {
      await page.getByRole('tab', { name: new RegExp(name) }).click();
      await expect(page.getByRole('tabpanel')).toBeVisible();
    }
    await expect(editor).toHaveValue(/Chapter One/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: `/tmp/audiobook-layout-${width}.png`, fullPage: true });
  });
}
