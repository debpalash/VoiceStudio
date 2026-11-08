import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { startHealthBackend } from './fake-health-backend.mjs';
import { routesForShard } from './crawler-shards.mjs';
import { waitForQuietRequests } from './crawler-settle.mjs';

/**
 * Clicks every enabled button on every static route, once per backend fault mode, and fails on
 * the ways a click goes wrong without any scripted assertion noticing:
 *  - stuck: a spinner, progress bar or aria-busy region the click started is still showing after
 *    every API request it triggered has finished (the "button stuck loading" class)
 *  - disabled: a fault mode failed the click's write, every request settled, and a button that
 *    was enabled before the click is still disabled (the "button stuck greyed out" class)
 *  - silent: a fault mode failed the click's write and the screen gave no feedback at all (no
 *    new toast or alert, no changed text), so the user sees a button that "does nothing"
 *  - crash: an uncaught exception or unhandled promise rejection
 *  - console: a console.error the click caused
 *  - unsettled: API traffic never gives the UI a full quiet grace within the request budget
 *  - frozen: the renderer stops answering after the click
 *
 * Fault modes apply to API writes (anything but GET/HEAD) issued after the click, so every button
 * must recover from a failed request instead of only the happy path:
 *  - fixture: the shared fake backend (unmocked writes answer 404)
 *  - http-500: writes answer 500 with a FastAPI-style detail
 *  - network-error: writes fail at the network layer
 *  - bad-json: writes answer 200 with a body that is not JSON
 *
 * A canary screen with a deliberately stuck button and a throwing button runs first, so a
 * detector that has gone blind fails the smoke instead of passing it.
 *
 * Run it against `smoke:server` like the renderer smokes; with OMNIVOICE_PORT set it serves that
 * port from the shared fake backend itself.
 *
 * Env: VOICESTUDIO_UI_URL, VOICESTUDIO_CRAWL_MODES (comma list), VOICESTUDIO_CRAWL_ROUTES
 * (comma list of path substrings), VOICESTUDIO_CRAWL_SHARD (`i/n`: the i-th of n cost-balanced
 * route sets, for parallel CI jobs; see crawler-shards.mjs), VOICESTUDIO_CRAWL_VERBOSE=1 to log every click.
 */

const BASE = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const MODES = ['fixture', 'http-500', 'network-error', 'bad-json'];
const modes = (process.env.VOICESTUDIO_CRAWL_MODES || MODES.join(',')).split(',').filter(Boolean);
const routeFilter = (process.env.VOICESTUDIO_CRAWL_ROUTES || '').split(',').filter(Boolean);
const [shard, shards] = (process.env.VOICESTUDIO_CRAWL_SHARD || '1/1').split('/').map(Number);
assert.ok(shard >= 1 && shard <= shards, 'VOICESTUDIO_CRAWL_SHARD must look like 1/3');
const verbose = process.env.VOICESTUDIO_CRAWL_VERBOSE === '1';

/** Longest a click's API traffic may stay in flight before the button is judged stuck. */
const REQUEST_BUDGET_MS = 8_000;
/** Grace after the traffic settles for React to drop its loading state. */
const SETTLE_MS = 400;
/** Chromium logs every failed fetch itself; the app did nothing wrong by making the request. */
const BROWSER_NETWORK_LOG = /^Failed to load resource:/;
const FEEDBACK = '[data-sonner-toast], [role="alert"], [role="alertdialog"]';
const BUSY = '[aria-busy="true"], .animate-spin, [role="progressbar"]:not([aria-valuenow])';
const CLICKABLE = ':is(button, [role="button"]):not([disabled]):not([aria-disabled="true"])';
const isWrite = (request) => !['GET', 'HEAD', 'OPTIONS'].includes(request.method());
const isApi = (request) => new URL(request.url()).pathname.startsWith('/api/');

/**
 * Buttons the crawler must not click, keyed by accessible name. Each entry needs a reason; an
 * unexplained skip hides exactly the regressions this smoke exists to catch.
 */
export const SKIPPED_BUTTONS = {};

/**
 * Findings that are understood and tracked. Key: `${mode} ${route} ${button} ${kind}`; value: the
 * reason or issue link. Keep this empty — fix the button instead of listing it.
 */
export const KNOWN_FINDINGS = {};

function faultResponse(mode) {
  if (mode === 'http-500')
    return { status: 500, json: { detail: 'Injected failure from ui-crawler-smoke' } };
  if (mode === 'bad-json')
    return { status: 200, contentType: 'application/json', body: '<html>not json' };
  return null;
}

async function openSession(browser, mode) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(() => {
    window.__crawlErrors = [];
    window.addEventListener('unhandledrejection', (event) => {
      const reason = event.reason;
      window.__crawlErrors.push(
        `unhandledrejection: ${reason?.stack || reason?.message || reason}`,
      );
    });
    // Popups and sandboxed frames have an opaque origin where localStorage throws.
    try {
      localStorage.setItem('voicestudio.setup.complete.v1', '1');
    } catch {}
  });
  const state = {
    faulting: false,
    failedWrites: 0,
    inflight: new Set(),
    activity: 0,
    events: [],
  };
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    // Local-first: nothing a click triggers may leave the machine.
    if (url.hostname !== 'localhost' && url.hostname !== '127.0.0.1') return route.abort();
    if (!state.faulting || !isWrite(request) || !isApi(request)) return route.fallback();
    if (mode === 'network-error') return route.abort('failed');
    const response = faultResponse(mode);
    return response ? route.fulfill(response) : route.fallback();
  });

  const page = await context.newPage();
  const countFailure = (request) => {
    if (state.faulting && isWrite(request) && isApi(request)) state.failedWrites += 1;
  };
  page.on('response', (response) => {
    if (response.status() >= 400 || mode === 'bad-json') countFailure(response.request());
  });
  page.on('requestfailed', countFailure);
  page.on('request', (request) => {
    if (isApi(request) && request.resourceType() !== 'eventsource') {
      state.inflight.add(request);
      state.activity += 1;
      recent.push(request);
      if (recent.length > 6) recent.shift();
    }
  });
  // Name what kept the page busy, so an unsettled finding points at its request.
  const recent = [];
  const describeTraffic = () => {
    const names = [...new Set([...state.inflight, ...recent].map((r) => `${r.method()} ${new URL(r.url()).pathname}`))];
    return names.length ? `: ${names.slice(0, 6).join(', ')}` : '';
  };
  const finished = (request) => {
    if (!state.inflight.delete(request)) return;
    state.activity += 1;
  };
  page.on('requestfinished', finished);
  page.on('requestfailed', finished);
  page.on('pageerror', (error) =>
    state.events.push({ kind: 'crash', detail: error.stack || error.message }),
  );
  page.on('console', (message) => {
    if (message.type() === 'error' && !BROWSER_NETWORK_LOG.test(message.text()))
      state.events.push({ kind: 'console', detail: message.text() });
  });
  page.on('dialog', (dialog) => dialog.accept().catch(() => {}));
  context.on('page', (popup) => popup !== page && popup.close().catch(() => {}));

  const settle = (grace = SETTLE_MS, snapshot) => waitForQuietRequests(state, {
    wait: (duration) => page.waitForTimeout(duration),
    graceMs: grace,
    budgetMs: REQUEST_BUDGET_MS,
    snapshot,
  });
  const navigate = (to) =>
    page.evaluate(async (target) => {
      // Import once per page load: re-importing after a Vite hot update can catch the
      // router/routes cycle mid-evaluation ("Cannot access 'routeTree' before initialization").
      window.__crawlRouter ??= (await import('/src/router.tsx')).router;
      await window.__crawlRouter.navigate({ to: target });
    }, to);
  const buttons = () =>
    page.locator(`main ${CLICKABLE}, [role="dialog"] ${CLICKABLE}`).filter({ visible: true });

  /** Clear per-click evidence; feedback already on screen is marked so only new feedback counts. */
  const arm = async () => {
    await settle(100);
    state.events.length = 0;
    state.failedWrites = 0;
    recent.length = 0;
    await page.evaluate((feedback) => {
      window.__crawlErrors.length = 0;
      for (const element of document.querySelectorAll(feedback)) element.dataset.crawlSeen = '1';
    }, FEEDBACK);
  };

  /** Visible loading indicators, counted with Playwright's own visibility rules. */
  const busy = () => page.locator(BUSY).filter({ visible: true }).count();

  /** Click one button and return the kinds of failure it showed, with details. */
  const clickAndCheck = async (button, route) => {
    // Independent reads share one round trip.
    const [before] = await Promise.all([
      busy(),
      page.evaluate((clickable) => {
        for (const element of document.querySelectorAll(`main ${clickable}`))
          element.dataset.crawlEnabled = '1';
        window.__crawlText = document.querySelector('main')?.innerText ?? '';
      }, CLICKABLE),
    ]);
    state.faulting = true;
    const clicked = await button
      .click({ timeout: 2_000 })
      .then(() => true)
      .catch(() => false);
    if (!clicked) {
      state.faulting = false;
      return null; // covered by an overlay or detached mid-click; not a finding
    }
    let observation;
    try {
      observation = await settle(SETTLE_MS, busy);
    } finally {
      state.faulting = false;
    }
    const problems = [];
    const failedWrite = Boolean(state.failedWrites) && mode !== 'fixture' && observation.settled;
    // One read gathers the evidence and proves the renderer still answers.
    const evidence = !observation.unresponsive && await Promise.race([
      page.evaluate(({ failedWrite, feedback }) => {
        const result = { errors: window.__crawlErrors.splice(0), greyed: [], feedback: true };
        if (!failedWrite) return result;
        result.greyed = [...document.querySelectorAll('main [data-crawl-enabled]')]
          .filter((element) => element.matches('[disabled], [aria-disabled="true"]'))
          .map((element) =>
            (element.textContent || element.getAttribute('aria-label') || '?').trim(),
          );
        result.feedback =
          [...document.querySelectorAll(feedback)].some((node) => !node.dataset.crawlSeen) ||
          (document.querySelector('main')?.innerText ?? '') !== window.__crawlText;
        return result;
      }, { failedWrite, feedback: FEEDBACK }),
      new Promise((resolve) => setTimeout(() => resolve(false), 5_000)),
    ]);
    if (!evidence) {
      await page.goto(`${BASE}/#${route}`);
      return [{ kind: 'frozen', detail: observation.unresponsive
        ? 'renderer did not answer before the request budget expired'
        : 'renderer did not answer within 5s' }];
    }
    if (!observation.settled)
      problems.push({ kind: 'unsettled', detail: `API traffic did not stay quiet within ${REQUEST_BUDGET_MS}ms (${observation.pending} request(s) pending${describeTraffic()})` });
    const after = observation.snapshot;
    if (observation.settled && after > before && new URL(page.url()).hash === `#${route}`)
      problems.push({
        kind: 'stuck',
        detail: `${after - before} loading indicator(s) left after all requests settled`,
      });
    if (evidence.greyed.length)
      problems.push({
        kind: 'disabled',
        detail: `still disabled after the failed write settled: ${evidence.greyed.join(', ').slice(0, 200)}`,
      });
    if (!evidence.feedback)
      problems.push({
        kind: 'silent',
        detail: `${state.failedWrites} failed write(s) and no visible feedback`,
      });
    for (const error of evidence.errors) problems.push({ kind: 'crash', detail: error });
    problems.push(...state.events.splice(0));
    return problems;
  };

  await page.goto(`${BASE}/#/settings/usage`);
  await page.locator('main').first().waitFor();
  await settle();
  return { context, page, navigate, buttons, arm, clickAndCheck };
}

const label = (locator) =>
  locator.evaluate((element) =>
    (
      element.getAttribute('aria-label') ||
      element.textContent ||
      element.getAttribute('title') ||
      element.getAttribute('data-slot') ||
      element.tagName
    )
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 60),
  );

/** Prove each detector still fires on a planted defect before trusting a clean crawl. */
async function canary(browser) {
  const session = await openSession(browser, 'http-500');
  const { page, arm, clickAndCheck } = session;
  const route = '/settings/usage';
  await page.evaluate(() => {
    const main = document.querySelector('main');
    const add = (name, onClick) => {
      const button = document.createElement('button');
      button.textContent = name;
      button.addEventListener('click', onClick);
      main.prepend(button);
      return button;
    };
    add('canary-reload', () => {});
    add('canary-late-request', () => {
      setTimeout(() => {
        const spinner = document.createElement('span');
        spinner.className = 'animate-spin';
        spinner.textContent = '.';
        main.append(spinner);
        // A background poll can start during the detector's React grace.
        // The view recovers within that grace, measured from the response.
        void fetch('/api/health').finally(() => setTimeout(() => spinner.remove(), 300));
      }, 300);
    });
    add('canary-stuck', () => {
      const spinner = document.createElement('span');
      spinner.className = 'animate-spin';
      spinner.textContent = '.';
      main.append(spinner);
      void fetch('/api/canary', { method: 'POST' }).catch(() => {});
    });
    add('canary-disabled', (event) => {
      event.currentTarget.disabled = true;
      main.append('working');
      void fetch('/api/canary', { method: 'POST' }).catch(() => {});
    });
    add('canary-silent', () => void fetch('/api/canary', { method: 'POST' }).catch(() => {}));
    add('canary-crash', () => Promise.reject(new Error('canary rejection')));
  });
  const kinds = async (name) => {
    await arm();
    const result = await clickAndCheck(page.getByRole('button', { name, exact: true }), route);
    return (result ?? []).map((problem) => problem.kind);
  };
  assert.deepEqual(
    await kinds('canary-late-request'),
    [],
    'recovering late request was mistaken for a stuck spinner',
  );
  assert.ok((await kinds('canary-stuck')).includes('stuck'), 'stuck detector went blind');
  assert.ok((await kinds('canary-silent')).includes('silent'), 'silent detector went blind');
  assert.ok((await kinds('canary-disabled')).includes('disabled'), 'disabled detector went blind');
  assert.ok((await kinds('canary-crash')).includes('crash'), 'crash detector went blind');
  // Playwright can reject the post-click DOM snapshot when a reload replaces
  // its execution context. Recovery writes must not inherit this click's fault.
  await arm();
  const reloadError = new Error('Execution context was destroyed during reload');
  const locate = page.locator.bind(page);
  let busyReads = 0;
  page.locator = (selector, ...args) => selector !== BUSY ? locate(selector, ...args) : {
    filter: (options) => ({
      count: async () => {
        if (++busyReads === 2) throw reloadError;
        return locate(selector, ...args).filter(options).count();
      },
    }),
  };
  try {
    await assert.rejects(
      clickAndCheck(page.getByRole('button', { name: 'canary-reload', exact: true }), route),
      (error) => error === reloadError,
      'snapshot reload must preserve the original recovery error',
    );
  } finally {
    page.locator = locate;
  }
  const recoveryStatus = await page.evaluate(async () =>
    (await fetch('/api/health', { method: 'POST' })).status,
  );
  assert.equal(recoveryStatus, 200, 'snapshot failure leaked fault injection into recovery');
  await session.context.close();
}

/** A valid, silent 0.1 s mono WAV, so audio pickers accept the fixture. */
function silentWav() {
  const samples = 1600;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0);
  wav.writeUInt32LE(36 + samples * 2, 4);
  wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16_000, 24);
  wav.writeUInt32LE(32_000, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write('data', 36);
  wav.writeUInt32LE(samples * 2, 40);
  return wav;
}

function fixtureFor(accept) {
  if (/audio|\.wav|\.mp3/.test(accept))
    return { name: 'crawl.wav', mimeType: 'audio/wav', buffer: silentWav() };
  if (/video|\.mp4/.test(accept))
    return { name: 'crawl.mp4', mimeType: 'video/mp4', buffer: Buffer.from('fixture') };
  if (/json/.test(accept))
    return { name: 'crawl.json', mimeType: 'application/json', buffer: Buffer.from('{}') };
  return {
    name: 'crawl.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Hello from the UI crawler.'),
  };
}

/**
 * Fill the screen's form so the buttons it gates (Generate, Clone, Dub...) become clickable.
 * Search boxes are left alone: filtering would hide the very rows we want to click.
 */
async function fillForm(page) {
  await page.evaluate(() => {
    for (const button of document.querySelectorAll('main :is(button, [role="button"])'))
      if (button.matches('[disabled], [aria-disabled="true"]')) button.dataset.crawlGated = '1';
  });
  const fields = page
    .locator('main :is(textarea, input:not([type]), input[type="text"], input[type="url"])')
    .filter({ visible: true });
  for (let index = 0; index < (await fields.count()); index += 1) {
    const field = fields.nth(index);
    if (!(await field.isEditable().catch(() => false)) || (await field.inputValue()) !== '')
      continue;
    const url = (await field.getAttribute('type')) === 'url';
    await field
      .fill(url ? 'http://localhost/crawl.mp4' : 'Hello from the UI crawler.', { timeout: 1_000 })
      .catch(() => {});
  }
  const files = page.locator('main input[type="file"]');
  for (let index = 0; index < (await files.count()); index += 1) {
    const input = files.nth(index);
    const accept = (await input.getAttribute('accept')) ?? '';
    await input.setInputFiles(fixtureFor(accept), { timeout: 1_000 }).catch(() => {});
  }
}

async function crawlMode(browser, mode, routes) {
  const session = await openSession(browser, mode);
  const { context, navigate, buttons, arm, clickAndCheck, page } = session;
  /** A click or upload that reloads the whole page (e.g. "Reload app") is legitimate. */
  const RELOADED = /Execution context was destroyed|navigat|Target .*closed|detached/i;
  const recover = async (route, error) => {
    if (verbose)
      console.log(`[${mode}] ${route} recovering after: ${error.message.split('\n')[0]}`);
    // The click already started a reload; a goto that only changes the hash would never commit
    // while it is pending, so let it finish and route inside the fresh app instead.
    await page.waitForLoadState('domcontentloaded').catch(() => {});
    if (!(await page.locator('main').first().isVisible().catch(() => false)))
      await page.goto(`${BASE}/?recover=${Date.now()}#/settings/usage`, { waitUntil: 'commit' });
    await page.locator('main').first().waitFor({ timeout: 15_000 });
    await navigate(route);
  };
  // Remount the route so every click starts from the same fresh screen.
  const reset = async (route) => {
    try {
      for (let i = 0; i < 3 && (await page.locator('[role="dialog"]').count()); i += 1)
        await page.keyboard.press('Escape');
      await navigate('/settings/usage');
      await navigate(route);
    } catch (error) {
      if (!RELOADED.test(error.message)) throw error;
      await recover(route, error);
    }
    await arm();
  };
  const dialogs = () => page.locator('[role="dialog"]').filter({ visible: true });
  const gated = () =>
    page
      .locator(`main [data-crawl-gated]${CLICKABLE.slice(CLICKABLE.indexOf(')') + 1)}`)
      .filter({ visible: true });
  const dialogButtons = () =>
    page.locator(`[role="dialog"] ${CLICKABLE}`).filter({ visible: true });
  const findings = [];
  let clicks = 0;
  let unclickable = 0;
  let reloads = 0;

  /** One click from a prepared screen; `prepare` rebuilds the screen and returns the target. */
  const visit = (route, prefix, prepare) =>
    visitOnce(route, prefix, prepare).catch(async (error) => {
      if (!RELOADED.test(error.message)) throw error;
      reloads += 1;
      await recover(route, error);
      return null;
    });
  const visitOnce = async (route, prefix, prepare) => {
    await reset(route);
    const button = await prepare();
    if (!button || !(await button.count())) return null; // the screen changed shape
    const name = `${prefix}${await label(button).catch(() => '?')}`;
    if (Object.hasOwn(SKIPPED_BUTTONS, name.split(' > ').at(-1))) return null;
    const openDialogs = await dialogs().count();
    await arm();
    const problems = await clickAndCheck(button, route);
    if (!problems) {
      unclickable += 1;
      return null;
    }
    clicks += 1;
    if (verbose) console.log(`[${mode}] ${route} :: ${name}`);
    for (const { kind, detail } of problems) {
      const key = `${mode} ${route} ${name} ${kind}`;
      if (!Object.hasOwn(KNOWN_FINDINGS, key)) findings.push({ key, detail });
    }
    return { opensDialog: (await dialogs().count()) > openDialogs };
  };

  const seconds = {};
  for (const route of routes) {
    const started = performance.now();
    // Pass 1: every enabled button on the fresh screen.
    await reset(route);
    const openers = [];
    for (let index = 0, total = await buttons().count(); index < total; index += 1) {
      const result = await visit(route, '', async () => buttons().nth(index));
      if (result?.opensDialog) openers.push(index);
    }
    // Pass 2: buttons the empty form kept disabled, clicked with the form filled in.
    await reset(route);
    await fillForm(page);
    for (let index = 0, total = await gated().count(); index < total; index += 1)
      await visit(route, '(filled) ', async () => {
        await fillForm(page);
        return gated().nth(index);
      });
    // Pass 3: one level into each dialog a button opened.
    for (const opener of openers) {
      await reset(route);
      const openerName = await label(buttons().nth(opener)).catch(() => '?');
      await buttons()
        .nth(opener)
        .click({ timeout: 2_000 })
        .catch(() => {});
      for (let index = 0, total = await dialogButtons().count(); index < total; index += 1)
        await visit(route, `${openerName} > `, async () => {
          const button = buttons().nth(opener);
          await button.click({ timeout: 2_000 }).catch(() => {});
          await dialogs()
            .first()
            .waitFor({ timeout: 2_000 })
            .catch(() => {});
          return dialogButtons().nth(index);
        });
    }
    seconds[route] = (performance.now() - started) / 1000;
  }
  await context.close();
  return { mode, clicks, unclickable, reloads, findings, seconds };
}

const backendPort = Number(process.env.OMNIVOICE_PORT) || 0;
const backend = backendPort ? await startHealthBackend({ port: backendPort }) : null;
const browser = await chromium.launch({
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  headless: true,
  // Recording buttons get a fake microphone instead of a permission rejection.
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
});
try {
  await canary(browser);
  const probe = await browser.newPage();
  await probe.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
  await probe.goto(`${BASE}/#/settings/usage`);
  await probe.locator('main').first().waitFor();
  const allRoutes = [
    ...new Set(
      await probe.evaluate(async () => {
        const { router } = await import('/src/router.tsx');
        return Object.values(router.routesById).map((route) => route.fullPath);
      }),
    ),
  ]
    .filter((path) => path !== '/' && !path.includes('$'))
    .filter((path) => !routeFilter.length || routeFilter.some((part) => path.includes(part)))
    .sort();
  const routes = routesForShard(allRoutes, shard, shards);
  await probe.close();

  // Settle every mode before reporting: one mode's harness error must not close the shared
  // browser under the others and bury their results.
  const settled = await Promise.allSettled(modes.map((mode) => crawlMode(browser, mode, routes)));
  let failed = 0;
  const results = settled.map((outcome, index) =>
    outcome.status === 'fulfilled'
      ? outcome.value
      : {
          mode: modes[index],
          clicks: 0,
          unclickable: 0,
          reloads: 0,
          findings: [{ key: `${modes[index]} harness error`, detail: outcome.reason?.stack }],
        },
  );
  for (const { mode, clicks, unclickable, reloads, findings } of results) {
    console.log(
      `${findings.length ? 'FAIL' : 'PASS'}  ${mode}: ${clicks} clicks (${unclickable} covered, ` +
        `${reloads} reloaded) ` +
        `on ${routes.length} routes, ${findings.length} finding(s)`,
    );
    for (const { key, detail } of findings)
      console.log(`  ${key}\n    ${String(detail).split('\n').slice(0, 3).join('\n    ')}`);
    failed += findings.length;
  }
  // Per-route cost (slowest mode), for re-balancing ROUTE_WEIGHTS in crawler-shards.mjs.
  const cost = (route) => Math.max(...results.map((result) => result.seconds?.[route] ?? 0));
  if (routes.some(cost))
    console.log(
      `Route seconds: ${[...routes].sort((a, b) => cost(b) - cost(a))
        .map((route) => `${route} ${cost(route).toFixed(1)}`).join(', ')}`,
    );
  process.exitCode = failed ? 1 : 0;
} finally {
  await browser.close();
  await backend?.close();
}
