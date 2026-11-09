import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const browser = await chromium.launch({
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  headless: true,
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
});
async function mockBackend(target) {
  await target.route(
    (url) => url.pathname.startsWith('/api/'),
    (route) => {
      const path = new URL(route.request().url()).pathname.replace(/^\/api(?:\/api)?/, '');
      const json =
        path === '/setup/status'
          ? { models_ready: true, missing: [] }
          : path === '/health'
            ? { status: 'ok' }
            : path === '/settings/analytics'
              ? { available: false, prompted: true, opted_in: false }
              : path === '/models/install/status'
                ? { jobs: [] }
                : path === '/calls/readiness'
                  ? { items: [] }
                  : path === '/calls'
                    ? { calls: [] }
                    : path === '/profiles'
                      ? []
                      : path === '/history'
                        ? []
                        : path === '/models'
                          ? { models: [] }
                          : path === '/model/loaded'
                            ? { models: [], count: 0 }
                            : path === '/batch/jobs'
                              ? []
                              : path === '/engines/translation'
                                ? { engines: [], active: '' }
                                : undefined;
      return route.fulfill({
        status: json === undefined ? 404 : 200,
        json: json ?? { detail: 'Not available in workflow smoke fixture' },
      });
    },
  );
}
await mockBackend(page);
const errors = [];
const nativeDialogs = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('console', (message) => {
  if (message.type() !== 'error') return;
  const value = message.text();
  if (!value.includes('Failed to load resource') && !value.includes('WebSocket connection'))
    errors.push(value);
});
page.on('dialog', async (dialog) => {
  nativeDialogs.push(dialog.message());
  await dialog.dismiss();
});

try {
  await page.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
  await page.goto(`${base}/#/workflows`, { waitUntil: 'domcontentloaded' });
  try {
    await page.getByRole('textbox', { name: 'Workflow name' }).waitFor({ timeout: 5000 });
  } catch (error) {
    console.error(await page.locator('body').innerText(), errors);
    throw error;
  }
  try {
    await page.waitForFunction(
      () => document.querySelectorAll('.react-flow__node').length === 3,
      null,
      { timeout: 5000 },
    );
  } catch (error) {
    console.error(await page.locator('body').innerText(), errors);
    throw error;
  }
  assert.equal(await page.locator('.react-flow__node').count(), 3);
  // A fresh draft flags its missing settings and says why Run is unavailable.
  assert.equal(await page.locator('.workflow-node__badge').count(), 2);
  assert(
    await page
      .locator('.workflow-intro')
      .getByRole('button', { name: 'Run workflow' })
      .isDisabled(),
  );
  await page.getByRole('button', { name: 'Start needs setup' }).click();
  await page
    .getByRole('complementary', { name: 'Step details' })
    .getByLabel('Scripts', { exact: true })
    .waitFor();
  // The step palette is reachable from the keyboard and moves with arrow keys.
  const palette = page.getByRole('toolbar', { name: 'Add a step' });
  await palette.getByRole('button', { name: 'Add Start' }).focus();
  await page.keyboard.press('ArrowDown');
  assert.equal(
    await page.evaluate(() => document.activeElement?.getAttribute('aria-label')),
    'Add Source clip',
  );
  const node = page.locator('.react-flow__node').first();
  const before = await page.evaluate(
    () =>
      JSON.parse(localStorage.getItem('voicestudio.workflows.v1')).documents[0].steps[0].position.x,
  );
  await node.click();
  await node.focus();
  await node.press('ArrowRight');
  await page.waitForFunction(
    (before) =>
      JSON.parse(localStorage.getItem('voicestudio.workflows.v1')).documents[0].steps[0].position
        .x > before,
    before,
  );
  await page.reload();
  await page.getByRole('textbox', { name: 'Workflow name' }).waitFor();
  assert(
    (await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem('voicestudio.workflows.v1')).documents[0].steps[0].position
          .x,
    )) > before,
  );
  await page
    .getByRole('toolbar', { name: 'Add a step' })
    .getByRole('button', { name: 'Add Call', exact: true })
    .click();
  await page.waitForFunction(() => document.querySelectorAll('.react-flow__node').length === 4);
  assert.equal(await page.locator('.react-flow__node').count(), 4);
  await page.getByLabel('Step name').fill('Booking');
  await page.getByLabel('Phone number').fill('+15550100199');
  await page.getByLabel('What should it do?').fill('Ask about opening hours');
  const stored = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('voicestudio.workflows.v1')),
  );
  assert(
    stored.documents[0].steps.some(
      (step) => step.title === 'Booking' && step.phone === '+15550100199',
    ),
  );
  await page.reload();
  await page.getByRole('textbox', { name: 'Workflow name' }).waitFor();
  assert.equal(await page.locator('.react-flow__node').count(), 4);
  await page.getByRole('button', { name: 'Duplicate' }).click();
  await page.getByRole('button', { name: 'Untitled workflow (2)' }).waitFor();
  await page.locator('.workflow-intro').getByRole('button', { name: 'Delete' }).click();
  const deleteDialog = page.getByRole('dialog', { name: 'Delete' });
  await deleteDialog.waitFor();
  await page.waitForTimeout(180);
  await page.screenshot({
    path: join(tmpdir(), 'voicestudio-workflow-delete.png'),
    fullPage: true,
  });
  await deleteDialog.getByRole('button', { name: 'Cancel' }).click();
  assert.equal(await page.getByRole('button', { name: 'Untitled workflow (2)' }).count(), 1);
  await page.locator('.workflow-intro').getByRole('button', { name: 'Delete' }).click();
  await deleteDialog.getByRole('button', { name: 'Delete' }).click();
  assert.equal(await page.getByRole('button', { name: 'Untitled workflow (2)' }).count(), 0);
  assert.deepEqual(nativeDialogs, []);
  assert.equal(await page.getByRole('complementary', { name: 'Step details' }).count(), 0);
  await page.getByRole('button', { name: 'New from template' }).click();
  const picker = page.getByRole('dialog', { name: 'Start from a template' });
  await picker.getByRole('region', { name: 'Calls' }).waitFor();
  await page.waitForTimeout(180);
  await page.screenshot({ path: join(tmpdir(), 'voicestudio-workflow-templates.png') });
  await picker.getByRole('button', { name: 'Book a restaurant', exact: true }).click();
  await picker.waitFor({ state: 'detached' });
  await page.waitForFunction(() => document.querySelectorAll('.react-flow__node').length === 3);
  await page.getByLabel('Phone number').fill('+15550100199');
  assert(
    (await page.getByLabel('What should it do?').inputValue()).includes(
      'Book a table for 2 people',
    ),
  );
  const callNode = await page.locator('.react-flow__node').nth(1).boundingBox();
  assert(callNode && callNode.width >= 170, 'template nodes should open at a readable size');
  await page.getByRole('button', { name: 'Prepare call' }).click();
  assert.equal(await page.locator('input[type="tel"]').inputValue(), '+15550100199');
  assert(
    (await page.locator('form textarea').first().inputValue()).includes(
      'Book a table for 2 people',
    ),
  );
  await page.getByRole('button', { name: 'Canvas', exact: true }).click();
  await page.getByRole('textbox', { name: 'Workflow name' }).waitFor();
  assert.equal(await page.locator('.react-flow__node').count(), 3);
  await page.screenshot({
    path: join(tmpdir(), 'voicestudio-workflow-canvas.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1500 });
  const tallNode = await page.locator('.react-flow__node').nth(1).boundingBox();
  assert(
    tallNode && tallNode.y < 450 && tallNode.width >= 240,
    'the call stays readable near the top of a tall canvas',
  );
  await page.screenshot({ path: join(tmpdir(), 'voicestudio-workflow-tall.png'), fullPage: true });
  await page.setViewportSize({ width: 640, height: 720 });
  const mobileWidth = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  assert(mobileWidth.scroll <= mobileWidth.client, 'workflow canvas overflows a narrow window');
  await page.screenshot({
    path: join(tmpdir(), 'voicestudio-workflow-mobile.png'),
    fullPage: true,
  });
  assert.deepEqual(errors, []);

  const localized = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await mockBackend(localized);
  await localized.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
    localStorage.setItem('voicestudio.locale', 'de');
    localStorage.setItem(
      'voicestudio.theme.v2',
      JSON.stringify({ mode: 'light', light: 'default', dark: 'default' }),
    );
    localStorage.setItem(
      'voicestudio.workflows.v1',
      JSON.stringify({
        version: 1,
        activeId: 'legacy',
        documents: ['workflows.untitled', 'workflows.untitled copy copy'].map((name, index) => ({
          id: index ? 'legacy-copy' : 'legacy',
          name,
          updatedAt: 1,
          steps: [
            {
              id: 'start',
              kind: 'start',
              position: { x: 80, y: 180 },
              title: '',
              text: '',
              phone: '',
            },
          ],
          connections: [],
        })),
      }),
    );
  });
  await localized.goto(`${base}/#/workflows`, { waitUntil: 'domcontentloaded' });
  await localized.getByRole('textbox', { name: 'Workflow-Name' }).waitFor();
  await localized.getByRole('button', { name: 'Unbenannter Workflow', exact: true }).waitFor();
  await localized.getByRole('button', { name: 'Unbenannter Workflow (3)' }).waitFor();
  await localized.getByRole('button', { name: 'Bedingung hinzufügen' }).waitFor();
  assert.equal(await localized.getByRole('complementary', { name: 'Schrittdetails' }).count(), 0);
  assert.equal(await localized.getByText('workflows.untitled').count(), 0);
  await localized.screenshot({
    path: join(tmpdir(), 'voicestudio-workflow-light.png'),
    fullPage: true,
  });
  await localized.close();
  console.log('Workflow canvas smoke passed');
} finally {
  await browser.close();
}
