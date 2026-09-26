import { expect, test } from '@playwright/test';

for (const width of [1280, 390]) {
  test(`Projects list stays stable on hover at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/src/test/visual/harness.html?component=DubIdleWorkspace');
    await page.waitForFunction(
      () => document.documentElement.getAttribute('data-visual-ready') === 'true',
    );
    if (width === 390)
      await page.locator('#visual-root').evaluate((root) => root.classList.add('shell-mini'));
    const library = page.locator('.dub-project-library');
    await expect(library.getByText('Projects', { exact: true })).toBeVisible();
    await expect(page.getByRole('tab')).toHaveCount(0);
    await expect(library.getByText('Documentary final.mp4')).toBeVisible();
    const cards = library.locator('.history-item');
    await cards.first().scrollIntoViewIfNeeded();
    const before = await cards.evaluateAll((nodes) =>
      nodes.map((n) => {
        const r = n.getBoundingClientRect();
        return { y: r.y, height: r.height };
      }),
    );
    const title = cards.first().locator('.history-title');
    const titleHit = await title.evaluate((el) => {
      const r = el.getBoundingClientRect();
      return document
        .elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
        ?.closest('button')
        ?.getAttribute('aria-label');
    });
    expect(titleHit).toBe('Open: Documentary final.mp4');
    await cards.first().hover();
    await expect(cards.first().getByRole('button', { name: 'Open', exact: true })).toBeVisible();
    await page.waitForTimeout(250);
    const after = await cards.evaluateAll((nodes) =>
      nodes.map((n) => {
        const r = n.getBoundingClientRect();
        return { y: r.y, height: r.height };
      }),
    );
    expect(after).toEqual(before);
    await cards.first().getByRole('button', { name: 'Open', exact: true }).focus();
    await expect(cards.first().getByRole('button', { name: 'Open', exact: true })).toBeFocused();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.screenshot({ path: `/tmp/dub-projects-${width}.png`, fullPage: true });
  });
}
