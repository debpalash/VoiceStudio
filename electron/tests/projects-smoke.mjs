import { launchSmokeBrowser, openMockedPage } from './smoke-api-mock.mjs';
import assert from 'node:assert/strict';
const browser = await launchSmokeBrowser({ fallbackChannel: 'msedge' });
try {
  const page = await openMockedPage(browser);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const stored = {
    id: 'legacy',
    name: 'Legacy project',
    state: {
      dubJobId: 'fixture',
      dubFilename: 'clip.wav',
      inputType: 'audio',
      dubLang: 'Spanish',
      dubStep: 'editing',
      dubSegments: [{ id: 1, start: 0, end: 2, text: 'Legacy sentence' }],
      preserveBg: false,
    },
  };
  let row = stored;
  let writes = 0;
  let renames = 0;
  await page.addInitScript(() => {
    localStorage.setItem(
      'omni_transcriptions',
      JSON.stringify([
        {
          id: 42,
          text: 'Fixture transcript',
          language: 'English',
          duration_s: 3,
          segments: [],
          timestamp: new Date().toISOString(),
        },
      ]),
    );
  });
  await page.route('**/api/profiles', (route) =>
    route.fulfill({
      json: [
        {
          id: 'voice-1',
          name: 'Library voice',
          kind: 'clone',
          ref_audio_path: 'voice.wav',
          created_at: 1,
        },
      ],
    }),
  );
  await page.route('**/api/history', (route) =>
    route.fulfill({
      json: [
        {
          id: 'take-1',
          text: 'Library take',
          mode: 'clone',
          audio_path: 'take.wav',
          created_at: 2,
        },
      ],
    }),
  );
  await page.route('**/api/export/history', (route) =>
    route.fulfill({
      json: [
        {
          id: 'export-1',
          filename: 'finished.wav',
          destination_path: 'C:\\exports\\finished.wav',
          mode: 'clone',
          created_at: 3,
        },
      ],
    }),
  );
  await page.route('**/api/longform/jobs**', (route) => route.fulfill({ json: { jobs: [] } }));
  await page.route('**/api/projects**', (route) => {
    const method = route.request().method();
    if (method === 'PATCH') {
      renames++;
      row = { ...row, name: route.request().postDataJSON().name };
      return route.fulfill({ json: row });
    }
    if (method === 'DELETE') {
      row = null;
      return route.fulfill({ json: { deleted: 'legacy' } });
    }
    if (method === 'PUT') {
      writes++;
      row = { ...route.request().postDataJSON(), id: 'legacy' };
      return route.fulfill({ json: row });
    }
    return route.fulfill({
      json: route.request().url().endsWith('/projects') ? (row ? [row] : []) : row,
    });
  });
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902') + '/#/projects');
  const filters = page.locator('[data-slot=secondary-sidebar]');
  await filters.getByRole('button', { name: 'Voice profiles 1', exact: true }).click();
  await page.locator('main').getByRole('heading', { name: 'Library voice', exact: true }).waitFor();
  await filters.getByRole('button', { name: 'Transcripts 1', exact: true }).click();
  await page.getByText('Fixture transcript', { exact: true }).waitFor();
  await filters.getByRole('button', { name: 'All projects 1', exact: true }).click();
  // History takes and exports keep their own lists instead of mixing into All projects.
  assert.equal(await page.getByText('Library take', { exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Legacy project', exact: true }).click();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert(page.url().endsWith('/projects'));
  await page.getByRole('button', { name: 'Legacy project', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm', exact: true }).click();
  await page.waitForURL('**/#/dub');
  // Saving the open dub lives in Dubbing, in the pane's Projects list.
  const pane = page.locator('[data-slot=secondary-sidebar]');
  const openSaveForm = () => pane.getByRole('tab', { name: 'Projects', exact: true }).click();
  const workspaces = page.getByRole('navigation', { name: 'Workspaces' });
  await openSaveForm();
  await page.locator('#project-name').fill('Renamed project');
  await page
    .locator('[data-slot=dub-save-project]')
    .getByRole('button', { name: 'Save', exact: true })
    .click();
  await pane.getByRole('button', { name: /^Renamed project/ }).waitFor();
  assert.equal(writes, 1);
  assert.equal(row.state.preserveBg, false);
  await page.reload();
  await openSaveForm();
  assert.equal(await page.locator('#project-name').inputValue(), 'Renamed project');
  await workspaces.getByRole('link', { name: 'Projects', exact: true }).click();
  await page.getByRole('button', { name: 'Rename Renamed project', exact: true }).click();
  await page.getByLabel('Project name', { exact: true }).fill('Library renamed');
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await page.getByRole('button', { name: 'Library renamed', exact: true }).waitFor();
  assert.equal(renames, 1);
  assert.equal(writes, 1);
  assert.equal(row.state.preserveBg, false);
  // Away from its own pages the Dubbing group is closed: open it, then follow its link from the
  // group's popover (rendered outside the navigation landmark in the compact rail).
  await workspaces.getByRole('button', { name: 'Dubbing', exact: true }).click();
  await page.getByRole('link', { name: 'Dubbing', exact: true }).click();
  await openSaveForm();
  assert.equal(await page.locator('#project-name').inputValue(), 'Library renamed');
  await workspaces.getByRole('link', { name: 'Projects', exact: true }).click();
  await page.getByRole('button', { name: 'Delete Library renamed', exact: true }).click();
  assert(row);
  const dialog = page.getByRole('dialog');
  await dialog.waitFor();
  const bounds = await dialog.boundingBox();
  assert(bounds && bounds.y >= 0 && bounds.y + bounds.height <= page.viewportSize().height);
  if (process.env.VOICESTUDIO_SCREENSHOT)
    await page.screenshot({ path: process.env.VOICESTUDIO_SCREENSHOT });
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await page
    .getByRole('button', { name: 'Library renamed', exact: true })
    .waitFor({ state: 'detached' });
  await filters.getByRole('button', { name: 'Voice profiles 1', exact: true }).click();
  await page.locator('main').getByRole('heading', { name: 'Library voice', exact: true }).waitFor();
  assert.equal(row, null);
  assert.deepEqual(errors, []);
  console.log(
    'PASS: legacy project open/cancel, save, preserved options, reload and confirmed deletion',
  );
} finally {
  await browser.close();
}
