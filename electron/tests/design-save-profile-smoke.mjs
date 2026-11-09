import { launchSmokeBrowser, openMockedPage } from './smoke-api-mock.mjs';
import assert from 'node:assert/strict';

// Saving a designed voice confirms it: the form only clears its name field, so
// without the toast the save looked like nothing happened.
const BASE = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const browser = await launchSmokeBrowser();
try {
  const created = [];
  const page = await openMockedPage(browser, {
    viewport: { width: 1280, height: 860 },
    fixtures: {
      '/api/history': [],
      '/api/projects': [],
      '/api/export/history': [],
      '/api/longform/jobs': { jobs: [] },
    },
    handle: (route, request, path) => {
      if (path !== '/api/profiles') return false;
      if (request.method() === 'POST') {
        return (async () => {
          const form = await new Response(request.postDataBuffer(), {
            headers: { 'content-type': request.headers()['content-type'] },
          }).formData();
          const profile = {
            id: 'designed-profile',
            name: String(form.get('name') || ''),
            kind: 'design',
            language: 'English',
          };
          created.push(profile);
          return route.fulfill({ json: profile });
        })();
      }
      if (request.method() === 'GET') return route.fulfill({ json: created });
      return false;
    },
  });
  await page.goto(`${BASE}/#/design`);
  await page.getByRole('textbox', { name: 'Profile name' }).fill('Release voice');
  await page.getByRole('button', { name: 'Save as voice profile' }).click();
  await page.locator('[data-sonner-toast]', { hasText: 'Voice profile saved.' }).waitFor();
  assert.equal(created.length, 1);
  assert.equal(await page.getByRole('textbox', { name: 'Profile name' }).inputValue(), '');
  console.log('design save profile smoke passed');
} finally {
  await browser.close();
}
