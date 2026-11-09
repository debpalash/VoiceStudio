import assert from 'node:assert/strict';
import { DEFAULT_FIXTURES } from './fake-health-backend.mjs';
import { launchSmokeBrowser, openMockedPage } from './smoke-api-mock.mjs';

const browser = await launchSmokeBrowser();
try {
  const page = await openMockedPage(browser, {
    fixtures: {
      ...Object.fromEntries(
        Object.entries(DEFAULT_FIXTURES).map(([path, value]) => ['/api' + path, value]),
      ),
      '/api/profiles': [
        {
          id: 'preset-fixture',
          name: 'Preset fixture',
          kind: 'clone',
          ref_audio_path: 'fixture.wav',
          created_at: 0,
        },
      ],
      '/api/engines': {
        tts: {
          active: 'fixture',
          backends: [
            {
              id: 'fixture',
              display_name: 'Preset fixture',
              available: true,
              preset_voices: ['M1', 'F3'],
              default_voice: 'F3',
            },
          ],
        },
        asr: { active: '', backends: [] },
        llm: { active: '', backends: [] },
      },
    },
  });
  await page.goto((process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912') + '/#/clone');
  await page.getByRole('button', { name: 'Preset fixture', exact: true }).first().click();
  const trigger = page.getByRole('button', { name: 'Preset voice', exact: true });
  await trigger.click();
  const list = page.getByRole('listbox');
  const colors = await list.evaluate((listbox) => {
    const element = listbox.parentElement;
    const probe = document.createElement('span');
    probe.style.backgroundColor = 'var(--popover)';
    document.body.append(probe);
    const expectedBackground = getComputedStyle(probe).backgroundColor;
    probe.style.color = 'var(--primary)';
    const expectedAccent = getComputedStyle(probe).color;
    probe.remove();
    const option = element.querySelector('[role=option][aria-selected=true]');
    return {
      background: getComputedStyle(element).backgroundColor,
      expectedBackground,
      accent: getComputedStyle(option).color,
      expectedAccent,
    };
  });
  assert.equal(
    colors.background,
    colors.expectedBackground,
    'portaled menu uses the app popover surface',
  );
  assert.notEqual(colors.background, 'rgba(0, 0, 0, 0)');
  assert.equal(colors.accent, colors.expectedAccent, 'selection uses the app accent');
  const search = page.getByRole('combobox', { name: 'Preset voice', exact: true });
  await search.press('Enter');
  assert.equal(await trigger.textContent(), 'F3');
  await trigger.click();
  await search.fill('M1');
  await search.press('Enter');
  assert.equal(await trigger.textContent(), 'M1');
  console.log('PASS: preset picker portal theme and keyboard selection');
} finally {
  await browser.close();
}
