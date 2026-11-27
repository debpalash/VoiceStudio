import assert from 'node:assert/strict';
import { launchSmokeBrowser, openMockedPage } from './smoke-api-mock.mjs';

const browser = await launchSmokeBrowser();
try {
  const page = await openMockedPage(browser, {
    viewport: { width: 1200, height: 800 },
    fixtures: {
      '/api/profiles': [],
      '/api/history': [],
      '/api/projects': [],
    },
  });
  await page.goto(
    (process.env.VOICESTUDIO_UI_URL || 'http://localhost:3928') + '/#/settings/appearance',
  );
  const dark = page.getByRole('group', { name: 'Dark', exact: true });
  const studioDark = dark.getByRole('button', { name: 'Studio', exact: true });
  const original = dark.getByRole('button', { name: 'VoiceStudio Original', exact: true });
  assert.equal(
    await dark
      .getByRole('button', { name: 'VoiceStudio Classic', exact: true })
      .getAttribute('aria-pressed'),
    'true',
  );
  assert.equal(await original.getAttribute('aria-pressed'), 'false');
  await original.click();
  assert.equal(await original.getAttribute('aria-pressed'), 'true');
  assert.equal(await page.evaluate(() => document.documentElement.dataset.themeId), 'signal');
  const originalBackground = await page.evaluate(
    () => getComputedStyle(document.body).backgroundColor,
  );
  await studioDark.click();
  let state = await page.evaluate(() => ({
    dark: document.documentElement.classList.contains('dark'),
    palette: document.documentElement.dataset.themeId ?? null,
    saved: JSON.parse(localStorage.getItem('voicestudio.theme.v2') || '{}'),
  }));
  assert.equal(state.dark, true);
  assert.equal(state.palette, null);
  assert.equal(state.saved.dark, 'default');
  assert.equal(state.saved.mode, 'dark');
  assert.equal(
    await page.evaluate(() => getComputedStyle(document.body).backgroundColor),
    'rgb(0, 0, 0)',
  );
  assert.equal(
    await studioDark
      .locator('div[aria-hidden=true]')
      .evaluate((preview) => getComputedStyle(preview).backgroundColor),
    'rgb(0, 0, 0)',
  );
  assert.equal(
    await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--radius').trim(),
    ),
    '0.5rem',
  );
  assert.notEqual(
    await page.evaluate(() => getComputedStyle(document.body).backgroundColor),
    originalBackground,
  );
  assert.equal(
    await dark.getByRole('button', { name: 'Studio', exact: true }).getAttribute('aria-pressed'),
    'true',
  );
  assert.equal(await original.getAttribute('aria-pressed'), 'false');

  const light = page.getByRole('group', { name: 'Light', exact: true });
  const studioLight = light.getByRole('button', { name: 'Studio', exact: true });
  assert.equal(
    await light
      .getByRole('button', { name: 'VoiceStudio Classic', exact: true })
      .getAttribute('aria-pressed'),
    'true',
  );
  await studioLight.click();
  state = await page.evaluate(() => ({
    dark: document.documentElement.classList.contains('dark'),
    palette: document.documentElement.dataset.themeId ?? null,
    saved: JSON.parse(localStorage.getItem('voicestudio.theme.v2') || '{}'),
  }));
  assert.equal(state.dark, false);
  assert.equal(state.palette, null);
  assert.equal(state.saved.light, 'default');
  assert.equal(state.saved.mode, 'light');
  assert.equal(
    await page.evaluate(() => getComputedStyle(document.body).backgroundColor),
    await studioLight
      .locator('div[aria-hidden=true]')
      .evaluate((preview) => getComputedStyle(preview).backgroundColor),
  );

  await page.reload();
  assert.equal(
    await page.evaluate(() => document.documentElement.classList.contains('dark')),
    false,
  );
  assert.equal(
    await light.getByRole('button', { name: 'Studio', exact: true }).getAttribute('aria-pressed'),
    'true',
  );
  console.log('PASS: Studio dark/light applies and survives reload');
} finally {
  await browser.close();
}
