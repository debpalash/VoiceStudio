#!/usr/bin/env node
// Electron launch timing and idle CPU, against a backend that is already up.
// Normally driven by bench.py, which starts an isolated backend and passes its
// port; it can also run alone against any local backend:
//
//   node scripts/bench/app_start.mjs --port 3900 [--runs 5] [--idle-window 30]
//        [--app /Applications/VoiceStudio.app/Contents/MacOS/VoiceStudio]
//
// Each launch uses one throwaway profile (seeded once as "setup complete", in
// English), so the numbers are a returning user's launch, not first-run setup.
// The app runs with the updater off and without managing a backend, and every
// renderer request to a non-local host is aborted, so a run never goes online.
// HOME points inside the throwaway profile, so the packaged app's terminal
// command setup cannot touch ~/.local/bin or shell profiles.
// Prints JSON on stdout; progress on stderr.
import { _electron as electron } from 'playwright';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { launchEnv } from './launch_env.mjs';

const repo = resolve(import.meta.dirname, '../..');
const option = (name, fallback) => {
  const index = process.argv.indexOf(name);
  return index > -1 ? process.argv[index + 1] : fallback;
};
const port = Number(option('--port'));
const runs = Number(option('--runs', 5));
const idleWindowS = Number(option('--idle-window', 30));
const SETTLE_MS = 5000;
const packaged = option('--app');
if (!port) throw new Error('--port <backend port> is required');

function devElectron() {
  const dist = join(repo, 'electron/node_modules/electron/dist');
  if (process.platform === 'win32') return join(dist, 'electron.exe');
  if (process.platform === 'darwin') return join(dist, 'Electron.app/Contents/MacOS/Electron');
  return join(dist, 'electron');
}

const percentile = (values, q) => {
  const sorted = [...values].sort((a, b) => a - b);
  const rank = ((sorted.length - 1) * q) / 100;
  const low = Math.floor(rank);
  const high = Math.min(low + 1, sorted.length - 1);
  return sorted[low] + (sorted[high] - sorted[low]) * (rank - low);
};
const round = (n) => Math.round(n * 1000) / 1000;
const summarize = (values) => ({
  median: round(percentile(values, 50)),
  p90: round(percentile(values, 90)),
  min: round(Math.min(...values)),
  max: round(Math.max(...values)),
  n: values.length,
});
const isLocal = (url) =>
  !/^(https?|wss?):$/.test(url.protocol) || ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);

async function launch(profile, seed) {
  const startedAt = Date.now();
  const app = await electron.launch({
    executablePath: packaged ?? devElectron(),
    args: [...(packaged ? [] : [join(repo, 'electron/out/main/index.js')]), `--user-data-dir=${profile}`],
    env: launchEnv(process.env, { port, profile }),
    timeout: 60000,
  });
  const context = app.context();
  await context.route((url) => !isLocal(url), (route) => route.abort());
  if (seed) {
    await context.addInitScript(() => {
      localStorage.setItem('voicestudio.setup.complete.v1', '1');
      localStorage.setItem('voicestudio.locale', 'en');
    });
  }
  const page = await app.firstWindow();
  return { app, page, startedAt };
}

/** Epoch ms when a heading reading exactly `Home` is in the DOM (the studio, backend ready). */
const homeShown = (page) =>
  page
    .waitForFunction(
      () =>
        [...document.querySelectorAll('h1, h2, h3, [role=heading]')].some(
          (h) => h.textContent?.trim() === 'Home',
        ) && Date.now(),
      null,
      { polling: 'raf', timeout: 120000 },
    )
    .then((handle) => handle.jsonValue());

/** Epoch ms of the first contentful paint of the current document. */
const firstPaint = (page) =>
  page
    .waitForFunction(
      () => {
        const fcp = performance.getEntriesByName('first-contentful-paint')[0];
        return fcp ? performance.timeOrigin + fcp.startTime : false;
      },
      null,
      { timeout: 60000 },
    )
    .then((handle) => handle.jsonValue());

const metrics = (app) =>
  app.evaluate(({ app: electronApp }) =>
    electronApp.getAppMetrics().map((m) => ({ type: m.type, pid: m.pid, cpu: m.cpu.percentCPUUsage })),
  );

const GROUPS = { Browser: 'main', Tab: 'renderer', GPU: 'gpu' };

async function main() {
  const profile = mkdtempSync(join(tmpdir(), 'vs-bench-profile-'));
  const result = { app: packaged ?? 'electron/out (unpackaged)', runs: [] };
  try {
    // Seed the profile: setup marked complete, so later launches open the studio.
    {
      const { app, page, startedAt } = await launch(profile, true);
      result.fresh_profile_first_paint_ms = (await firstPaint(page)) - startedAt;
      await page.reload();
      await homeShown(page);
      await app.close();
    }
    for (let index = 0; index < runs; index += 1) {
      const { app, page, startedAt } = await launch(profile, false);
      try {
        const run = {
          first_paint_ms: (await firstPaint(page)) - startedAt,
          home_ms: (await homeShown(page)) - startedAt,
        };
        await page.waitForTimeout(SETTLE_MS);
        await metrics(app); // starts the averaging window
        await page.waitForTimeout(idleWindowS * 1000);
        const cpu = { main: 0, renderer: 0, gpu: 0, other: 0 };
        for (const m of await metrics(app)) cpu[GROUPS[m.type] ?? 'other'] += m.cpu;
        cpu.total = cpu.main + cpu.renderer + cpu.gpu + cpu.other;
        run.idle_cpu_percent = cpu;
        result.runs.push(run);
        process.stderr.write(
          `  app run ${index + 1}/${runs}: paint ${run.first_paint_ms} ms, home ${run.home_ms} ms, ` +
            `idle cpu main ${cpu.main.toFixed(1)}% renderer ${cpu.renderer.toFixed(1)}%\n`,
        );
      } finally {
        await app.close().catch(() => {});
      }
    }
  } finally {
    rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
  result.first_paint_ms = summarize(result.runs.map((r) => r.first_paint_ms));
  result.home_ms = summarize(result.runs.map((r) => r.home_ms));
  result.idle_window_s = idleWindowS;
  result.idle_cpu_percent = Object.fromEntries(
    ['main', 'renderer', 'gpu', 'other', 'total'].map((k) => [
      k,
      summarize(result.runs.map((r) => r.idle_cpu_percent[k])),
    ]),
  );
  console.log(JSON.stringify(result, null, 2));
}

await main();
