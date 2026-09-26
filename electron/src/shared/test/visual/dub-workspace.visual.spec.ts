import { expect, test } from '@playwright/test';
import { waveformFixture } from './waveformFixture.js';

for (const width of [1920, 1280, 900, 390]) {
  test(`dubbing workspace uses readable panes and reachable settings at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const audio = waveformFixture();
    await page.route('**/workspace-fixture.wav', (route) =>
      route.fulfill({ contentType: 'audio/wav', body: audio }),
    );
    await page.route('**/dub/audio/workspace-fixture', (route) =>
      route.fulfill({ contentType: 'audio/wav', body: audio }),
    );
    await page.goto('/src/test/visual/harness.html?component=DubWorkspaceLayout');
    const output = page.getByRole('button', { name: 'Output Options:' });
    await expect(output).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('.segment-row').first()).toBeVisible();
    await expect
      .poll(() =>
        page.locator('.dub-panel-right').evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
      )
      .toBe(true);
    const panes = await page.locator('.dub-panel-col').evaluateAll((items) =>
      items.map((el) => {
        const { x, y, width, height } = el.getBoundingClientRect();
        return { x, y, width, height };
      }),
    );
    if (width >= 1280) {
      expect(panes[1].x).toBeGreaterThan(panes[0].x + panes[0].width);
      expect(panes[1].y).toBe(panes[0].y);
      const editorHeight = await page
        .locator('.dub-segment-table__body')
        .evaluate((el) => el.clientHeight);
      expect(editorHeight).toBeGreaterThan(450);
    } else expect(panes[1].y).toBeGreaterThanOrEqual(panes[0].y + panes[0].height - 1);
    await page.screenshot({ path: `/tmp/dub-workspace-${width}.png`, fullPage: true });
    if (width < 1280) {
      await output.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `/tmp/dub-workspace-editor-${width}.png` });
    }
    await output.click();
    await expect(page.getByRole('radio', { name: 'Smart Fit', exact: true })).toBeVisible();
    await page.getByRole('radio', { name: 'Smart Fit', exact: true }).click();
    await output.click();
    await expect(output).toContainText('Smart Fit');
    await page.getByRole('button', { name: 'Transcript', exact: true }).click();
    await expect(page.locator('.dub-transcript-body')).toBeVisible();
    const collapse = page.getByRole('button', { name: 'Edit translation settings' });
    await collapse.click();
    await expect(page.locator('.dub-translation-summary')).toBeVisible();
    await page.getByRole('button', { name: 'Edit translation settings' }).click();
    await expect(page.locator('.dub-translation-fields')).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });
}
