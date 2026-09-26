import { expect, test } from '@playwright/test';

for (const width of [620, 360]) {
  test(`bulk segment menus fit and escape clipping at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 850 });
    await page.goto('/src/test/visual/harness.html?component=DubSelectionLayout');
    const toolbar = page.locator('.dub-selection-toolbar');
    await expect(toolbar).toBeVisible();
    expect(await toolbar.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/dub-selection-${width}.png` });
    await page.getByRole('button', { name: 'Set voice…' }).click();
    const menu = page.getByRole('listbox');
    await expect(menu.getByRole('option', { name: 'VoiceStudio Demo Voice' })).toBeVisible();
    expect(await menu.evaluate((el) => el.parentElement === document.body)).toBe(true);
    await page.screenshot({ path: `/tmp/dub-selection-voices-${width}.png` });
    await menu.getByRole('textbox').fill('Speaker 2');
    await expect(menu.getByRole('option')).toHaveCount(1);
    await menu.getByRole('textbox').press('Enter');
    await expect(menu).toHaveCount(0);
    await page.getByRole('button', { name: 'Set lang…' }).click();
    await menu.getByRole('textbox').fill('Bengali');
    await expect(menu.getByRole('option', { name: /Bengali/ })).toBeVisible();
    const bounds = (await menu.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    await menu.getByRole('textbox').press('Escape');
    await expect(menu).toHaveCount(0);
  });
}

for (const width of [1000, 620, 360]) {
  test(`dub controls fit their rows at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/src/test/visual/harness.html?component=DubSegmentLayout');
    await expect(page.locator('.segment-row').first()).toBeVisible();
    const violations = () =>
      page.locator('.segment-row').evaluateAll((rows) => {
        const errors: string[] = [];
        const overlaps = (a: DOMRect, b: DOMRect) =>
          a.left < b.right - 1 &&
          a.right > b.left + 1 &&
          a.top < b.bottom - 1 &&
          a.bottom > b.top + 1;
        rows.slice(0, 3).forEach((row, i) => {
          const bounds = row.getBoundingClientRect();
          const controls = [...row.querySelectorAll('input, textarea, select, button')].filter(
            (el) => (el as HTMLElement).offsetWidth > 0,
          );
          controls.forEach((control, j) => {
            const rect = control.getBoundingClientRect();
            if (
              rect.left < bounds.left ||
              rect.right > bounds.right + 1 ||
              rect.bottom > bounds.bottom + 1
            )
              errors.push(`row ${i}: ${control.className} escapes row`);
            controls.slice(j + 1).forEach((other) => {
              if (overlaps(rect, other.getBoundingClientRect()))
                errors.push(`row ${i}: ${control.className} overlaps ${other.className}`);
            });
          });
          const time = row.querySelector('.segment-time')!;
          if (time.scrollWidth > time.clientWidth + 1 || time.scrollHeight > time.clientHeight + 1)
            errors.push(`row ${i}: timing clipped`);
          if (i > 0 && bounds.top < rows[i - 1].getBoundingClientRect().bottom - 1)
            errors.push(`row ${i}: overlaps previous row`);
        });
        return errors;
      });
    await expect.poll(violations).toEqual([]);
    await page.screenshot({ path: `/tmp/dub-segments-${width}.png` });
    // Resizing must update virtual offsets as rows gain or lose wrapped lines.
    await page.setViewportSize({ width: width === 360 ? 1000 : 360, height: 900 });
    await expect.poll(violations).toEqual([]);
    await page.getByRole('list').evaluate((el) => {
      el.scrollTop = 5000;
    });
    await expect.poll(violations).toEqual([]);
  });
}

test('waveform drag pans both ways without seeking and keeps the transcript aligned', async ({
  page,
}) => {
  await page.setViewportSize({ width: 620, height: 900 });
  await page.goto('/src/test/visual/harness.html?component=DubWaveformPan');
  const waveform = page.locator('.waveform-container [part="wrapper"]').first();
  const scrollLeft = () => waveform.evaluate((el) => el.parentElement!.scrollLeft);
  const time = () => page.locator('audio').evaluate((el) => (el as HTMLAudioElement).currentTime);
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeEnabled();
  await expect.poll(() => waveform.evaluate((el) => el.scrollWidth)).toBeGreaterThan(1000);
  const bounds = await page.locator('.waveform-container').boundingBox();
  const x = bounds!.x + 350;
  const y = bounds!.y + 30;
  const initialScroll = await scrollLeft();
  const initialTime = await time();
  const segment = page.getByRole('option').first();
  const segmentLeft = (await segment.boundingBox())!.x;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 120, y, { steps: 8 });
  await page.mouse.up();
  await expect.poll(scrollLeft).toBe(initialScroll + 120);
  expect(await time()).toBe(initialTime);
  await expect.poll(async () => (await segment.boundingBox())!.x).toBeCloseTo(segmentLeft - 120, 0);
  // The pointer is still at x - 120: moving to x - 60 pans right by 60.
  await page.mouse.down();
  await page.mouse.move(x - 60, y, { steps: 8 });
  await page.mouse.up();
  await expect.poll(scrollLeft).toBe(initialScroll + 60);
  expect(await time()).toBe(initialTime);
  await page.mouse.click(x, y);
  await expect.poll(time).toBeGreaterThan(initialTime);
});
