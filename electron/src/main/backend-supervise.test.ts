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
vi.mock('./runtime-project', () => ({
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
  calls.length = 0;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

/**
 * `down` is a closed port (the connection is refused), `busy` a blocked event
 * loop (the kernel still accepts, so the probe's deadline fires), `up` an
 * answering backend.
 */
type Health = {
  mode: 'down' | 'busy' | 'rejected' | 'up';
  /** The remote's authenticated /system/info answers 401 (expired session). */
  authRejected?: boolean;
  /** The authenticated /system/info neither accepts nor rejects: it times out or 5xx. */
  authInconclusive?: 'timeout' | 'server_error';
};

const calls: string[] = [];

/** /health answers only in `up`. */
function stubHealth(health: Health): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown) => {
      calls.push(String(input));
      if (health.mode === 'busy') throw new DOMException('timed out', 'TimeoutError');
      if (health.mode === 'down') throw new TypeError('fetch failed');
      if (health.mode === 'rejected') {
        return new Response('{}', { status: 503, headers: { 'x-omnivoice-backend': 'test' } });
      }
      if (String(input).endsWith('/system/info')) {
        if (health.authInconclusive === 'timeout') {
          throw new DOMException('timed out', 'TimeoutError');
        }
        if (health.authInconclusive === 'server_error') return new Response('{}', { status: 503 });
        if (health.authRejected) return new Response('{}', { status: 401 });
      }
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

async function attachedSupervisor(health: Health): Promise<BackendSupervisor> {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  vi.stubEnv('OMNIVOICE_PORT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  stubHealth(health);
  // No child to inspect: a backend that was already running is attached to.
  const supervisor = new BackendSupervisor();
  await supervisor.start();
  await vi.advanceTimersByTimeAsync(1_000);
  expect(supervisor.status.stage).toBe('ready');
  expect(mocks.spawn).not.toHaveBeenCalled();
  return supervisor;
}

it('still reports an attached backend that refuses connections as crashed', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await attachedSupervisor(health);
  try {
    health.mode = 'down';
    await vi.advanceTimersByTimeAsync(7_000);
    expect(supervisor.status.stage).toBe('crashed');
    expect(supervisor.status.message).toMatch(/stopped answering/);
  } finally {
    await supervisor.shutdown();
  }
});

it('waits out an attached backend that accepts connections but is busy (#2601)', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await attachedSupervisor(health);
  try {
    // A long generation blocks the external backend's event loop. It used to
    // be declared crashed ~10 s in, although the process was fine.
    health.mode = 'busy';
    await vi.advanceTimersByTimeAsync(60_000);
    expect(supervisor.status.stage).toBe('unresponsive');
    expect(supervisor.status.message).toMatch(/busy/i);

    health.mode = 'up';
    await vi.advanceTimersByTimeAsync(3_000);
    expect(supervisor.status.stage).toBe('ready');
  } finally {
    await supervisor.shutdown();
  }
});

it('never declares an attached backend crashed from timeouts alone', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await attachedSupervisor(health);
  try {
    health.mode = 'busy';
    // Long jobs run for hours; six hours of silence is still a live listener.
    await vi.advanceTimersByTimeAsync(6 * 60 * 60_000);
    expect(supervisor.status.stage).toBe('unresponsive');

    health.mode = 'up';
    await vi.advanceTimersByTimeAsync(3_000);
    expect(supervisor.status.stage).toBe('ready');
  } finally {
    await supervisor.shutdown();
  }
});

it('forgets a past refusal once the listener is back (classified from the latest probes)', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await attachedSupervisor(health);
  try {
    // Two refusals (a restart in progress), then the listener returns busy.
    health.mode = 'down';
    await vi.advanceTimersByTimeAsync(4_500);
    health.mode = 'busy';
    await vi.advanceTimersByTimeAsync(60_000);
    expect(supervisor.status.stage).toBe('unresponsive');
  } finally {
    await supervisor.shutdown();
  }
});

it('reports remote timeouts as uncertain connectivity, not busy', async () => {
  const health: Health = { mode: 'up' };
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  vi.stubEnv('OMNIVOICE_PORT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  stubHealth(health);
  const supervisor = new BackendSupervisor();
  (supervisor as unknown as { remoteUrl: string }).remoteUrl = 'http://remote.example:3900';
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(supervisor.status.stage).toBe('ready');
    health.mode = 'busy';
    await vi.advanceTimersByTimeAsync(60_000);
    expect(supervisor.status.stage).toBe('unresponsive');
    expect(supervisor.status.message).toMatch(/Cannot confirm connectivity/);
    // The renderer localizes this state from the code, not the English prose.
    expect(supervisor.status.diagnosis).toBe('remote_unreachable');
    expect(supervisor.status.message).not.toMatch(/busy; it is not answering/);
  } finally {
    await supervisor.shutdown();
  }
});

it('reports a backend that keeps answering unhealthy as failed, never busy', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await attachedSupervisor(health);
  try {
    health.mode = 'rejected';
    // A few unhealthy answers are not enough; the listener is alive.
    await vi.advanceTimersByTimeAsync(20_000);
    expect(supervisor.status.stage).toBe('ready');

    await vi.advanceTimersByTimeAsync(40_000);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.diagnosis).toBe('unhealthy');
    expect(supervisor.status.message).not.toMatch(/busy/i);

    // Nothing was killed or respawned, and a healthy probe clears it.
    expect(mocks.spawn).not.toHaveBeenCalled();
    health.mode = 'up';
    await vi.advanceTimersByTimeAsync(3_000);
    expect(supervisor.status.stage).toBe('ready');
    expect(supervisor.status.diagnosis).toBeUndefined();
  } finally {
    await supervisor.shutdown();
  }
});

it('does not let unhealthy answers hide a listener that then goes away', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await attachedSupervisor(health);
  try {
    health.mode = 'rejected';
    await vi.advanceTimersByTimeAsync(10_000);
    health.mode = 'down';
    await vi.advanceTimersByTimeAsync(8_000);
    expect(supervisor.status.stage).toBe('crashed');
  } finally {
    await supervisor.shutdown();
  }
});

it('resumes supervising a remote after a Retry made while it was down', async () => {
  const health: Health = { mode: 'up' };
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  vi.stubEnv('OMNIVOICE_PORT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  stubHealth(health);
  const supervisor = new BackendSupervisor();
  (supervisor as unknown as { remoteUrl: string }).remoteUrl = 'http://remote.example:3900';
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(1_000);
    health.mode = 'rejected';
    await vi.advanceTimersByTimeAsync(60_000);
    expect(supervisor.status.stage).toBe('failed');

    // The gate's Retry while the server is still down: the initial probe fails.
    health.mode = 'down';
    await supervisor.restart();
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.message).toMatch(/Could not reach the configured remote/);

    // Nothing but the recovery loop is probing now; it must notice the return.
    health.mode = 'up';
    await vi.advanceTimersByTimeAsync(5_000);
    expect(supervisor.status.stage).toBe('ready');

    // And supervision is back: a later stall is reported again.
    health.mode = 'busy';
    await vi.advanceTimersByTimeAsync(10_000);
    expect(supervisor.status.stage).toBe('unresponsive');
  } finally {
    await supervisor.shutdown();
  }
});

async function remoteSupervisor(health: Health): Promise<BackendSupervisor> {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  vi.stubEnv('OMNIVOICE_PORT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  stubHealth(health);
  const supervisor = new BackendSupervisor();
  (supervisor as unknown as { remoteUrl: string }).remoteUrl = 'http://remote.example:3900';
  return supervisor;
}

it('does not mark a remote ready on /health alone when its session is no longer accepted', async () => {
  const health: Health = { mode: 'up', authRejected: true };
  const supervisor = await remoteSupervisor(health);
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.diagnosis).toBe('auth_required');
    expect(supervisor.status.message).toMatch(/no longer accepts/);

    // Reconnecting with a fresh session clears it.
    health.authRejected = false;
    await supervisor.restart();
    expect(supervisor.status.stage).toBe('ready');
    expect(supervisor.status.diagnosis).toBeUndefined();
  } finally {
    await supervisor.shutdown();
  }
});

it('keeps a recovered remote failed when its session expired during the outage', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await remoteSupervisor(health);
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(supervisor.status.stage).toBe('ready');

    // Outage: Retry while down parks it failed with the recovery probe running.
    health.mode = 'down';
    await supervisor.restart();
    expect(supervisor.status.stage).toBe('failed');

    // The server returns, but the session did not survive the outage.
    health.mode = 'up';
    health.authRejected = true;
    await vi.advanceTimersByTimeAsync(5_000);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.diagnosis).toBe('auth_required');
  } finally {
    await supervisor.shutdown();
  }
});

it('keeps an unresponsive remote failed on auth when /health recovers before its session', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await remoteSupervisor(health);
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(1_000);
    health.mode = 'busy';
    await vi.advanceTimersByTimeAsync(10_000);
    expect(supervisor.status.stage).toBe('unresponsive');

    health.mode = 'up';
    health.authRejected = true;
    await vi.advanceTimersByTimeAsync(3_000);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.diagnosis).toBe('auth_required');
  } finally {
    await supervisor.shutdown();
  }
});

it('never asks a local backend for credentials before marking it ready', async () => {
  const health: Health = { mode: 'up', authRejected: true };
  const supervisor = await attachedSupervisor(health);
  try {
    expect(supervisor.status.stage).toBe('ready');
    expect(calls.some((url) => url.endsWith('/system/info'))).toBe(false);
  } finally {
    await supervisor.shutdown();
  }
});

it('re-validates an auth_required remote on a slow cadence so out-of-app fixes recover', async () => {
  const health: Health = { mode: 'up', authRejected: true };
  const supervisor = await remoteSupervisor(health);
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(supervisor.status.diagnosis).toBe('auth_required');

    // Still rejected: checked rarely, not every 2 s tick.
    const checks = () => calls.filter((url) => url.endsWith('/system/info')).length;
    const before = checks();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(checks() - before).toBeLessThanOrEqual(1);
    expect(supervisor.status.stage).toBe('failed');

    // Credentials are fixed outside the app (key restored on the server).
    health.authRejected = false;
    await vi.advanceTimersByTimeAsync(40_000);
    expect(supervisor.status.stage).toBe('ready');
    expect(supervisor.status.diagnosis).toBeUndefined();
  } finally {
    await supervisor.shutdown();
  }
});

it.each(['timeout', 'server_error'] as const)(
  'reports uncertain connectivity, not ready, when the first credential check is inconclusive (%s)',
  async (kind) => {
    const health: Health = { mode: 'up', authInconclusive: kind };
    const supervisor = await remoteSupervisor(health);
    try {
      await supervisor.start();
      await vi.advanceTimersByTimeAsync(5_000);
      expect(supervisor.status.stage).toBe('unresponsive');
      expect(supervisor.status.diagnosis).toBe('remote_unreachable');

      // The next tick retries; a valid answer promotes.
      health.authInconclusive = undefined;
      await vi.advanceTimersByTimeAsync(3_000);
      expect(supervisor.status.stage).toBe('ready');
      expect(supervisor.status.diagnosis).toBeUndefined();
    } finally {
      await supervisor.shutdown();
    }
  },
);

it('never promotes an auth_required remote on an inconclusive recheck', async () => {
  const health: Health = { mode: 'up', authRejected: true };
  const supervisor = await remoteSupervisor(health);
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(supervisor.status.diagnosis).toBe('auth_required');

    // Rechecks keep failing to complete: the session is still unverified.
    health.authRejected = false;
    health.authInconclusive = 'timeout';
    await vi.advanceTimersByTimeAsync(120_000);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.diagnosis).toBe('auth_required');

    // A valid answer finally promotes it.
    health.authInconclusive = undefined;
    await vi.advanceTimersByTimeAsync(40_000);
    expect(supervisor.status.stage).toBe('ready');
  } finally {
    await supervisor.shutdown();
  }
});

it('never promotes a failed remote that recovered on /health when its credentials are unverifiable', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await remoteSupervisor(health);
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(1_000);
    health.mode = 'down';
    await supervisor.restart();
    expect(supervisor.status.stage).toBe('failed');

    health.mode = 'up';
    health.authInconclusive = 'server_error';
    await vi.advanceTimersByTimeAsync(20_000);
    expect(supervisor.status.stage).toBe('failed');

    health.authInconclusive = undefined;
    await vi.advanceTimersByTimeAsync(3_000);
    expect(supervisor.status.stage).toBe('ready');
  } finally {
    await supervisor.shutdown();
  }
});

it('demotes a recovering remote whose credentials are rejected, even after inconclusive checks', async () => {
  const health: Health = { mode: 'up' };
  const supervisor = await remoteSupervisor(health);
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(1_000);
    health.mode = 'busy';
    await vi.advanceTimersByTimeAsync(10_000);
    expect(supervisor.status.stage).toBe('unresponsive');

    health.mode = 'up';
    health.authInconclusive = 'timeout';
    await vi.advanceTimersByTimeAsync(6_000);
    expect(supervisor.status.stage).toBe('unresponsive');

    health.authInconclusive = undefined;
    health.authRejected = true;
    await vi.advanceTimersByTimeAsync(4_000);
    expect(supervisor.status.stage).toBe('failed');
    expect(supervisor.status.diagnosis).toBe('auth_required');
  } finally {
    await supervisor.shutdown();
  }
});
