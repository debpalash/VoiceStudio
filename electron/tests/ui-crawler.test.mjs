import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ROUTE_WEIGHTS, routesForShard } from './crawler-shards.mjs';
import { createTraffic, stalledClick, stalledRoute, tracksRequest, waitForQuietRequests } from './crawler-settle.mjs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = dirname(fileURLToPath(import.meta.url));
const crawler = readFileSync(join(dir, 'ui-crawler.mjs'), 'utf8');
const ci = readFileSync(join(dir, '../../.github/workflows/ci.yml'), 'utf8');

const job = ci.match(/\n {2}ui-crawler:\n([\s\S]*?)\n {2}[\w-]+:\n/)[1];

test('CI crawls every fault mode, split only by route shard', () => {
  // The crawler runs the modes concurrently itself. A mode x shard matrix queued twelve jobs per
  // push behind the runner limit, so the only axis is the route shard, kept at three.
  assert.doesNotMatch(job, /matrix\.mode/, 'shard by route only, never by fault mode');
  assert.match(job, /shard: \[1, 2, 3\]/);
  assert.match(job, /VOICESTUDIO_CRAWL_SHARD: \$\{\{ matrix\.shard \}\}\/3/);
  // Narrowing the modes or routes in CI would silently skip the faults this job exists for.
  assert.doesNotMatch(job, /VOICESTUDIO_CRAWL_(MODES|ROUTES)/);
  assert.match(crawler, /const MODES = \['fixture', 'http-500', 'network-error', 'bad-json'\]/);
});

test('each shard leaves room for a full crawl', () => {
  const minutes = Number(job.match(/timeout-minutes: (\d+)/)[1]);
  assert.ok(minutes >= 30, `timeout-minutes ${minutes} is too short for every mode and route`);
});

test('crawler findings are fixed, not allowlisted', () => {
  assert.match(crawler, /export const KNOWN_FINDINGS = \{\};/);
});


test('the route shards cover every route exactly once and share the cost evenly', () => {
  const routes = [...Object.keys(ROUTE_WEIGHTS), ...Array.from({ length: 24 }, (_, i) => `/route-${i}`)];
  const owned = [1, 2, 3].map((shard) => routesForShard(routes, shard, 3));
  assert.deepEqual(owned.flat().sort(), [...routes].sort(), 'each route is crawled by exactly one shard');
  const cost = owned.map((set) => set.reduce((sum, route) => sum + (ROUTE_WEIGHTS[route] ?? 1), 0));
  assert.ok(Math.max(...cost) - Math.min(...cost) <= 14, `shard costs ${cost} are too uneven`);
  assert.ok(owned.every((set) => set.length), 'no shard is left empty');
});

test('a route without a weight still lands on a shard', () => {
  const owned = [1, 2, 3].map((shard) => routesForShard(['/new-route'], shard, 3));
  assert.equal(owned.flat().length, 1);
});

function clock(events = [], options = {}) {
  let time = 0;
  const traffic = { inflight: new Set(), activity: 0 };
  const change = (kind) => {
    traffic.activity += 1;
    if (kind === 'start') traffic.inflight.add('request');
    else traffic.inflight.delete('request');
  };
  return {
    traffic, change,
    time: () => time,
    run: () => waitForQuietRequests(traffic, {
      graceMs: 400, budgetMs: 1000, now: () => time,
      wait: async (duration) => {
        time += duration;
        while (events.length && events[0][0] <= time) change(events.shift()[1]);
      },
      ...options,
    }),
  };
}

test('quiet traffic gets the React grace without extending the budget', async () => {
  const run = clock();
  assert.equal((await run.run()).settled, true);
  assert.equal(run.time(), 400);
});

test('a late response gets a full grace window after it completes', async () => {
  const run = clock([[300, 'start'], [350, 'finish']]);
  assert.equal((await run.run()).settled, true);
  assert.equal(run.time(), 750);
});

test('a request completed between samples still restarts the quiet window', async () => {
  const run = clock([[275, 'start'], [280, 'finish']]);
  assert.equal((await run.run()).settled, true);
  assert.equal(run.time(), 700);
});

test('traffic during the DOM snapshot invalidates that snapshot', async () => {
  let snapshots = 0;
  const run = clock([], { snapshot: async () => {
    snapshots += 1;
    if (snapshots === 1) { run.change('start'); run.change('finish'); }
    return snapshots;
  } });
  const result = await run.run();
  assert.equal(result.settled, true);
  assert.equal(result.snapshot, 2);
  assert.equal(run.time(), 800);
});

test('a permanently pending request exhausts the original budget', async () => {
  const run = clock([[100, 'start']]);
  assert.deepEqual(await run.run(), { settled: false, pending: 1 });
  assert.equal(run.time(), 1000);
});

test('continuous completed polls cannot turn deadline exhaustion into success', async () => {
  const events = Array.from({ length: 10 }, (_, index) => [[index * 100 + 75, 'start'], [index * 100 + 80, 'finish']]).flat();
  const run = clock(events);
  assert.deepEqual(await run.run(), { settled: false, pending: 0 });
  assert.equal(run.time(), 1000);
});


test('a frozen DOM snapshot cannot outlive the request budget', { timeout: 1000 }, async () => {
  const result = await waitForQuietRequests({ inflight: new Set(), activity: 0 }, {
    graceMs: 0,
    budgetMs: 10,
    wait: (duration) => new Promise((resolve) => setTimeout(resolve, duration)),
    snapshot: () => new Promise(() => {}),
  });
  assert.deepEqual(result, { settled: false, pending: 0, unresponsive: true });
});


test('a snapshot completed after the deadline is still unresponsive', async () => {
  let time = 0;
  const result = await waitForQuietRequests({ inflight: new Set(), activity: 0 }, {
    graceMs: 0, budgetMs: 1000, now: () => time,
    wait: async () => assert.fail('no wait is needed before this snapshot'),
    snapshot: async () => { time = 1001; return 0; },
  });
  assert.deepEqual(result, { settled: false, pending: 0, unresponsive: true });
});

test('a reload ends the old document\'s requests, which Playwright never reports', async () => {
  // "Reset and reload" on /settings/storage reloaded the page while two polls were in flight;
  // they stayed pending forever and every later click in the shard waited out its budget.
  const traffic = createTraffic();
  traffic.start('GET /api/model/status');
  traffic.start('GET /api/batch/jobs');
  traffic.start('document', { navigation: true });
  const before = traffic.activity;
  traffic.committed();
  assert.equal(traffic.inflight.size, 0);
  assert.ok(traffic.activity > before, 'dropping requests restarts the quiet window');
  traffic.start('GET /api/engines');
  traffic.finish('GET /api/model/status'); // a late event for a dropped request is harmless
  assert.deepEqual([...traffic.inflight], ['GET /api/engines'], 'the new document is still tracked');
  let time = 0;
  const result = await waitForQuietRequests(
    (traffic.finish('GET /api/engines'), traffic),
    { graceMs: 400, budgetMs: 1000, now: () => time, wait: async (ms) => { time += ms; } },
  );
  assert.equal(result.settled, true);
});

test('route changes inside the app keep their requests in flight', () => {
  const traffic = createTraffic();
  traffic.start('GET /api/engines');
  traffic.committed(); // hash navigation: no navigation request came first
  assert.equal(traffic.inflight.size, 1);
  traffic.start('document', { navigation: true });
  traffic.navigationFailed(); // e.g. a download: the document stays
  traffic.finish('document');
  traffic.committed();
  assert.equal(traffic.inflight.size, 1);
});

test('a request pending past the stall limit names what started it', () => {
  let time = 0;
  const traffic = createTraffic({ now: () => time });
  traffic.cause = 'the click on /settings/storage Reset and reload';
  traffic.start('GET /api/model/status');
  time = 5_000;
  traffic.cause = 'opening /stories';
  traffic.start('GET /api/engines');
  time = 29_999;
  assert.equal(traffic.stalled(30_000), null);
  time = 31_000;
  assert.deepEqual(traffic.stalled(30_000), {
    request: 'GET /api/model/status',
    document: 0,
    at: 0,
    cause: 'the click on /settings/storage Reset and reload',
    ageMs: 31_000,
  });
});

test('only a click whose own wait failed can stop the mode', () => {
  let time = 0;
  const traffic = createTraffic({ now: () => time });
  traffic.start('GET /api/engines');
  time = 45_000; // held through setup and pre-click waits, which a mode may still pass after
  assert.equal(stalledClick({ settled: true, pending: 0 }, traffic, 30_000), null);
  assert.equal(stalledClick({ settled: false, pending: 1 }, traffic, 30_000).request, 'GET /api/engines');
  // The pre-click waits (openSession, reset, arm) never judge a stall; only the click's does.
  assert.equal(crawler.match(/stalledClick\(/g).length, 1);
  assert.match(crawler, /observation = await settle\(SETTLE_MS, busy\);[\s\S]{0,120}stalledClick\(observation, traffic, STALLED_MS\)/);
  assert.match(crawler, /const settle = \(grace = SETTLE_MS, snapshot\) => waitForQuietRequests\(/);
  assert.match(crawler, /page\.on\('framenavigated', \(frame\) => frame === page\.mainFrame\(\) && traffic\.committed\(\)\)/);
  assert.match(crawler, /error instanceof CrawlStalled\) && RELOAD_ERROR/, 'a stall is never mistaken for a reload');
});

test('a hang no click judged still fails the mode after its route', () => {
  let time = 0;
  const traffic = createTraffic({ now: () => time });
  traffic.start('GET /api/model/status'); // every click on the route missed: no click wait ran
  traffic.start('GET /api/engines');
  time = 31_000;
  traffic.finish('GET /api/engines'); // settled during a click: never reported
  assert.equal(stalledRoute(traffic, 30_000).request, 'GET /api/model/status');
  traffic.finish('GET /api/model/status');
  assert.equal(stalledRoute(traffic, 30_000), null);
  assert.match(crawler, /await crawlRoute\(route\);[\s\S]{0,160}await failStalled\(stalledRoute\(traffic, STALLED_MS\)\);/);
});

test('a dialog opener clicked outside visit() still names itself as the cause', () => {
  const pass3 = crawler.slice(crawler.indexOf('// Pass 3'));
  const named = pass3.match(/traffic\.cause = `the click on \$\{route\} \$\{openerName\}`;\s*(?:await buttons\(\)|const button = buttons\(\))/g);
  assert.equal(named?.length, 2, 'both opener clicks set the cause first');
});

test('lazily loaded code counts toward the quiet window, event streams do not', () => {
  // A screen still downloading modules holds the connections the next screen's API reads need,
  // so module loads must settle before the crawler moves on (/settings/openapi -> /sharing).
  const origin = 'http://localhost:3912';
  assert.ok(tracksRequest(`${origin}/api/system/info`, 'fetch', origin));
  assert.ok(tracksRequest(`${origin}/@fs/repo/node_modules/@scalar/icons/x.js`, 'script', origin));
  assert.ok(tracksRequest(`${origin}/src/features/settings/a.css`, 'stylesheet', origin));
  assert.equal(tracksRequest(`${origin}/api/events`, 'eventsource', origin), false);
  assert.equal(tracksRequest('https://tailscale.com/download', 'document', origin), false);
});

test('dev servers proxy /api itself, not app paths that start with it', () => {
  // Settings > API (Scalar) moves the page to /api-reference/...; a bare '/api' proxy prefix sent
  // the next reload of that page to the backend, which answered with a 404 instead of the app.
  for (const config of ['../electron.vite.config.ts', '../vite.web.config.ts', 'renderer-smoke.vite.config.mjs']) {
    const source = readFileSync(join(dir, config), 'utf8');
    assert.doesNotMatch(source, /^\s*'\/api': \{/m, config);
    const key = source.match(/^\s*'(\^\/api[^']*)': \{/m)?.[1];
    assert.ok(key, `${config} has no /api proxy`);
    const proxied = new RegExp(key);
    for (const url of ['/api', '/api/engines', '/api?x=1']) assert.match(url, proxied, `${config} ${url}`);
    for (const url of ['/api-reference/description/introduction', '/apis', '/src/api/client.ts'])
      assert.doesNotMatch(url, proxied, `${config} ${url}`);
  }
});
