import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage();
  await page.addInitScript(() => {
    localStorage.setItem('voicestudio.setup.complete.v1', '1');
  });
  const requests = [];
  await page.route('**/api/tools/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON();
    requests.push({ path, body });
    return route.fulfill({
      json: path.endsWith('direction')
        ? {
            method: 'heuristic',
            instruct_prompt: 'urgent',
            translate_hint: 'surprised',
            rate_bias: 1.1,
            tokens: ['urgent'],
            taxonomy: {},
          }
        : path.endsWith('rate-fit')
          ? { method: 'heuristic', text: 'Fitted line', ratio: 1, attempts: 1 }
          : { format: { duration: '4', format_name: 'wav' } },
    });
  });
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902') + '/#/tools');
  assert(await page.getByRole('button', { name: 'Parse', exact: true }).isDisabled());
  await page.getByRole('textbox', { name: 'Direction', exact: true }).fill('urgent and surprised');
  await page.getByRole('button', { name: 'Parse', exact: true }).click();
  await page.getByText('urgent', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Speech-rate fit', exact: true }).click();
  await page.getByRole('textbox', { name: 'Translated line', exact: true }).fill('A line');
  await page.getByRole('spinbutton', { name: 'Slot (seconds)', exact: true }).fill('0');
  assert(await page.getByRole('button', { name: 'Fit', exact: true }).isDisabled());
  await page.getByRole('spinbutton', { name: 'Slot (seconds)', exact: true }).fill('2.5');
  await page.getByRole('button', { name: 'Fit', exact: true }).click();
  await page.getByText('Fitted line', { exact: true }).waitFor();
  assert.deepEqual(requests.at(-1).body, { text: 'A line', slot_seconds: 2.5, target_lang: 'en' });
  await page.getByRole('button', { name: 'Probe file', exact: true }).click();
  await page
    .getByRole('textbox', { name: 'Absolute file path', exact: true })
    .fill('/fixture/sample.wav');
  await page.getByRole('button', { name: 'Probe', exact: true }).click();
  await page.getByText(/format_name/).waitFor();
  assert.equal(requests.at(-1).body.path, '/fixture/sample.wav');
  console.log(
    'PASS: direction parser, rate-fit validation/request/result, and file metadata probe',
  );
} finally {
  await browser.close();
}
