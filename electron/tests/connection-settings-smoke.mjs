import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const strings = JSON.parse(
  await readFile(new URL('../src/renderer/src/i18n/locales/en.json', import.meta.url), 'utf8'),
);
const t = (key) => key.split('.').reduce((value, part) => value[part], strings);
const browser = await chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const writes = [];
  let proxy = 'http://old-proxy:7890';
  let fail = false;
  await page.route('**/api/system/info', (route) => route.fulfill({ json: { proxy_url: proxy } }));
  await page.route('**/api/system/set-env', async (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    assert.match(route.request().headers()['content-type'] || '', /application\/json/);
    if (fail && body.key === 'HTTPS_PROXY')
      return route.fulfill({ status: 500, json: { detail: 'private backend detail' } });
    if (body.key === 'HTTP_PROXY') proxy = body.value;
    return route.fulfill({ json: { ok: true } });
  });
  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902';
  await page.goto(base + '/#/settings/network');
  const input = page.getByRole('textbox', { name: t('settings.proxy'), exact: true });
  await page.waitForFunction(
    () => document.querySelector('input[autocomplete="off"]')?.value === 'http://old-proxy:7890',
  );
  await input.fill('http://new-proxy:7890');
  await page.getByRole('button', { name: t('common.save'), exact: true }).click();
  await page.getByRole('status').waitFor();
  assert.equal(writes.length, 6);
  assert.deepEqual(
    new Set(writes.map((write) => write.key)),
    new Set(['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']),
  );
  await page.reload();
  await input.waitFor();
  await page.waitForFunction(
    () => document.querySelector('input[autocomplete="off"]')?.value === 'http://new-proxy:7890',
  );
  await page.getByRole('button', { name: t('settings.proxy_clear'), exact: true }).click();
  await page.getByRole('status').waitFor();
  assert.equal(writes.length, 12);
  assert(writes.slice(6).every((write) => write.value === ''));
  fail = true;
  await input.fill('http://failed-proxy:7890');
  await page.getByRole('button', { name: t('common.save'), exact: true }).click();
  await page.getByRole('alert').waitFor();
  assert.equal(writes.length, 14);
  assert.equal(await page.getByRole('status').count(), 0);
  assert.equal(await page.getByText('private backend detail').count(), 0);
  let tests = 0,
    saves = 0;
  const clears = [];
  const sources = [
    { source: 'app', set: false },
    { source: 'env', set: true, masked: 'hf_env****' },
    { source: 'hf-cli', set: true, masked: 'hf_cli****' },
  ];
  await page.route('**/api/api/settings/hf-token**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'POST') {
      saves++;
      assert.deepEqual(request.postDataJSON(), { token: 'hf_fixture' });
      await new Promise((resolve) => setTimeout(resolve, 100));
      sources[0] = { source: 'app', set: true, masked: 'hf_fix****' };
    }
    if (request.method() === 'DELETE') {
      clears.push(url.searchParams.get('also_clear_hf_cli'));
      sources[0].set = false;
      if (url.searchParams.get('also_clear_hf_cli') === 'true') sources[2].set = false;
    }
    if (url.searchParams.has('fresh')) {
      tests++;
      sources.forEach((source) => {
        source.whoami_ok = source.set;
      });
    }
    return route.fulfill({ json: { active: sources[0].set ? 'app' : 'env', sources } });
  });
  await page.goto(base + '/#/settings/credentials');
  await page.getByText('hf_env****').waitFor();
  assert.equal(tests, 0);
  const token = page.getByLabel(t('settings.hf_token_input'), { exact: true });
  await token.fill('hf_fixture');
  await token.press('Enter');
  await token.press('Enter');
  await page.getByText('hf_fix****').waitFor();
  assert.equal(saves, 1);
  assert.equal(await token.inputValue(), '');
  await page.getByRole('button', { name: t('settings.hf_token_test_now'), exact: true }).click();
  await page.waitForFunction(() => document.body.textContent.includes('Verified'));
  assert.equal(tests, 1);
  await page.getByRole('button', { name: t('settings.hf_token_clear_btn'), exact: true }).click();
  await page.getByRole('button', { name: t('common.cancel'), exact: true }).click();
  assert.equal(clears.length, 0);
  await page.getByRole('button', { name: t('settings.hf_token_clear_btn'), exact: true }).click();
  await page
    .getByRole('button', { name: t('settings.hf_token_clear_btn'), exact: true })
    .last()
    .click();
  await page.getByText('hf_fix****').waitFor({ state: 'detached' });
  assert.deepEqual(clears, [null]);
  await page.getByRole('button', { name: t('settings.hf_token_clear_btn'), exact: true }).click();
  await page.getByRole('switch').check();
  await page
    .getByRole('button', { name: t('settings.hf_token_clear_btn'), exact: true })
    .last()
    .click();
  await page.getByText('hf_cli****').waitFor({ state: 'detached' });
  assert.deepEqual(clears, [null, 'true']);

  // Each provider's fields carry the provider name: "DeepL · DeepL API Key".
  const key = page
    .locator('input')
    .and(page.getByLabel(`DeepL · ${t('settings.llmp_api_key')}`, { exact: true }));
  await key.fill('translation-fixture');
  await key.press('Enter');
  await page.waitForFunction(() =>
    Array.from(document.querySelectorAll('input')).every(
      (input) => input.value !== 'translation-fixture',
    ),
  );
  assert.deepEqual(writes.at(-1), { key: 'DEEPL_API_KEY', value: 'translation-fixture' });
  await page.reload();
  await page.getByText('hf_env****').waitFor();
  assert.equal(tests, 1);
  assert.equal(await token.inputValue(), '');
  assert.deepEqual(errors, []);
  console.log(
    'PASS: proxy save/reload/clear/failure, local token reads, explicit validation, secret clearing and provider credentials',
  );
} finally {
  await browser.close();
}
