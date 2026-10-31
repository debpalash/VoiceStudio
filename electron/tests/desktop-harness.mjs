import { _electron as electron } from 'playwright';
import { execFileSync } from 'node:child_process';
import {
  appendFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
} from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { DEFAULT_FIXTURES, startHealthBackend } from './fake-health-backend.mjs';
import { forceAndWaitForExit } from './desktop-harness-exit.mjs';
import { isAllowedRequest, STAR_COUNT_STUB, STAR_COUNT_URL } from './local-first.mjs';

const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
export const ARTIFACT_DIR = join(workspaceRoot, 'electron/test-results', process.platform);

/**
 * Console errors tolerated across a run. Each entry needs a comment saying why the app
 * legitimately emits it; an unexplained entry hides real regressions.
 */
export const ALLOWED_CONSOLE_ERRORS = [];

export function electronExecutable() {
  const dist = join(workspaceRoot, 'electron/node_modules/electron/dist');
  const executable =
    process.platform === 'win32'
      ? join(dist, 'electron.exe')
      : process.platform === 'darwin'
        ? join(dist, 'Electron.app/Contents/MacOS/Electron')
        : join(dist, 'electron');
  if (!existsSync(executable)) {
    throw new Error(
      `Electron binary missing at ${executable}. Run: node electron/node_modules/electron/install.js`,
    );
  }
  return executable;
}

/** Latin non-ASCII plus a space, so path-encoding bugs in user-data handling surface per OS. */
export const makeProfile = () => mkdtempSync(join(tmpdir(), 'VoiceStudio Tëst '));

/** A loopback port with nothing listening on it. */
export const freePort = () =>
  new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolvePort(port));
    });
  });

/** True when an app launch died because a port chosen by `freePort()` was taken meanwhile. */
export const isPortConflict = (error) => /EADDRINUSE/.test(String(error?.message ?? error));

/**
 * `freePort()` can only report a port that was free a moment ago: between closing its
 * probe and Electron binding the port, another process (a parallel test, the previous
 * app still shutting down) can take it. Retry the launch with fresh ports rather than
 * failing a test for a collision that has nothing to do with it.
 */
export async function launchWithFreshPorts(launchOnce, nextPorts, attempts = 3) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await launchOnce();
    } catch (error) {
      if (attempt >= attempts || !isPortConflict(error)) throw error;
      await nextPorts();
    }
  }
}

const slug = (name) =>
  name
    .replace(/[^a-z0-9]+/gi, '-')
    .replace(/^-|-$/g, '')
    .toLowerCase();

async function launch({ profile, backendPort, seed }) {
  // VS Code terminals export ELECTRON_RUN_AS_NODE=1, which makes Electron boot as plain Node.
  const { ELECTRON_RUN_AS_NODE: _runAsNode, ...inherited } = process.env;
  const app = await electron.launch({
    executablePath: electronExecutable(),
    args: [
      // Preloaded into the main process before the app's own code, so no request precedes it.
      '-r',
      join(workspaceRoot, 'electron/tests/fixtures/request-guard.cjs'),
      join(workspaceRoot, 'electron/out/main/index.js'),
      '--user-data-dir=' + profile,
    ],
    env: {
      ...inherited,
      ELECTRON_RENDERER_URL: '',
      OMNIVOICE_PORT: String(backendPort),
      VOICESTUDIO_SKIP_BACKEND: '1',
      VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: '1',
      VOICESTUDIO_DISABLE_UPDATER: '1',
      // The OS assigns it at bind time; a pre-picked port can be taken before the app binds it.
      VOICESTUDIO_ELECTRON_PROXY_PORT: '0',
    },
    timeout: 60000,
  });
  // Playwright disposes its process channel when Electron closes. Keep the
  // actual child so post-crash guards can inspect exit state without that channel.
  const child = app.process();
  const output = [];
  for (const stream of [child.stdout, child.stderr]) {
    stream?.on('data', (chunk) => output.push(String(chunk)));
  }
  const context = app.context();
  await context.tracing.start({ screenshots: true, snapshots: true });
  await context.route(STAR_COUNT_URL, (route) => route.fulfill({ json: STAR_COUNT_STUB }));
  if (seed) {
    // Only fill gaps: this runs on every page load and must not undo what a test persisted.
    await context.addInitScript(() => {
      try {
        for (const [key, value] of [
          ['voicestudio.setup.complete.v1', '1'],
          ['voicestudio.locale', 'en'],
        ]) {
          if (localStorage.getItem(key) === null) localStorage.setItem(key, value);
        }
      } catch {
        // Some documents (data: URLs, the blank-window fallback) have no storage access.
      }
    });
  }
  let page;
  try {
    page = await app.firstWindow();
  } catch (error) {
    await app.close().catch(() => {});
    // The app died before showing a window: its own output is the only evidence of why.
    let journal = '(none)';
    try {
      journal = readFileSync(join(profile, 'main-process-errors.json'), 'utf8');
    } catch {
      // No journal was written.
    }
    throw new Error(
      `${error.message}\n--- app output ---\n${output.join('').slice(-4000)}\n--- main-process-errors.json ---\n${journal}`,
      { cause: error },
    );
  }
  const errors = [];
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    if (!ALLOWED_CONSOLE_ERRORS.some((pattern) => pattern.test(text))) {
      errors.push(`console: ${text}`);
    }
  });
  // The main-process guard predates the app; one reload also replays the first page's console.
  await page.reload();
  return { app, child, page, errors };
}

/**
 * Runs `body` against a freshly launched desktop app and enforces the run-wide guards:
 * every request stays local, no renderer errors, no main-process errors. On failure the
 * trace, a screenshot and the app logs are saved to ARTIFACT_DIR/<test name>/.
 * Planned exits use session.crash() to capture guards before the process dies.
 */
export async function withApp(t, options, body) {
  const { profile: given, fixtures, seed = true, backend: backendMode = 'up' } = options;
  const profile = given ?? makeProfile();
  // Like the real backend, report a data directory (inside the profile) for path authorization.
  const dataDir = join(profile, 'data');
  mkdirSync(dataDir, { recursive: true });
  const backend =
    backendMode === 'up'
      ? await startHealthBackend({
          fixtures: {
            '/system/info': {
              ...DEFAULT_FIXTURES['/system/info'],
              app_version: 'desktop-ui-test',
              data_dir: dataDir,
            },
            ...fixtures,
          },
        })
      : null;
  let backendPort = backend ? backend.port : await freePort();
  const live = [];
  const session = { profile, backend, relaunch: null, crash: null };
  const start = async () => {
    // With the dev proxy on an OS-assigned port, the backend port is the only one
    // picked ahead of time, so it is the only one worth re-picking on a collision.
    const started = await launchWithFreshPorts(
      () => launch({ profile, backendPort, seed }),
      async () => {
        backendPort = await freePort();
      },
      backend ? 1 : 3,
    );
    live.push(started);
    Object.assign(session, started);
    return session;
  };
  session.crash = async () => {
    const started = live.at(-1);
    // The crashed process cannot return its request log afterward.
    await collectGuards(started);
    const signal = AbortSignal.timeout(30_000);
    started.expectedExit = true;
    await forceAndWaitForExit(started.child, started.app, () => {
      if (process.platform === 'win32') {
        // Playwright launches through cmd.exe. Kill the entire tree and verify
        // both the wrapper exit and Electron close, even if taskkill races a child.
        execFileSync('taskkill.exe', ['/PID', String(started.child.pid), '/T', '/F'], {
          windowsHide: true,
          timeout: 10_000,
        });
      } else started.child.kill('SIGKILL');
    }, signal);
  };
  session.relaunch = async () => {
    const previous = live.at(-1);
    await collectGuards(previous);
    // Closing an app that was killed mid-test rejects; the process is gone either way.
    await previous.app.close().catch(() => {});
    return start();
  };
  const violations = [];
  const errors = [];
  let unobserved = 0;
  async function collectGuards(started) {
    errors.push(...started.errors);
    const { child } = started;
    if (child.exitCode !== null || child.signalCode !== null) {
      assert.equal(started.expectedExit, true, 'Desktop process exited unexpectedly');
      // Planned crashes already collected this launch's guards while it was alive.
      return;
    }
    const seen = await started.app.evaluate(() => globalThis.__requestLog);
    // An empty log means the guard never ran (hook missing or replaced), not that all is well.
    if (!seen.some((url) => url.startsWith('app:'))) unobserved += 1;
    // The guard already left out the renderer's stubbed star-count request.
    violations.push(...seen.filter((url) => !isAllowedRequest(url)));
  }
  try {
    await start();
    await body(session);
    await collectGuards(live.at(-1));
    assert.deepEqual(violations, [], 'Requests left the machine');
    assert.deepEqual(errors, [], 'Renderer reported errors');
    assert.equal(
      unobserved,
      0,
      'Request guard observed no app requests (hook missing or replaced)',
    );
    const journal = join(profile, 'main-process-errors.json');
    if (existsSync(journal)) {
      const entries = JSON.parse(readFileSync(journal, 'utf8'));
      assert.deepEqual(entries.length ? entries : [], [], 'Main process journaled errors');
    }
  } catch (error) {
    // Written synchronously first: a hard crash during cleanup loses anything still buffered.
    breadcrumb(t.name, `FAILED: ${error?.stack ?? error}`);
    await saveArtifacts(t.name, live.at(-1), profile).catch(() => {});
    throw error;
  } finally {
    breadcrumb(t.name, 'closing app');
    for (const started of live) await started.app.close().catch(() => {});
    breadcrumb(t.name, 'closing backend');
    await backend?.close();
    breadcrumb(t.name, 'removing profile');
    // Windows keeps the profile locked briefly after exit; cleanup must never mask the real result.
    if (!given) {
      try {
        rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
      } catch {
        // A leftover temp directory is harmless; a swallowed test failure is not.
      }
    }
    breadcrumb(t.name, 'cleanup done');
  }
}

/** Synchronous progress log in the artifact directory; survives a crash of this process. */
function breadcrumb(name, message) {
  try {
    mkdirSync(ARTIFACT_DIR, { recursive: true });
    appendFileSync(join(ARTIFACT_DIR, 'breadcrumbs.log'), `[${name}] ${message}\n`);
  } catch {
    // Diagnostics only.
  }
}

async function saveArtifacts(name, started, profile) {
  if (!started) return;
  const dir = join(ARTIFACT_DIR, slug(name));
  mkdirSync(dir, { recursive: true });
  await started.page.screenshot({ path: join(dir, 'final.png') }).catch(() => {});
  await started.app.context().tracing.stop({ path: join(dir, 'trace.zip') });
  const journal = join(profile, 'main-process-errors.json');
  if (existsSync(journal)) cpSync(journal, join(dir, 'main-process-errors.json'));
}
