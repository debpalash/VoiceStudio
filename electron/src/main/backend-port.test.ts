// @vitest-environment node
import { EventEmitter } from 'node:events';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  spawn: vi.fn(),
  listen: vi.fn(),
  bindError: 'EACCES' as string | null,
  occupied: new Set<number>(),
  denyAll: false,
  close: vi.fn(),
}));
vi.mock('electron', () => ({ app: { isPackaged: true, getPath: () => '/unused-port-test' } }));
vi.mock('node:child_process', () => ({ spawn: mocks.spawn }));
vi.mock('node:net', () => ({
  createServer: () => {
    const server = Object.assign(new EventEmitter(), {
      listen: (options: { port: number }, done: () => void) => {
        mocks.listen(options);
        const code = mocks.occupied.has(options.port)
          ? 'EADDRINUSE'
          : options.port === 3900 || mocks.denyAll
            ? mocks.bindError
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
vi.mock('./runtime-project', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./runtime-project')>()),
  runtimeReady: async () => true,
  runtimeDependenciesReady: async () => true,
  stageRuntimeSources: async () => {},
  runtimePython: () => '/runtime/python',
}));
import { BackendSupervisor } from './backend';
import { availableBackendPort } from './backend-port';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  mocks.bindError = 'EACCES';
  mocks.occupied.clear();
  mocks.denyAll = false;
});

it.each(['ready', 'timeout', 'shutdown'])(
  'waits for an identified starting fallback: %s',
  async (outcome) => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
    vi.stubEnv('OMNIVOICE_PORT', '');
    vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
    vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
    vi.stubEnv('OMNIVOICE_STARTUP_BUDGET_S', '10');
    mocks.occupied.add(4900);
    let ready = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (!url.startsWith('http://127.0.0.1:4900/')) throw new Error('unreachable');
        return new Response(
          JSON.stringify({ status: ready ? 'ok' : 'starting', version: 'test' }),
          {
            status: ready ? 200 : 503,
            headers: { 'x-omnivoice-backend': 'test' },
          },
        );
      }),
    );
    const supervisor = new BackendSupervisor();
    try {
      await supervisor.start();
      expect(mocks.spawn).not.toHaveBeenCalled();
      expect(supervisor.status.stage).toBe('attaching');
      if (outcome === 'shutdown') await supervisor.shutdown();
      ready = outcome !== 'timeout';
      await vi.advanceTimersByTimeAsync(11_000);
      expect(supervisor.status.stage).toBe(
        outcome === 'ready' ? 'ready' : outcome === 'timeout' ? 'failed' : 'idle',
      );
      expect(mocks.spawn).not.toHaveBeenCalled();
    } finally {
      (supervisor as unknown as { child: null }).child = null;
      await supervisor.shutdown();
    }
  },
);

it.each([false, true])(
  'recovers a denied default and retries it on restart (existing default backend: %s)',
  async (existingDefault) => {
    vi.stubEnv('OMNIVOICE_PORT', '');
    vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
    vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
    let runningPort = 0;
    const child = Object.assign(new EventEmitter(), {
      stdin: null,
      stdout: null,
      stderr: null,
      stdio: [],
    });
    mocks.spawn.mockImplementation((_command: string, args: string[]) => {
      const port = Number(args.at(-1));
      // Replay the reported bind denial: this port cannot host a healthy backend.
      runningPort = port === 3900 ? 0 : port;
      if (runningPort) mocks.occupied.add(runningPort);
      return child;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (!runningPort || !url.startsWith(`http://127.0.0.1:${runningPort}/`))
          throw new Error('unreachable');
        return new Response(JSON.stringify({ status: 'ok', version: 'test' }), {
          headers: { 'x-omnivoice-backend': 'test' },
        });
      }),
    );
    const supervisor = new BackendSupervisor();
    try {
      await supervisor.start();
      expect(runningPort).toBe(4900);
      await vi.waitFor(() => expect(supervisor.status.stage).toBe('ready'));
      expect(supervisor.status.baseUrl).toBe('http://127.0.0.1:4900');
      expect(mocks.spawn.mock.calls[0][2].env.OMNIVOICE_PORT).toBe('4900');
      vi.stubEnv('VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES', '1');
      const second = new BackendSupervisor();
      try {
        await second.start();
        expect(mocks.spawn).toHaveBeenCalledTimes(1);
        expect(second.status.stage).toBe('ready');
        expect(second.status.managed).toBe(false);
        expect(second.baseUrl).toBe(supervisor.baseUrl);
      } finally {
        (second as unknown as { child: null }).child = null;
        await second.shutdown();
      }
      (supervisor as unknown as { child: null }).child = null;
      await supervisor.shutdown();
      runningPort = existingDefault ? 3900 : 0;
      mocks.occupied.clear();
      mocks.bindError = null;
      mocks.listen.mockClear();
      mocks.spawn.mockImplementation((_command: string, args: string[]) => {
        runningPort = Number(args.at(-1));
        return child;
      });
      await supervisor.start();
      expect(runningPort).toBe(3900);
      await vi.waitFor(() => expect(supervisor.status.stage).toBe('ready'));
      expect(supervisor.baseUrl).toBe('http://127.0.0.1:3900');
      expect(mocks.spawn).toHaveBeenCalledTimes(existingDefault ? 1 : 2);
      if (existingDefault) expect(mocks.listen).not.toHaveBeenCalled();
      else
        expect(mocks.listen).toHaveBeenCalledExactlyOnceWith({
          host: '127.0.0.1',
          port: 3900,
          exclusive: true,
        });
    } finally {
      // Detach the test child before normal shutdown (no real process was spawned).
      (supervisor as unknown as { child: null }).child = null;
      await supervisor.shutdown();
    }
  },
);

it.each([200, 404])('skips unrelated occupied fallback listeners (HTTP %s)', async (status) => {
  vi.stubEnv('OMNIVOICE_PORT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  mocks.occupied.add(4900);
  let runningPort = 0;
  mocks.spawn.mockImplementation((_command: string, args: string[]) => {
    runningPort = Number(args.at(-1));
    return Object.assign(new EventEmitter(), {
      stdin: null,
      stdout: null,
      stderr: null,
      stdio: [],
    });
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url.startsWith('http://127.0.0.1:4900/'))
        return new Response(JSON.stringify({ status: 'ok', version: 'test' }), { status });
      if (runningPort && url.startsWith(`http://127.0.0.1:${runningPort}/`))
        return new Response(JSON.stringify({ status: 'ok', version: 'test' }), {
          headers: { 'x-omnivoice-backend': 'test' },
        });
      throw new Error('unreachable');
    }),
  );
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    expect(runningPort).toBe(5900);
    await vi.waitFor(() => expect(supervisor.status.stage).toBe('ready'));
    expect(supervisor.baseUrl).toBe('http://127.0.0.1:5900');
    expect(vi.mocked(fetch).mock.calls.every(([, options]) => options?.redirect === 'error')).toBe(
      true,
    );
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it('gives a replacement its full handoff grace after a late startup exit', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  vi.stubEnv('OMNIVOICE_PORT', '');
  vi.stubEnv('OMNIVOICE_BACKEND_CMD', '');
  vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', '');
  vi.stubEnv('OMNIVOICE_STARTUP_BUDGET_S', '10');
  let ready = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      if (!ready) throw new Error('starting');
      return new Response(JSON.stringify({ status: 'ok', version: 'test' }), {
        headers: { 'x-omnivoice-backend': 'test' },
      });
    }),
  );
  const child = Object.assign(new EventEmitter(), {
    stdin: null,
    stdout: null,
    stderr: null,
    stdio: [],
  });
  mocks.spawn.mockReturnValue(child);
  const supervisor = new BackendSupervisor();
  try {
    await supervisor.start();
    await vi.advanceTimersByTimeAsync(9_000);
    child.emit('exit', 0, null);
    await vi.advanceTimersByTimeAsync(8_000);
    expect(supervisor.status.stage).toBe('attaching');
    ready = true;
    await vi.advanceTimersByTimeAsync(500);
    expect(supervisor.status.stage).toBe('ready');
    expect(supervisor.status.managed).toBe(false);
  } finally {
    (supervisor as unknown as { child: null }).child = null;
    await supervisor.shutdown();
  }
});

it.each([null, 'EADDRINUSE'])(
  'preserves the preferred port for %s (including replacement attachment)',
  async (error) => {
    mocks.bindError = error;
    expect(await availableBackendPort(3900)).toBe(3900);
    expect(mocks.listen).toHaveBeenCalledExactlyOnceWith({
      host: '127.0.0.1',
      port: 3900,
      exclusive: true,
    });
    expect(mocks.close).toHaveBeenCalledTimes(error ? 0 : 1);
  },
);

it('does not hide unrelated bind failures', async () => {
  mocks.bindError = 'EMFILE';
  await expect(availableBackendPort(3900)).rejects.toMatchObject({ code: 'EMFILE' });
  expect(mocks.listen).toHaveBeenCalledTimes(1);
});

it('bounds fallback attempts when all candidates are denied', async () => {
  mocks.denyAll = true;
  await expect(availableBackendPort(3900)).rejects.toMatchObject({ code: 'EACCES' });
  expect(mocks.listen).toHaveBeenCalledTimes(17);
  expect(mocks.listen.mock.calls.every(([options]) => options.port !== 0)).toBe(true);
});

it.each(['explicit port', 'custom command', 'external backend', 'healthy backend'])(
  'never changes the port for an %s',
  async (kind) => {
    vi.stubEnv('OMNIVOICE_PORT', kind === 'explicit port' ? '3900' : '');
    vi.stubEnv('OMNIVOICE_BACKEND_CMD', kind === 'custom command' ? '["custom-python"]' : '');
    vi.stubEnv('VOICESTUDIO_SKIP_BACKEND', kind === 'external backend' ? '1' : '');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        if (kind !== 'healthy backend') throw new Error('unreachable');
        return new Response(JSON.stringify({ status: 'ok', version: 'test' }));
      }),
    );
    mocks.spawn.mockReturnValue(
      Object.assign(new EventEmitter(), { stdin: null, stdout: null, stderr: null, stdio: [] }),
    );
    const supervisor = new BackendSupervisor();
    try {
      await supervisor.start();
      expect(supervisor.port).toBe(3900);
      expect(mocks.listen).not.toHaveBeenCalled();
    } finally {
      (supervisor as unknown as { child: null }).child = null;
      await supervisor.shutdown();
    }
  },
);
