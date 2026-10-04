// @vitest-environment node
// #2430 â€” a live-but-busy backend must never be reported as a failure.
//
// The supervisor already knew better: it checked `exitCode`/`signalCode`
// before touching the stage precisely because "inference can monopolize
// Python's event loop longer than the health deadline". It then published the
// terminal `failed` stage anyway. The renderer acts on `failed` by replacing the
// workspace with an error gate, pausing every query and dead-ending in-flight
// requests â€” for a stall that the very next probe clears on its own. That is how
// a long voice-clone job took the app down mid-generation.
//
// These pin both halves of the contract: a LIVE child that stops answering is
// `unresponsive` (non-terminal, self-recovering, never killed), while a child
// that is actually gone still reports `crashed`.
import { EventEmitter } from 'node:events';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ spawn: vi.fn(), spawnSync: vi.fn() }));
vi.mock('electron', () => ({ app: { isPackaged: true, getPath: () => '/unused-supervise-test' } }));
vi.mock('node:child_process', () => ({ spawn: mocks.spawn, spawnSync: mocks.spawnSync }));
// `availableBackendPort` preflights the preferred port; a clean bind means the
// managed launch keeps 3900.
vi.mock('node:net', () => ({
  createServer: () => {
    const server = Object.assign(new EventEmitter(), {
      listen: (_options: unknown, done: () => void) => queueMicrotask(done),
      address: () => ({ port: 39152 }),
      close: (done: () => void) => done(),
    });
    return server;
  },
}));
vi.mock('./runtime-project', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./runtime-project')>()),
  runtimeReady: async () => true,
  runtimeCompatible: async () => true,
  runtimeDependenciesReady: async () => true,
  stageRuntimeSources: async () => {},
  runtimePython: () => '/runtime/python',
  installRuntime: vi.fn(),
  promoteLegacyRuntimeCaches: vi.fn(async () => {}),
  runtimeInstallInterrupted: vi.fn(async () => false),
}));
import { BackendSupervisor } from './backend';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

/** `down` is a cold port, `busy` a blocked event loop, `up` an answering backend. */
type Health = { mode: 'down' | 'busy' | 'up' };

/** /health answers only in `up`. */
function stubHealth(health: Health): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      if (health.mode !== 'up') throw new Error('no answer');
      return new Response(JSON.stringify({ status: 'ok', version: 'test' }), {
        headers: { 'x-omnivoice-backend': 'test' },
      });
    }),
  );
}

/** A spawned child that is alive: no exit code, no signal, nothing to kill. */
function liveChild() {
  return Object.assign(new EventEmitter(), {
    stdin: null,
    stdout: null,
    stderr: null,
    stdio: [],
    pid: 4242,
    exitCode: null as number | null,
    signalCode: null as string | null,
  });
}

it('reports a live-but-busy backend as unresponsive, not failed (#2430)', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  vi.stubEnv('OMNIVOICE_PORT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  vi.stubEnv('OMNIVOICE_STARTUP_BUDGET_S', '60');
  const health: Health = { mode: 'down' };
  stubHealth(health);
  const child = liveChild();
  mocks.spawn.mockReturnValue(child);

  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    // Cold port: nothing to attach to, so the shell owns the process.
    expect(mocks.spawn).toHaveBeenCalledOnce();

    health.mode = 'up';
    await vi.advanceTimersByTimeAsync(1_000);
    expect(supervisor.status.stage).toBe('ready');
    expect(supervisor.status.managed).toBe(true);

    // A heavy job blocks the event loop past the probe deadline.
    health.mode = 'busy';
    // SUPERVISE_POLL_MS (2 s) x SUPERVISE_MISSES (3), plus a tick of margin.
    await vi.advanceTimersByTimeAsync(7_000);

    // The whole bug: this used to be `failed`.
    expect(supervisor.status.stage).toBe('unresponsive');
    expect(supervisor.status.message).toMatch(/busy/i);
    // Still the same live, managed process â€” observed, never killed.
    expect(supervisor.status.managed).toBe(true);
    expect(child.exitCode).toBeNull();
    expect(mocks.spawnSync).not.toHaveBeenCalled();

    // The job finishes: the health loop retires the stage on its own.
    health.mode = 'up';
    await vi.advanceTimersByTimeAsync(3_000);
    expect(supervisor.status.stage).toBe('ready');
    expect(supervisor.status.message).toBeUndefined();
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('still reports a backend that is genuinely gone as crashed', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  vi.stubEnv('OMNIVOICE_PORT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  const health: Health = { mode: 'up' };
  stubHealth(health);

  // No child to inspect: an attached/external backend that stops answering is
  // indistinguishable from one that died, so it must still surface as `crashed`.
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(supervisor.status.stage).toBe('ready');
    expect(mocks.spawn).not.toHaveBeenCalled();

    health.mode = 'busy';
    await vi.advanceTimersByTimeAsync(7_000);
    expect(supervisor.status.stage).toBe('crashed');
  } finally {
    await supervisor.shutdown();
  }
});
