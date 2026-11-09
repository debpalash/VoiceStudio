import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage();
try {
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
  });
  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
  await page.goto(base + '/#/clone');
  await page.getByRole('heading', { name: 'Voice cloning', exact: true }).waitFor();
  await page.evaluate(async () => {
    const { router } = await import('/src/router.tsx');
    const route = router.routesById['/settings/support'];
    window.originalSupportComponent = route.options.component;
    window.recoverySentinel = 'renderer-still-running';
    route.options.component = () => {
      throw new Error('Fixture failure at /home/private-user/runtime');
    };
    await router.navigate({ to: '/settings/support' });
  });
  const fallback = page.getByRole('alert');
  await fallback.waitFor();
  assert.ok(!(await fallback.innerText()).includes('private-user'));
  await fallback.locator('summary').click();
  assert.ok((await fallback.locator('pre').innerText()).includes('~/runtime'));
  await page.evaluate(async () => {
    const { router } = await import('/src/router.tsx');
    router.routesById['/settings/support'].options.component = window.originalSupportComponent;
  });
  await fallback.getByRole('button', { name: 'Try again', exact: true }).click();
  await page.getByRole('heading', { name: 'Donate to VoiceStudio Project', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.recoverySentinel), 'renderer-still-running');

  await page.evaluate(async () => {
    const { router } = await import('/src/router.tsx');
    await router.navigate({ to: '/clone' });
  });
  await page.getByRole('heading', { name: 'Voice cloning', exact: true }).waitFor();
  const firstDynamicReload = page.waitForEvent('load');
  await page.evaluate(async () => {
    const { router } = await import('/src/router.tsx');
    const route = router.routesById['/settings/support'];
    route.options.component = () => {
      throw new Error(
        'Failed to fetch dynamically imported module: /home/private-user/gallery-page.tsx',
      );
    };
    await router.navigate({ to: '/settings/support' });
  });
  await firstDynamicReload;
  await page.getByRole('heading', { name: 'Donate to VoiceStudio Project', exact: true }).waitFor();
  assert.equal(
    await page.evaluate(() =>
      Object.keys(sessionStorage).some((key) => key.startsWith('voicestudio.moduleRetry:')),
    ),
    true,
  );

  await page.evaluate(async () => {
    const { router } = await import('/src/router.tsx');
    await router.navigate({ to: '/clone' });
  });
  await page.getByRole('heading', { name: 'Voice cloning', exact: true }).waitFor();
  await page.evaluate(async () => {
    const { router } = await import('/src/router.tsx');
    window.dynamicRepair = null;
    window.recoverySentinel = 'renderer-still-running-after-reload';
    window.addEventListener(
      'voicestudio:repair-agent-open',
      (event) => {
        window.dynamicRepair = event.detail;
      },
      { once: true },
    );
    const route = router.routesById['/settings/support'];
    window.originalSupportComponent = route.options.component;
    route.options.component = () => {
      throw new Error(
        'Failed to fetch dynamically imported module: /home/private-user/gallery-page.tsx',
      );
    };
    await router.navigate({ to: '/settings/support' });
  });
  await page.waitForFunction(() => window.dynamicRepair?.autoFix === true);
  const dynamicFallback = page.getByRole('alert');
  await dynamicFallback.waitFor();
  assert.ok(!(await dynamicFallback.innerText()).includes('private-user'));
  assert.ok(!(await page.evaluate(() => window.dynamicRepair.report)).includes('private-user'));
  await page.evaluate(async () => {
    const { router } = await import('/src/router.tsx');
    router.routesById['/settings/support'].options.component = window.originalSupportComponent;
  });
  await dynamicFallback.getByRole('button', { name: 'Try again', exact: true }).click();
  await page.getByRole('heading', { name: 'Donate to VoiceStudio Project', exact: true }).waitFor();
  assert.equal(
    await page.evaluate(() => window.recoverySentinel),
    'renderer-still-running-after-reload',
  );
  console.log(
    'Routed recovery, one-time dynamic-module reload, and repeated-failure repair handoff passed.',
  );
} finally {
  await browser.close();
}
