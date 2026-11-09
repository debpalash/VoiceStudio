import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage();
  let state = {
    configured: '',
    effective: '',
    mode: 'auto',
    presets: [{ label: 'Official', url: 'https://huggingface.co' }],
    auto: null,
  };
  let tests = 0;
  let fail = false;
  const writes = [];
  await page.route('**/api/api/settings/hf-mirror', (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      writes.push(body);
      if (fail) return route.fulfill({ status: 500, json: { detail: 'private diagnostic' } });
      state = { ...state, configured: body.url, mode: body.mode, restart_required: true };
    }
    return route.fulfill({ json: state });
  });
  await page.route('**/api/api/settings/hf-mirror/test', (route) => {
    tests++;
    state = {
      ...state,
      auto: { endpoint: 'https://huggingface.co', reachable: true, latency_ms: 42 },
    };
    return route.fulfill({ json: state });
  });
  await page.goto(
    (process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902') + '/#/settings/models',
  );
  await page.getByRole('textbox', { name: 'Custom mirror URL', exact: true }).waitFor();
  assert.equal(tests, 0);
  await page.getByRole('button', { name: 'Test endpoints', exact: true }).click();
  await page.getByText('42 ms', { exact: true }).waitFor();
  assert.equal(tests, 1);
  const input = page.getByRole('textbox', { name: 'Custom mirror URL', exact: true });
  await input.fill('https://mirror.example.com');
  await input.press('Enter');
  await page.getByText('Mirror setting saved', { exact: true }).waitFor();
  assert.deepEqual(writes.at(-1), { url: 'https://mirror.example.com', mode: 'manual' });
  await page.reload();
  await input.waitFor();
  assert.equal(await input.inputValue(), 'https://mirror.example.com');
  fail = true;
  await input.fill('https://failed.example.com');
  await input.press('Enter');
  await page.getByRole('alert').waitFor();
  assert.equal(state.configured, 'https://mirror.example.com');
  assert.equal(await page.getByText('private diagnostic').count(), 0);
  fail = false;
  await page.getByRole('radio', { name: /^Auto/ }).click();
  await page.getByText('Mirror setting saved', { exact: true }).waitFor();
  assert.deepEqual(writes.at(-1), { url: '', mode: 'auto' });
  assert.equal(tests, 1);
  console.log(
    'PASS: cached mirror status, explicit probe, save/reload, failed-save preservation and Auto reset',
  );
} finally {
  await browser.close();
}
