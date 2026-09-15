import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({
  ...(process.env.CHROME_PATH
    ? { executablePath: process.env.CHROME_PATH }
    : { channel: 'msedge' }),
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
let models = false;
let preflight = false;
let partialPreflight = true;
let installs = 0;
const consentWrites = [];
try {
  await page.route('**/api/api/settings/performance-profile', (route) =>
    route.fulfill({
      json: {
        global: 'balanced',
        effective: {},
        applicable_families: ['tts'],
        targets: {},
        selections: {},
      },
    }),
  );
  await page.route('**/api/models', (route) =>
    route.fulfill({
      json: {
        models: [
          {
            repo_id: 'fixture/optional',
            label: 'Optional model',
            role: 'ASR',
            supported: true,
            installed: false,
            size_gb: 1,
          },
          {
            repo_id: 'fixture/recommended',
            label: 'Recommended model',
            role: 'ASR',
            curated: true,
            supported: true,
            installed: false,
            size_gb: 1,
          },
          {
            repo_id: 'k2-fsa/OmniVoice',
            label: 'Required model',
            role: 'TTS',
            required: true,
            supported: true,
            installed: false,
            size_gb: 1,
          },
        ],
      },
    }),
  );
  await page.route('**/api/settings/analytics', (route) => {
    if (route.request().method() === 'PUT') consentWrites.push(route.request().postDataJSON());
    return route.fulfill({
      json: { available: true, prompted: consentWrites.length > 0, opted_in: false },
    });
  });
  await page.route('**/api/setup/status', (route) =>
    route.fulfill({
      json: {
        models_ready: models,
        missing: models ? [] : [{ repo_id: 'k2-fsa/OmniVoice', label: 'Required model' }],
      },
    }),
  );
  await page.route('**/api/setup/preflight', (route) =>
    route.fulfill({
      json: partialPreflight
        ? { ok: false }
        : {
            ok: preflight,
            checks: [
              {
                id: 'ram',
                label: 'Memory',
                status: preflight ? 'pass' : 'fail',
                detail: 'Fixture memory result',
                fix: null,
              },
            ],
          },
    }),
  );
  page.on('request', (req) => {
    if (req.url().endsWith('/models/install') || req.url().includes('/engines/install')) installs++;
  });
  await page.goto('http://localhost:3912/#/clone');
  const next = page.getByRole('button', { name: /All good/ });
  await page.locator('summary', { hasText: 'Credentials' }).click();
  await page.locator('input[type=password]').first().waitFor();
  await page.locator('summary', { hasText: 'Credentials' }).click();
  await next.waitFor();
  assert.ok(await next.isDisabled());
  await page.getByRole('button', { name: 'Re-check', exact: true }).waitFor();
  assert.ok(await page.getByRole('heading', { name: 'VoiceStudio', exact: true }).isVisible());
  partialPreflight = false;
  await page.getByRole('button', { name: 'Re-check', exact: true }).click();
  await page.getByText('Fixture memory result').waitFor();
  assert.ok(await next.isDisabled());
  preflight = true;
  await page.getByRole('button', { name: 'Re-check', exact: true }).click();
  await next.click();
  assert.ok(await next.isDisabled());
  await page.getByRole('button', { name: /Install Balanced pack/i }).waitFor();
  assert.equal(await page.getByRole('heading', { name: 'Required model', exact: true }).count(), 0);
  await page.setViewportSize({ width: 640, height: 800 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: '/tmp/voicestudio-onboarding-packs.png' });
  await page.getByRole('button', { name: 'Advanced', exact: true }).click();
  await page.getByRole('heading', { name: 'Required model', exact: true }).waitFor();
  assert.ok(
    await page.getByRole('heading', { name: 'Recommended model', exact: true }).isVisible(),
  );
  assert.ok(
    !(await page.getByRole('heading', { name: 'Optional model', exact: true }).isVisible()),
  );
  await page.locator('summary', { hasText: 'Show 1 more models' }).click();
  await page.getByRole('heading', { name: 'Optional model', exact: true }).waitFor();
  await page.locator('summary', { hasText: 'Show 1 more models' }).click();
  await page.getByRole('button', { name: 'Advanced', exact: true }).click();
  models = true;
  await page.evaluate(async () => {
    const { queryClient } = await import('/src/lib/query.ts');
    await queryClient.invalidateQueries({ queryKey: ['setup-status'] });
  });
  await next.click();
  const decline = page.getByRole('button', { name: 'No thanks', exact: true });
  await decline.waitFor();
  assert.equal(consentWrites.length, 0);
  await decline.click();
  await decline.waitFor({ state: 'hidden' });
  assert.deepEqual(consentWrites, [{ enabled: false }]);
  await next.click();
  await page.getByRole('button', { name: 'Enter studio', exact: true }).click();
  await page
    .getByRole('button', { name: 'Enter studio', exact: true })
    .waitFor({ state: 'hidden' });
  assert.equal(
    await page.evaluate(() => localStorage.getItem('voicestudio.setup.complete.v1')),
    '1',
  );
  assert.equal(installs, 0);
  console.log(
    'First-run preflight/model gates, privacy/dictation steps and completion passed without downloads.',
  );
} finally {
  await browser.close();
}
