// @vitest-environment node
import { EventEmitter } from 'node:events';
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

/**
 * #2445 — "Backend did not answer on port 3900 within 300 s".
 *
 * The readiness budget covers the backend. The launch that precedes it — the
 * per-candidate interpreter import probe, staging the bundled sources and port
 * selection — all run before the process exists, so charging that time to the
 * backend left it seconds of its own window and then killed it.
 */
const mocks = vi.hoisted(() => ({
  spawn: vi.fn(),
  listen: vi.fn(),
  close: vi.fn(),
  /** Fake-clock milliseconds each pre-spawn step burns. */
  prespawnMs: 0,
  /** Ports the bind preflight reports as denied (EACCES) or taken (EADDRINUSE). */
  denied: new Set<number>(),
  occupied: new Set<number>(),
  /** False simulates an unbuilt runtime, so start() lands on setup_required. */
  runtimeReadyNow: true,
}));

vi.mock('electron', () => ({ app: { isPackaged: true, getPath: () => '/unused-budget-test' } }));
vi.mock('node:child_process', () => ({ spawn: mocks.spawn }));
vi.mock('node:net', () => ({
  createServer: () => {
    const server = Object.assign(new EventEmitter(), {
      listen: (options: { port: number }, done: () => void) => {
        mocks.listen(options.port);
        const code = mocks.denied.has(options.port)
          ? 'EACCES'
          : mocks.occupied.has(options.port)
            ? 'EADDRINUSE'
            : null;
        if (code) {
          queueMicrotask(() =>
            server.emit('error', Object.assign(new Error('bind failed'), { code })),
          );
        } else queueMicrotask(done);
        return server;
      },
      address: () => ({ port: 49152 }),
      close: (done: () => void) => {
        mocks.close();
        done();
      },
    });
    return server;
  },
}));

vi.mock('./runtime-project', () => {
  // Both pre-spawn steps are genuinely slow on a cold, scanner-contended
  // install, and both complete before the backend process exists.
  const burn = async (): Promise<void> => {
    if (mocks.prespawnMs > 0) await vi.advanceTimersByTimeAsync(mocks.prespawnMs);
  };
  return {
    resolveTorchVariant: () => ({ variant: 'cpu', explicit: false }),
    cpuTorchApplies: () => false,
    runtimeReady: async () => mocks.runtimeReadyNow,
    runtimeCompatible: async () => false,
    runtimeDependenciesReady: async () => {
      await burn();
      return mocks.runtimeReadyNow;
    },
    runtimeInstallInterrupted: async () => false,
    // Stands in for uv sync: the real installer spawns a child, so its output
    // lands in the same ring the backend's does.
    installRuntime: async (
      _bundle: string,
      _project: string,
      _uv: string | null,
      run: (
        command: string,
        args: string[],
        cwd: string,
        env: NodeJS.ProcessEnv,
      ) => Promise<string>,
    ) => {
      mocks.runtimeReadyNow = true;
      await run('uv', ['sync'], '/unused-budget-test', {});
    },
    stageRuntimeSources: burn,
    runtimePython: () => '/runtime/python',
  };
});

import {
  BackendSupervisor,
  LOG_READ_TIMEOUT_MS,
  readBackendLogTail,
  setBackendOnlyEnv,
  startupTimeoutMessage,
} from './backend';
import { scrubText } from '../shared/utils/scrub';
import { MAX_MSG_CHARS } from '../shared/utils/bugReportDocument';

const BUDGET_S = '10';

/** A spawned process that is alive but not answering yet — a cold backend boot. */
function fakeChild() {
  const stdout = Object.assign(new EventEmitter(), { setEncoding: () => {} });
  return {
    child: Object.assign(new EventEmitter(), {
      stdin: null,
      stdout,
      stderr: null,
      stdio: [],
    }),
    stdout,
  };
}

function stubBackend(ready: () => boolean): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      if (!ready()) throw new Error('not listening yet');
      return new Response(JSON.stringify({ status: 'ok', version: 'test' }), {
        headers: { 'x-omnivoice-backend': 'test' },
      });
    }),
  );
}

function stubEnv(): void {
  vi.stubEnv('OMNIVOICE_PORT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  vi.stubEnv('OMNIVOICE_STARTUP_BUDGET_S', BUDGET_S);
}

/**
 * A fresh, empty data dir per test, so a silent failure never reads the
 * developer's real omnivoice.log. Tests that need log content write their own
 * `omnivoice.log` inside `dataDir` before starting the supervisor.
 */
const tempDirs: string[] = [];
let dataDir = '';

function stubDataDir(): void {
  dataDir = mkdtempSync(join(tmpdir(), 'omnivoice-budget-'));
  tempDirs.push(dataDir);
  vi.stubEnv('OMNIVOICE_DATA_DIR', dataDir);
}

/**
 * The silent-timeout report reads the backend log asynchronously; real file
 * I/O finishes outside the fake clock, so let it land before asserting.
 */
async function untilFailed(supervisor: { status: { stage: string } }): Promise<void> {
  for (let i = 0; i < 200 && supervisor.status.stage !== 'failed'; i++) {
    await new Promise((done) => setImmediate(done));
    await vi.advanceTimersByTimeAsync(50);
  }
}

beforeEach(() => {
  // setupRuntime() refuses an Intel Mac outright (#2365), so these fixtures
  // would never spawn on a darwin/x64 runner. Pin a supported host.
  if (process.platform === 'darwin') vi.spyOn(process, 'arch', 'get').mockReturnValue('arm64');
  stubDataDir();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  mocks.prespawnMs = 0;
  mocks.denied.clear();
  mocks.occupied.clear();
  mocks.runtimeReadyNow = true;
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

it('gives a freshly spawned backend its whole budget after slow pre-launch work', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  // Two pre-spawn steps at 25 s each against a 10 s budget: the launch alone is
  // 5x the entire readiness window, and it finishes before the process exists.
  mocks.prespawnMs = 25_000;
  let ready = false;
  stubBackend(() => ready);
  const { child } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    expect(mocks.spawn).toHaveBeenCalledTimes(1);
    // The backend was spawned moments ago; the elapsed launch must not have
    // already spent its budget and killed it.
    await vi.advanceTimersByTimeAsync(0);
    expect(supervisor.status.stage).toBe('starting');
    await vi.advanceTimersByTimeAsync(9_000);
    expect(supervisor.status.stage).toBe('starting');
    ready = true;
    await vi.advanceTimersByTimeAsync(500);
    expect(supervisor.status.stage).toBe('ready');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('still fails the backend once it has had its full budget', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  let ready = false;
  stubBackend(() => ready);
  const { child } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(9_000);
    expect(supervisor.status.stage).toBe('starting');
    await vi.advanceTimersByTimeAsync(2_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain('did not answer on port 3900');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it.each([
  ['silent', null, "It printed no output. The backend's own log"],
  [
    'noisy',
    'RuntimeError: no module named torch\r\n',
    'Last output: RuntimeError: no module named torch',
  ],
])('carries the backend %s into the budget-expiry message', async (_label, line, expected) => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  stubBackend(() => false);
  const { child, stdout } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    if (line) stdout.emit('data', line);
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain(expected);
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('quotes the backend log tail when a silent backend wrote one', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  writeFileSync(
    join(dataDir, 'omnivoice.log'),
    ['INFO: loading torch', 'INFO: loading transformers', 'WARNING: slow disk'].join('\n'),
  );
  stubBackend(() => false);
  const { child } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain('did not answer on port 3900');
    expect(supervisor.status.message).toContain('INFO: loading torch');
    expect(supervisor.status.message).toContain('WARNING: slow disk');
    expect(supervisor.status.message).not.toContain('Last output:');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('names the backend log path when a silent backend has no log file', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  stubBackend(() => false);
  const { child } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain(scrubText(join(dataDir, 'omnivoice.log')));
    expect(supervisor.status.message).toContain('is missing, empty, or unreadable');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('scrubs a home directory out of the quoted backend log tail', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  writeFileSync(
    join(dataDir, 'omnivoice.log'),
    'loaded /home/alice/.omnivoice/models/model.bin\nINFO: done\n',
  );
  stubBackend(() => false);
  const { child } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain('loaded');
    expect(supervisor.status.message).not.toContain('alice');
    expect(supervisor.status.message).not.toContain('/home/alice');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

async function silentTimeoutMessage(): Promise<string> {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  stubBackend(() => false);
  const { child } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    return supervisor.status.message ?? '';
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
}

it('quotes the log of the data dir the backend was spawned with, not the shell env', async () => {
  // Packaged and scoped installs set OMNIVOICE_DATA_DIR only in the child's env.
  const spawnData = mkdtempSync(join(tmpdir(), 'omnivoice-spawn-data-'));
  tempDirs.push(spawnData);
  writeFileSync(join(spawnData, 'omnivoice.log'), 'INFO: from the spawned data dir\n');
  writeFileSync(join(dataDir, 'omnivoice.log'), 'INFO: from the shell data dir\n');
  setBackendOnlyEnv({ OMNIVOICE_DATA_DIR: spawnData });
  try {
    const message = await silentTimeoutMessage();
    expect(message).toContain('INFO: from the spawned data dir');
    expect(message).not.toContain('INFO: from the shell data dir');
  } finally {
    setBackendOnlyEnv({ OMNIVOICE_DATA_DIR: undefined });
  }
});

it('does not quote a log last written before this launch started', async () => {
  const log = join(dataDir, 'omnivoice.log');
  writeFileSync(log, 'INFO: a previous launch\n');
  const anHourAgo = new Date(Date.now() - 3_600_000);
  utimesSync(log, anHourAgo, anHourAgo);
  const message = await silentTimeoutMessage();
  expect(message).not.toContain('INFO: a previous launch');
  expect(message).toContain('has nothing new since this launch started');
});

it('reads only the end of a long log', async () => {
  const filler = 'x'.repeat(200);
  const lines = Array.from({ length: 2_000 }, (_, i) => `INFO: line ${i} ${filler}`);
  writeFileSync(join(dataDir, 'omnivoice.log'), lines.join('\n'));
  const message = await silentTimeoutMessage();
  expect(message).toContain('INFO: line 1999 ');
  expect(message).not.toContain('INFO: line 0 ');
});

it('keeps the newest log lines and the advice within what a bug report quotes', () => {
  const tail = Array.from({ length: 40 }, (_, i) => `INFO: line ${i} ${'x'.repeat(80)}`);
  const message = startupTimeoutMessage(3900, 300_000, {
    owned: true,
    logPath: '~/.omnivoice/omnivoice.log',
    logTail: tail,
  });
  expect(message.length).toBeLessThanOrEqual(MAX_MSG_CHARS);
  expect(message).toContain('INFO: line 39 ');
  expect(message).not.toContain('INFO: line 0 ');
  expect(message).toMatch(/did not answer on port 3900/);
  expect(message).toMatch(/raise OMNIVOICE_STARTUP_BUDGET_S \(for example 900\)\.$/);
});

it('resolves a relative data dir against the backend working directory', async () => {
  const cwd = mkdtempSync(join(tmpdir(), 'omnivoice-cwd-'));
  tempDirs.push(cwd);
  mkdirSync(join(cwd, 'rel-data'));
  writeFileSync(join(cwd, 'rel-data', 'omnivoice.log'), 'INFO: relative data dir\n');
  const log = await readBackendLogTail({
    env: { OMNIVOICE_DATA_DIR: 'rel-data' },
    cwd,
    startedAt: 0,
  });
  expect(log.tail).toEqual(['INFO: relative data dir']);
});

it('gives up on a log read that stalls, without holding up the report', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const pending = readBackendLogTail(
    { env: { OMNIVOICE_DATA_DIR: dataDir }, cwd: dataDir, startedAt: 0 },
    { read: () => new Promise(() => undefined) },
  );
  await vi.advanceTimersByTimeAsync(LOG_READ_TIMEOUT_MS);
  await expect(pending).resolves.toEqual({
    path: scrubText(join(dataDir, 'omnivoice.log')),
    tail: [],
    stale: false,
  });
});

it('reports the log path when a directory sits where the log should be', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  mkdirSync(join(dataDir, 'omnivoice.log'), { recursive: true });
  stubBackend(() => false);
  const { child } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain(scrubText(join(dataDir, 'omnivoice.log')));
    expect(supervisor.status.message).toContain('is missing, empty, or unreadable');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

/**
 * A launch owns its own diagnostics. `childLog` is a ring of process output,
 * and the previous launch — or the runtime installer, whose uv children share
 * the same reader — leaves lines behind. A backend that then dies silently
 * must not be handed someone else's last word.
 */
it('does not quote the previous backend after a restart', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  stubBackend(() => false);
  const first = fakeChild();
  mocks.spawn.mockReturnValue(first.child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    first.stdout.emit('data', 'previous run: CUDA out of memory\r\n');
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain('previous run: CUDA out of memory');

    // The replacement fails silently, so it must not inherit that line.
    mocks.spawn.mockReturnValue(fakeChild().child);
    await supervisor.restart();
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain('It printed no output.');
    expect(supervisor.status.message).not.toContain('previous run: CUDA out of memory');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('blames no backend for an attach-only wait that times out', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  stubBackend(() => false);
  const first = fakeChild();
  mocks.spawn.mockReturnValue(first.child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    first.stdout.emit('data', 'previous run: CUDA out of memory\r\n');
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');

    // Deny the default port so port selection falls through to 4900, which
    // answers the identity probe but never the health one. That is the
    // attach-only wait: a finite budget, and no process of ours to quote.
    mocks.denied.add(3900);
    mocks.occupied.add(4900);
    let identified = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (!url.includes(':4900') || ++identified > 1) throw new Error('never ready');
        return new Response(JSON.stringify({ status: 'starting' }), {
          status: 503,
          headers: { 'x-omnivoice-backend': 'test' },
        });
      }),
    );
    await supervisor.restart();
    expect(mocks.spawn).toHaveBeenCalledTimes(1);
    expect(supervisor.port).toBe(4900);
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain('Nothing was spawned for this attempt.');
    expect(supervisor.status.message).not.toContain('previous run: CUDA out of memory');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('does not quote the installer after a completed runtime setup', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  stubBackend(() => false);
  mocks.runtimeReadyNow = false;
  // First spawn is uv inside installRuntime, second is the real backend.
  let spawns = 0;
  mocks.spawn.mockImplementation(() => {
    spawns++;
    const made = fakeChild();
    if (spawns === 1)
      queueMicrotask(() => {
        made.stdout.emit('data', 'uv: installed 412 packages\r\n');
        made.child.emit('close', 0);
      });
    return made.child;
  });
  const supervisor = new BackendSupervisor();
  try {
    // No runtime yet, so the first launch asks for setup.
    await supervisor.start();
    expect(supervisor.status.stage).toBe('setup_required');
    await supervisor.setupRuntime();
    expect(mocks.spawn).toHaveBeenCalledTimes(2);
    expect(supervisor.status.stage).toBe('starting');
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain('It printed no output.');
    expect(supervisor.status.message).not.toContain('uv: installed 412 packages');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

/**
 * #2445 - a slow host (no dedicated GPU, few cores, a scanner touching every
 * native library) can still be booting well past the nominal budget. A backend
 * that is demonstrably alive and printing is not stalled, so it keeps its place
 * until it goes quiet - but never past three budgets in total.
 */
it('keeps waiting for a backend that is still printing past the budget', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  let ready = false;
  stubBackend(() => ready);
  const { child, stdout } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    for (const at of [4_000, 8_000, 12_000]) {
      await vi.advanceTimersByTimeAsync(4_000);
      stdout.emit('data', `INFO: still migrating at ${at}\r\n`);
    }
    // 12 s is past the 10 s budget, yet the backend spoke a moment ago.
    expect(supervisor.status.stage).toBe('starting');
    ready = true;
    await vi.advanceTimersByTimeAsync(500);
    expect(supervisor.status.stage).toBe('ready');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('fails a backend that printed once and then went quiet, at the plain budget', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  stubBackend(() => false);
  const { child, stdout } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    stdout.emit('data', 'INFO: loading\r\n');
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('never extends a chatty backend past three budgets', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  stubBackend(() => false);
  const { child, stdout } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    for (let t = 0; t < 40_000 && supervisor.status.stage === 'starting'; t += 1_000) {
      stdout.emit('data', 'INFO: chatter\r\n');
      await vi.advanceTimersByTimeAsync(1_000);
    }
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('doubles the default budget on a small host', async () => {
  const { defaultStartupBudgetS } = await import('./backend');
  const GiB = 1024 ** 3;
  expect(defaultStartupBudgetS(16, 32 * GiB)).toBe(300);
  expect(defaultStartupBudgetS(4, 32 * GiB)).toBe(600);
  expect(defaultStartupBudgetS(16, 8 * GiB)).toBe(600);
  expect(defaultStartupBudgetS(2, 4 * GiB)).toBe(600);
});

// Review (Greptile P1, security): the last backend line is quoted in the failure
// message, which the Fix action forwards to a repair-agent CLI.
it('scrubs credentials and home directories from the quoted backend output', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  stubEnv();
  stubBackend(() => false);
  const { child, stdout } = fakeChild();
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    stdout.emit(
      'data',
      `auth failed with hf_${'A'.repeat(34)} reading C:\\Users\\alice\\AppData\\model.bin\r\n`,
    );
    await vi.advanceTimersByTimeAsync(11_000);
    await untilFailed(supervisor);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toContain('Last output: auth failed');
    expect(supervisor.status.message).not.toContain('hf_AAAA');
    expect(supervisor.status.message).not.toContain('alice');
    expect(supervisor.status.logTail.join('\n')).not.toContain('alice');
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('keeps the end of one log line too long to quote whole', () => {
  const line = `INFO: start ${'x'.repeat(3_000)} RuntimeError: CUDA driver too old`;
  const message = startupTimeoutMessage(3900, 300_000, {
    owned: true,
    logPath: '~/.omnivoice/omnivoice.log',
    logTail: [line],
  });
  expect(message.length).toBeLessThanOrEqual(MAX_MSG_CHARS);
  expect(message).toContain('RuntimeError: CUDA driver too old');
  expect(message).not.toContain('is missing, empty, or unreadable');
  expect(message).toMatch(/raise OMNIVOICE_STARTUP_BUDGET_S \(for example 900\)\.$/);
});

it('never splits an emoji when it shortens a log line', () => {
  const line = `INFO: ${'\u{1F600}'.repeat(2_000)}`;
  const message = startupTimeoutMessage(3900, 300_000, {
    owned: true,
    logPath: '~/.omnivoice/omnivoice.log',
    logTail: [line],
  });
  expect(message.length).toBeLessThanOrEqual(MAX_MSG_CHARS);
  expect(message).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/);
});
