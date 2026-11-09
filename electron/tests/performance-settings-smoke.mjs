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
  let device = {
    value: 'auto',
    applied: 'auto',
    effective_family: 'cuda',
    available_families: ['cuda', 'cpu'],
    env_pinned: false,
    override_ignored: false,
    restart_required: false,
  };
  let compile = { platform: 'win32', enabled: false };
  let performance = {
    global: 'balanced',
    overrides: {},
    effective: {
      tts: 'balanced',
      asr: 'balanced',
      dictation: 'balanced',
      diarisation: 'balanced',
      translation: 'balanced',
      llm: 'balanced',
    },
    families: ['tts', 'asr', 'dictation', 'diarisation', 'translation', 'llm'],
    implemented_families: ['tts', 'asr', 'dictation', 'diarisation', 'translation'],
    applicable_families: ['tts', 'asr', 'dictation', 'translation'],
    targets: {
      tts: { steps: 16, postprocess: true },
      asr: { engine: 'faster-whisper', beam_size: 3, best_of: 3 },
      dictation: { engine: 'sherpa-onnx', max_active_paths: 4 },
      diarisation: { engine: 'audiocpp-sortformer' },
      translation: { engine: 'nllb', num_beams: 3 },
    },
    selections: {
      tts: { engine: 'omnivoice', model: 'k2-fsa/OmniVoice' },
      asr: { engine: 'faster-whisper', model: 'deepdml/faster-whisper-large-v3-turbo-ct2' },
      dictation: {
        engine: 'offline-transducer',
        model: 'sherpa-parakeet-tdt-v3',
        label: 'Parakeet TDT v3',
      },
      diarisation: { engine: 'inactive', model: null },
      translation: { engine: 'nllb', model: 'facebook/nllb-200-distilled-600M' },
      llm: { engine: 'inactive', model: null },
    },
  };
  const performanceUpdates = [];
  let rejectDevice = false;
  const budgets = [];
  await page.route('**/api/api/settings/compute-device', (route) => {
    if (route.request().method() === 'PUT') {
      if (rejectDevice) return route.fulfill({ status: 500, json: { detail: 'Failed' } });
      device = { ...device, ...route.request().postDataJSON(), restart_required: true };
    }
    return route.fulfill({ json: device });
  });
  await page.route('**/api/api/settings/perf/torch-compile-disabled', (route) => {
    if (route.request().method() === 'PUT')
      compile = { ...compile, ...route.request().postDataJSON() };
    return route.fulfill({ json: compile });
  });
  await page.route('**/api/api/settings/performance-profile', (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      performanceUpdates.push(body);
      performance = {
        ...performance,
        overrides: {
          ...performance.overrides,
          ...(body.family ? { [body.family]: body.tier } : {}),
        },
        effective: body.family
          ? { ...performance.effective, [body.family]: body.tier }
          : Object.fromEntries(performance.families.map((family) => [family, body.tier])),
        global: body.family ? performance.global : body.tier,
      };
    }
    return route.fulfill({ json: performance });
  });
  await page.route('**/api/engines', (route) =>
    route.fulfill({
      json: {
        tts: { active: 'omnivoice', active_model: 'k2-fsa/OmniVoice', backends: [] },
        asr: {
          active: 'faster-whisper',
          active_model: 'deepdml/faster-whisper-large-v3-turbo-ct2',
          backends: [],
        },
        llm: { active: 'off', active_model: null, backends: [] },
      },
    }),
  );
  await page.route('**/api/dictation/prefs', (route) =>
    route.fulfill({
      json: { enabled: true, model_id: 'sherpa-parakeet-tdt-v3' },
    }),
  );
  await page.route('**/api/dictation/models', (route) =>
    route.fulfill({
      json: {
        engine_available: true,
        models: [{ id: 'sherpa-parakeet-tdt-v3', label: 'Parakeet TDT v3', installed: true }],
      },
    }),
  );
  await page.route('**/api/batch/jobs**', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/system/info', (route) =>
    route.fulfill({
      json: {
        platform: 'win32',
        arch: 'x64',
        python: '3.11',
        generate_timeout_s: 300,
        cpu_generate_timeout_s: 600,
        generate_timeout_shadowed: false,
        cpu_generate_timeout_shadowed: true,
      },
    }),
  );
  await page.route('**/api/sysinfo', (route) =>
    route.fulfill({ json: { ram: 12, total_ram: 32, vram: 4, gpu_active: true } }),
  );
  await page.route('**/api/system/set-env', (route) => {
    const body = route.request().postDataJSON();
    budgets.push(body);
    return route.fulfill({ json: { shadowed: body.key.includes('CPU') } });
  });
  const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3902';
  await page.goto(base + '/#/settings/performance');
  await page.getByRole('radiogroup').first().waitFor();
  assert.equal(await page.getByRole('radiogroup').count(), 7);
  await page.getByText('k2-fsa/OmniVoice', { exact: true }).waitFor();
  await page.getByText('deepdml/faster-whisper-large-v3-turbo-ct2', { exact: true }).waitFor();
  await page.getByText('Parakeet TDT v3', { exact: true }).waitFor();
  const ttsRow = page
    .locator('[data-slot="settings-row"]')
    .filter({ hasText: t('engineSidebar.tts') });
  const performanceResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'PUT' &&
      response.url().endsWith('/api/settings/performance-profile'),
  );
  await ttsRow.getByRole('radio', { name: t('performanceProfile.fast'), exact: true }).click();
  await performanceResponse;
  assert.deepEqual(performanceUpdates[0], { tier: 'fast', family: 'tts' });
  const unavailableMax = page
    .locator('[data-slot="settings-row"]')
    .filter({ hasText: t('engineSidebar.llm') })
    .getByRole('radio', { name: t('performanceProfile.max'), exact: true });
  assert.equal(await unavailableMax.isDisabled(), true);
  assert.equal(await unavailableMax.getAttribute('aria-checked'), 'false');
  const cpu = page.getByRole('button', { name: t('settings.device_family_cpu'), exact: true });
  await cpu.click();
  await cpu.and(page.locator('[aria-pressed="true"]')).waitFor();
  assert.equal(await cpu.getAttribute('aria-pressed'), 'true');
  await page.getByText(t('settings.compute_device_restart'), { exact: true }).first().waitFor();
  assert.equal(
    await page.locator('[aria-labelledby="compute-active"]').innerText(),
    t('settings.device_family_cuda'),
  );
  rejectDevice = true;
  await page.getByRole('button', { name: t('settings.compute_device_auto'), exact: true }).click();
  await page.getByRole('alert').waitFor();
  assert.equal(await cpu.getAttribute('aria-pressed'), 'true');
  const toggle = page.getByRole('switch', { name: t('settings.perf_torch_compile'), exact: true });
  await toggle.click();
  await toggle.and(page.locator('[aria-checked="true"]')).waitFor();
  assert.equal(compile.enabled, true);
  const gpuBudget = page.getByRole('spinbutton', {
    name: t('settings.generate_timeout_gpu'),
    exact: true,
  });
  await gpuBudget.fill('21601');
  assert.equal(await gpuBudget.locator('..').getByRole('button').isDisabled(), true);
  await gpuBudget.fill('900');
  const savedBudget = page.waitForResponse((response) =>
    response.url().endsWith('/system/set-env'),
  );
  await gpuBudget.press('Enter');
  await savedBudget;
  assert.deepEqual(budgets[0], { key: 'OMNIVOICE_GENERATE_TIMEOUT_S', value: '900' });
  const cpuBudget = page.getByRole('spinbutton', {
    name: t('settings.generate_timeout_cpu'),
    exact: true,
  });
  await cpuBudget.fill('1200');
  await cpuBudget.press('Enter');
  await page
    .getByRole('status')
    .filter({ hasText: t('settings.generate_timeout_shadowed_note') })
    .waitFor();
  assert.equal(budgets.length, 2);
  device = {
    ...device,
    value: 'mps',
    effective_family: 'cpu',
    available_families: ['cpu'],
    env_pinned: true,
    override_ignored: true,
    restart_required: false,
  };
  compile = { platform: 'linux', enabled: false };
  await page.reload();
  await page.getByText(t('settings.compute_device_env_pinned')).waitFor();
  await page.getByText(t('settings.compute_device_ignored')).waitFor();
  assert.equal(await cpu.isDisabled(), true);
  // torch.compile stays a live, user-controllable switch on every platform
  // (#2135): the env-pinned device must not lock it, and the linux state loads.
  await toggle.and(page.locator(':not([data-disabled]):not([aria-disabled="true"])')).waitFor();
  assert.equal(await toggle.isEnabled(), true);
  assert.equal(await toggle.getAttribute('aria-checked'), 'false');
  assert.equal(
    await page.getByRole('button', { name: t('settings.device_family_cuda'), exact: true }).count(),
    0,
  );
  assert.deepEqual(errors, []);
  console.log(
    'PASS: model-aware engine tiers, unavailable-family guard, compute rollback, environment pins, compile guard and timeout budgets',
  );
} finally {
  await browser.close();
}
