import { beforeEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ bridge: null as any, api: vi.fn() }));
vi.mock('@/components/bridge', () => ({ getBridge: () => mock.bridge }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api }));
const on = { enabled: true, opted_in: true, available: true, prompted: true };
const off = { ...on, enabled: false, opted_in: false };

beforeEach(() => {
  vi.resetModules();
  mock.bridge = null;
  mock.api.mockReset();
});

it('collects nothing with unanswered diagnostics even when analytics was enabled', async () => {
  const capture = vi.fn(async () => {});
  mock.bridge = {
    diagnostics: { getStatus: vi.fn(async () => ({ ...off, prompted: false })), capture },
  };
  localStorage.setItem('voicestudio.setup.analytics-consent.v1', JSON.stringify({ enabled: true }));
  const client = await import('./diagnostic-reports');
  client.captureDiagnosticException(new Error('private words'), 'renderer_crash', 'render');
  await client.refreshDiagnosticState();
  client.captureDiagnosticException(new Error('private words'), 'renderer_crash', 'render');
  expect(capture).not.toHaveBeenCalled();
  localStorage.clear();
});

it('uses desktop consent/capture without requiring a working backend', async () => {
  const capture = vi.fn(async () => {});
  mock.bridge = { diagnostics: { getStatus: vi.fn(async () => on), capture } };
  const client = await import('./diagnostic-reports');
  await client.refreshDiagnosticState();
  const error = new TypeError('secret prompt https://private.example/?token=secret');
  error.stack = `TypeError: secret prompt\n at run (file:///Users/private/repo/electron/src/renderer/src/hooks/use-generate.ts:12:9)\n at other (file:///Users/private/customer-name.ts:8:2)`;
  client.captureDiagnosticException(error, 'generation_failed', 'generation');
  client.captureDiagnosticException(error, 'renderer_crash', 'render');
  expect(capture).toHaveBeenCalledOnce();
  expect(capture).toHaveBeenCalledWith({
    category: 'generation_failed',
    source: 'renderer',
    stage: 'generation',
    error_type: 'TypeError',
    frames: 'electron/src/renderer/src/hooks/use-generate.ts:12:9',
  });
  expect(JSON.stringify(capture.mock.calls)).not.toMatch(/secret|private|customer|token/);
  expect(mock.api).not.toHaveBeenCalled();
});

it('coarsens unknown app asset names and excludes remote or personal locations', async () => {
  const client = await import('./diagnostic-reports');
  const origin = window.location.origin;
  const error = new Error('do not send this');
  error.stack = `Error: do not send this\n at fn (${origin}/assets/customer-private.js:2:8)\n at third (https://external.example/assets/index.js:3:9)\n at local (file:///home/private/name.ts:4:1)`;
  expect(client.diagnosticFrames(error)).toBe('renderer:2:8');
});

it('fails closed for malformed truthy consent fields', async () => {
  mock.api.mockResolvedValue({ ...on, enabled: 'true' });
  const client = await import('./diagnostic-reports');
  await client.refreshDiagnosticState();
  client.captureDiagnosticException(new Error('private'), 'renderer_crash', 'render');
  expect(mock.api).toHaveBeenCalledTimes(1);
  expect(client.getDiagnosticState().status).toBeNull();
});

it('an old enabled read cannot undo immediate withdrawal', async () => {
  let resolve!: (value: typeof on) => void;
  const capture = vi.fn(async () => {});
  mock.bridge = {
    diagnostics: {
      getStatus: vi.fn(
        () =>
          new Promise((done) => {
            resolve = done;
          }),
      ),
      setConsent: vi.fn(async () => off),
      capture,
    },
  };
  const client = await import('./diagnostic-reports');
  const read = client.refreshDiagnosticState();
  await client.setDiagnosticConsent(false);
  resolve(on);
  await read;
  client.captureDiagnosticException(new Error('private'), 'renderer_crash', 'render');
  expect(capture).not.toHaveBeenCalled();
  expect(client.getDiagnosticState().status?.enabled).toBe(false);
});

it('failed opt-in stays off and does not replay previous faults after enabling', async () => {
  const capture = vi.fn(async () => {});
  const save = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(on);
  mock.bridge = { diagnostics: { getStatus: vi.fn(async () => off), setConsent: save, capture } };
  const client = await import('./diagnostic-reports');
  await client.refreshDiagnosticState();
  client.captureDiagnosticException(new Error('before consent'), 'renderer_crash', 'render');
  await expect(client.setDiagnosticConsent(true)).rejects.toThrow();
  expect(client.getDiagnosticState().status?.enabled).toBe(false);
  await client.setDiagnosticConsent(true);
  expect(capture).not.toHaveBeenCalled();
  client.captureDiagnosticException(new Error('after consent'), 'renderer_crash', 'render');
  expect(capture).toHaveBeenCalledOnce();
});

it('browser fallback uses only the diagnostic endpoint and aborts delivery on off', async () => {
  let signal: AbortSignal | undefined;
  mock.api.mockImplementation((path, init) => {
    if (path.endsWith('/event')) {
      signal = init.signal;
      return new Promise(() => {});
    }
    return Promise.resolve(init?.method === 'PUT' ? off : on);
  });
  const client = await import('./diagnostic-reports');
  await client.refreshDiagnosticState();
  const failure = new Error('text must not leave');
  failure.stack = 'Error: text must not leave';
  client.captureDiagnosticException(failure, 'operation_failed', 'other');
  expect(signal?.aborted).toBe(false);
  await client.setDiagnosticConsent(false);
  expect(signal?.aborted).toBe(true);
  expect(mock.api.mock.calls.map(([path]) => path)).toEqual([
    '/api/settings/diagnostics',
    '/api/settings/diagnostics/event',
    '/api/settings/diagnostics',
  ]);
  expect(mock.api.mock.calls[1][1].body).toBe(
    JSON.stringify({
      category: 'operation_failed',
      source: 'renderer',
      stage: 'other',
      error_type: 'Error',
    }),
  );
});

it('a refresh started during withdrawal cannot turn capture back on', async () => {
  let finish!: (value: typeof off) => void;
  const capture = vi.fn(async () => {});
  mock.bridge = {
    diagnostics: {
      getStatus: vi.fn(async () => on),
      setConsent: vi.fn(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      ),
      capture,
    },
  };
  const client = await import('./diagnostic-reports');
  await client.refreshDiagnosticState();
  const saving = client.setDiagnosticConsent(false);
  await client.refreshDiagnosticState();
  client.captureDiagnosticException(new Error('private'), 'renderer_crash', 'render');
  expect(capture).not.toHaveBeenCalled();
  finish(off);
  await saving;
});

it('a failed withdrawal stays locally off through later status refreshes', async () => {
  const capture = vi.fn(async () => {});
  mock.bridge = {
    diagnostics: {
      getStatus: vi.fn(async () => on),
      setConsent: vi.fn(async () => {
        throw new Error('disk full');
      }),
      capture,
    },
  };
  const client = await import('./diagnostic-reports');
  await client.refreshDiagnosticState();
  await expect(client.setDiagnosticConsent(false)).rejects.toThrow();
  await client.refreshDiagnosticState();
  client.captureDiagnosticException(new Error('private'), 'renderer_crash', 'render');
  expect(capture).not.toHaveBeenCalled();
});

it('a new Yes cannot authorize early faults waiting on an older status read', async () => {
  let finish!: (value: typeof on) => void;
  mock.bridge = {
    diagnostics: {
      getStatus: vi.fn(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      ),
      setConsent: vi.fn(async () => on),
      capture: vi.fn(async () => {}),
    },
  };
  const client = await import('./diagnostic-reports');
  const replay = vi.fn();
  const initial = client.initializeDiagnosticReporting(replay);
  await Promise.resolve();
  await client.setDiagnosticConsent(true);
  finish(on);
  await initial;
  expect(replay).not.toHaveBeenCalled();
});

it('keeps a classified incompatibility code while rejecting validation/auth bodies', async () => {
  const capture = vi.fn(async () => {});
  mock.bridge = { diagnostics: { getStatus: vi.fn(async () => on), capture } };
  const client = await import('./diagnostic-reports');
  await client.refreshDiagnosticState();
  const failure = Object.assign(new Error('private GPU and prompt'), {
    status: 400,
    payload: { detail: { code: 'GPU_ARCH_UNSUPPORTED', message: 'private words' } },
  });
  failure.stack = 'Error: private words';
  client.captureDiagnosticException(failure, 'generation_failed', 'generation');
  client.captureDiagnosticException(
    Object.assign(new Error('private'), {
      status: 401,
      payload: { detail: { code: 'GPU_ARCH_UNSUPPORTED' } },
    }),
    'operation_failed',
    'other',
  );
  client.captureDiagnosticException(
    Object.assign(new Error('private'), {
      status: 422,
      payload: { detail: { code: 'INVALID_MEDIA_FILE' } },
    }),
    'operation_failed',
    'other',
  );
  expect(capture).toHaveBeenCalledExactlyOnceWith({
    category: 'generation_failed',
    source: 'renderer',
    stage: 'generation',
    error_type: 'Error',
    error_code: 'GPU_ARCH_UNSUPPORTED',
  });
  expect(JSON.stringify(capture.mock.calls)).not.toContain('private');
});

it('bounds stalled browser delivery and aborts every pending request on withdrawal', async () => {
  const signals: AbortSignal[] = [];
  mock.api.mockImplementation((path, init) => {
    if (path.endsWith('/event')) {
      signals.push(init.signal);
      return new Promise(() => {});
    }
    return Promise.resolve(init?.method === 'PUT' ? off : on);
  });
  const client = await import('./diagnostic-reports');
  await client.refreshDiagnosticState();
  for (let index = 0; index < 80; index++) {
    const error = new Error('private content');
    error.stack = 'Error: private content';
    client.captureDiagnosticException(error, 'renderer_crash', 'render');
  }
  expect(signals).toHaveLength(64);
  await client.setDiagnosticConsent(false);
  expect(signals.every((signal) => signal.aborted)).toBe(true);
});

it('a synchronous bridge failure does not poison later consent retries', async () => {
  const status = vi
    .fn()
    .mockImplementationOnce(() => {
      throw new Error('bridge unavailable');
    })
    .mockResolvedValueOnce(on);
  mock.bridge = { diagnostics: { getStatus: status, capture: vi.fn(async () => {}) } };
  const client = await import('./diagnostic-reports');
  await client.refreshDiagnosticState();
  await client.refreshDiagnosticState();
  expect(status).toHaveBeenCalledTimes(2);
  expect(client.getDiagnosticState().status?.enabled).toBe(true);
});
