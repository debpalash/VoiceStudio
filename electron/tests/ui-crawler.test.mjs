import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ROUTE_WEIGHTS, routesForShard } from './crawler-shards.mjs';
import { waitForQuietRequests } from './crawler-settle.mjs';
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
