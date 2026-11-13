import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage();
  const calls = [];
  let reject = false;
  let active = 'other';
  const provider = {
    id: 'custom',
    display_name: 'Fixture provider',
    local: false,
    configured: true,
    base_url: 'https://example.com/v1',
    model: 'fixture-model',
    has_key: true,
    active_from_env: false,
    base_url_from_env: true,
  };
  await page.route('**/api/api/settings/llm-providers', (route) =>
    route.fulfill({ json: { active, engine_active: active === 'custom' ? 'openai-compat' : 'off', providers: [provider] } }),
  );
  await page.route('**/api/api/settings/llm-providers/custom', (route) => {
    const body = route.request().postDataJSON();
    calls.push({ kind: 'save', body });
    if (reject) return route.fulfill({ status: 500, json: { detail: 'fixture failure' } });
    if (body.make_active) active = 'custom';
    return route.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/api/settings/llm-providers/custom/test', (route) => {
    calls.push({ kind: 'test' });
    return route.fulfill({ json: { ok: true, model: 'fixture-model', latency_ms: 5 } });
  });
  await page.route('**/api/api/settings/llm-providers/custom/connect', (route) => {
    calls.push({ kind: 'connect' });
    active = 'custom';
    return route.fulfill({ json: { ok: true, model: 'fixture-model', latency_ms: 5 } });
  });
  await page.route('**/api/api/settings/llm-providers/custom/models', (route) => {
    calls.push({ kind: 'models' });
    return route.fulfill({ json: { ok: true, models: ['model-two'] } });
  });
  await page.goto(
    (process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902') + '/#/settings/models/llm',
  );
  const model = page.getByRole('textbox', { name: 'Model', exact: true });
  await model.waitFor();
  assert(await page.getByRole('textbox', { name: 'Base URL', exact: true }).isDisabled());
  const key = page.locator('input[type=password]');
  assert.equal(await key.inputValue(), '');
  await model.fill('edited-model');
  await page.getByRole('button', { name: 'Test', exact: true }).click();
  await page.getByText(/ok .*fixture-model/).waitFor();
  assert.deepEqual(
    calls.map((call) => call.kind),
    ['save', 'test'],
  );
  assert.equal(calls[0].body.model, 'edited-model');
  assert(!('api_key' in calls[0].body));
  assert(!('base_url' in calls[0].body));
  reject = true;
  await page.getByRole('button', { name: 'Test', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'Save failed.' }).waitFor();
  assert.equal(calls.filter((call) => call.kind === 'test').length, 1);
  reject = false;
  await page.getByRole('button', { name: 'Fetch models', exact: true }).click();
  await page.getByRole('button', { name: 'model-two', exact: true }).click();
  assert.equal(await model.inputValue(), 'model-two');
  await page.getByRole('button', { name: 'Connect & enable', exact: true }).click();
  await page.getByRole('button', { name: 'Turn off LLM', exact: true }).waitFor();
  assert.equal(calls.filter((call) => call.kind === 'connect').length, 1);
  assert.equal(await page.getByText('Off (no LLM)', { exact: true }).count(), 0);
  console.log(
    'PASS: LLM pinned fields, preserved secret, save-before-test, failed-save guard, model catalogue and explicit activation',
  );
} finally {
  await browser.close();
}
