import { chromium } from 'playwright';
import assert from 'node:assert/strict';

// Regression guard: while a big book renders, the Stop button must stay reachable.
// It lives in the setup pane first, and in the editor column once that pane is folded.
const UI = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const SCREENS = [
  { label: 'desktop', width: 1400, height: 860 },
  { label: 'compact', width: 640, height: 760 },
];
const SCRIPT_LINES = 90;
const BOOK_CHAPTERS = 48;

const launchOptions = process.env.PLAYWRIGHT_EXECUTABLE_PATH
  ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
  : { channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' };

/** How far `inner` pokes out of `outer` on each side (all zero or negative when contained). */
function overflow(inner, outer) {
  assert(inner, 'element has no box');
  assert(outer, 'container has no box');
  return {
    left: outer.x - inner.x,
    top: outer.y - inner.y,
    right: inner.x + inner.width - (outer.x + outer.width),
    bottom: inner.y + inner.height - (outer.y + outer.height),
  };
}
function assertContained(inner, outer, what) {
  const worst = Math.max(...Object.values(overflow(inner, outer)));
  assert(worst <= 1, `${what} overflows its container by ${worst}px`);
}

async function prepare(page) {
  await page.addInitScript(
    ({ count }) => {
      localStorage.setItem('voicestudio.setup.complete.v1', '1');
      const lines = [];
      for (let n = 1; n <= count; n++)
        lines.push({
          id: `smoke-${n}`,
          text: `Sentence ${n} of the long layout fixture, written to wrap over a few rows.`,
          profileId: null,
        });
      localStorage.setItem(
        'voicestudio.longform.v1',
        JSON.stringify({ stories: { script: '', title: 'Long fixture', voice: 'v1', lines } }),
      );
    },
    { count: SCRIPT_LINES },
  );
  await page.route('**/api/profiles', (r) => r.fulfill({ json: [{ id: 'v1', name: 'Reader', kind: 'clone' }] }));
  await page.route('**/api/audiobook/jobs', (r) => r.fulfill({ json: { jobs: [] } }));
  await page.route('**/api/engines', (r) =>
    r.fulfill({
      json: {
        tts: { active: 'omnivoice', active_model: 'm1', backends: [{ id: 'omnivoice', name: 'OmniVoice', available: true }] },
      },
    }),
  );
  await page.route('**/api/models', (r) =>
    r.fulfill({
      json: {
        target: 'local',
        models: [{ repo_id: 'm1', label: 'Model', role: 'tts', size_gb: 1, installed: true, supported: true }],
      },
    }),
  );
}

async function startFakeRender(page) {
  await page.evaluate(
    async ({ chapters }) => {
      const { longformSession } = await import('/src/features/longform/longform-session.ts');
      const list = [];
      for (let n = 0; n < chapters; n++)
        list.push({ title: `Section ${n + 1}`, status: n < 15 ? 'done' : n === 15 ? 'rendering' : 'pending' });
      longformSession.setState((state) => ({
        ...state,
        active: 'stories',
        stage: 'rendering',
        completed: 15,
        total: chapters,
        chapters: list,
        startedAt: Date.now() - 45_000,
        lastActivityAt: Date.now(),
      }));
    },
    { chapters: BOOK_CHAPTERS },
  );
}

const browser = await chromium.launch({ ...launchOptions, headless: true });
try {
  for (const screen of SCREENS) {
    const page = await browser.newPage({ viewport: { width: screen.width, height: screen.height } });
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));
    await prepare(page);
    await page.goto(`${UI}/#/stories`);
    await page.locator('[data-slot=story-editor]').waitFor();
    await startFakeRender(page);

    const setup = page.locator('[data-slot=secondary-sidebar]');
    const stopInSetup = setup.locator('[data-slot=generate-panel]').getByRole('button', { name: 'Stop', exact: true });
    await stopInSetup.waitFor();
    assertContained(await stopInSetup.boundingBox(), await setup.boundingBox(), `${screen.label}: Stop in setup pane`);

    await setup.getByRole('button', { name: 'Collapse pane', exact: true }).click();
    const editor = page.locator('section:has([data-slot=story-editor])');
    const stopInEditor = editor.locator('[data-slot=generate-panel]').getByRole('button', { name: 'Stop', exact: true });
    await stopInEditor.waitFor();
    for (const edge of ['top', 'bottom']) {
      await editor.evaluate((el, which) => el.scrollTo(0, which === 'top' ? 0 : el.scrollHeight), edge);
      await page.waitForTimeout(80);
      assertContained(
        await stopInEditor.boundingBox(),
        await editor.boundingBox(),
        `${screen.label}: Stop in editor scrolled to ${edge}`,
      );
    }

    await page.locator('[data-slot=secondary-sidebar-expand]').click();
    await stopInSetup.waitFor();
    assert.equal(await editor.locator('[data-slot=generate-panel]').count(), 0, `${screen.label}: sticky copy remains`);
    assert.deepEqual(pageErrors, [], `${screen.label}: page errors`);
    await page.close();
  }
} finally {
  await browser.close();
}
