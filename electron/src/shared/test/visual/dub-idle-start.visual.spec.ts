import { expect, test } from '@playwright/test';

const START_CONTROLS = [
  '.dub-start-card',
  '.dub-start-drop',
  '.dub-start-choose',
  '.dub-start-url',
  '.dub-start-url input',
  '.dub-start-url button',
  '.dub-start-languages',
  '.dub-start-languages label',
  '.dub-start-languages button',
  '.dub-start-advanced',
];

const ADVANCED_CONTROLS = [
  '.dub-start-options',
  '.dub-start-caption-option',
  '.dub-start-cookie-option',
  '.dub-start-generation-options',
  '.dub-start-generation-options input',
];

async function expectControlsInsideViewport(page, selectors: string[]) {
  const measurements = await page.locator(selectors.join(',')).evaluateAll((elements) =>
    elements.map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        selector: element.className || element.tagName,
        left: rect.left,
        right: rect.right,
        width: rect.width,
      };
    }),
  );

  expect(measurements.length).toBeGreaterThanOrEqual(selectors.length);
  for (const control of measurements) {
    expect(control.width, `${control.selector} has no rendered width`).toBeGreaterThan(0);
    expect(control.left, `${control.selector} is clipped on the left`).toBeGreaterThanOrEqual(-1);
    expect(control.right, `${control.selector} is clipped on the right`).toBeLessThanOrEqual(
      (await page.viewportSize())!.width + 1,
    );
  }
}

for (const width of [1280, 390]) {
  test(`dubbing start screen keeps import controls usable at ${width}px`, async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/src/test/visual/harness.html?component=DubIdleStart');
    await page.waitForFunction(
      () => document.documentElement.getAttribute('data-visual-ready') === 'true',
    );

    const start = page.locator('.dub-start-screen');
    const card = page.locator('.dub-start-card');
    await expect(start).toBeVisible();
    await expect(page.getByText('Video Dubbing Studio', { exact: true })).toHaveCount(0);
    await expect(card).toBeVisible();
    await expect(page.getByRole('group', { name: 'Drop video or audio here' })).toBeVisible();
    await expect(page.getByRole('button', { name: /Upload/ })).toBeVisible();
    await expect(
      page.getByRole('textbox', { name: '…or paste YouTube / video URL' }),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Spoken language' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Dub into' })).toBeVisible();

    const source = page.getByRole('button', { name: 'Spoken language' });
    await source.click();
    const menu = page.getByRole('listbox');
    await expect(menu).toBeVisible();
    await menu.locator('input').fill('Thai');
    await menu.locator('input').press('Enter');
    await expect(source).toContainText('Thai');
    await expect(menu).toHaveCount(0);

    const advanced = page.getByRole('button', { name: 'Advanced' });
    await expect(advanced).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#dub-start-advanced-options')).toBeHidden();
    await expectControlsInsideViewport(page, START_CONTROLS);
    await expect
      .poll(() => card.evaluate((element) => element.scrollWidth <= element.clientWidth + 1))
      .toBe(true);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.screenshot({
      path: `/tmp/dub-idle-start-${width}-default.png`,
      fullPage: true,
      animations: 'disabled',
    });

    await advanced.click();
    await expect(advanced).toHaveAttribute('aria-expanded', 'true');
    const options = page.locator('#dub-start-advanced-options');
    await expect(options).toBeVisible();
    await expect(page.getByRole('checkbox', { name: /Pull YouTube captions/ })).toBeVisible();
    await expect(page.getByLabel('Choose a cookies.txt export')).toBeVisible();
    await expect(page.getByRole('spinbutton', { name: 'Speakers' })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Voice style' })).toBeVisible();
    await options.scrollIntoViewIfNeeded();
    await expectControlsInsideViewport(page, [...START_CONTROLS, ...ADVANCED_CONTROLS]);
    await expect
      .poll(() => options.evaluate((element) => element.scrollWidth <= element.clientWidth + 1))
      .toBe(true);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    expect(pageErrors).toEqual([]);
    await page.screenshot({
      path: `/tmp/dub-idle-start-${width}-advanced.png`,
      fullPage: true,
      animations: 'disabled',
    });
  });
}
