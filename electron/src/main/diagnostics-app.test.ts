// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { fstatSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { DiagnosticReports, type DiagnosticsOptions } from './diagnostics';
import { diagnosticAppDestination, diagnosticAppFields } from './diagnostics-app';
import analyticsConfig from '../../../config/analytics.json';
import { TTL_MS, type DiagnosticProps } from './diagnostics-schema';

const storageGate = vi.hoisted(() => ({
  unreadConsent: false,
  metadataOpenedFd: undefined as number | undefined,
  withoutNoFollow: false,
  afterMetadataOpen: undefined as ((file: string) => void) | undefined,
}));
vi.mock('node:fs', async (load) => {
  const actual = await load<typeof import('node:fs')>();
  return {
    ...actual,
    openSync: (...args: Parameters<typeof actual.openSync>) => {
      const metadata = String(args[0]).endsWith('supermetry-app.generated.json');
      const flags =
        metadata && storageGate.withoutNoFollow && typeof args[1] === 'number'
          ? args[1] & ~actual.constants.O_NOFOLLOW
          : args[1];
      const fd = actual.openSync(args[0], flags, args[2]);
      if (metadata) {
        storageGate.metadataOpenedFd = fd;
        try {
          storageGate.afterMetadataOpen?.(String(args[0]));
        } catch (error) {
          actual.closeSync(fd);
          throw error;
        }
      }
      return fd;
    },
  };
});
vi.mock('node:fs/promises', async (load) => {
  const actual = await load<typeof import('node:fs/promises')>();
  return {
    ...actual,
    open: (...args: Parameters<typeof actual.open>) => {
      if (storageGate.unreadConsent && String(args[0]).endsWith('consent.json'))
        return Promise.reject(Object.assign(new Error('fixture read denied'), { code: 'EACCES' }));
      return actual.open(...args);
    },
  };
});

const identity = {
  app_version: '0.5.9-beta.3',
  build_commit: '1234567',
  platform: 'linux',
  arch: 'x64',
};
const legacy = {
  site: 'legacy-fixture',
  host: 'https://legacy.invalid',
  appUrl: 'https://app.invalid',
};
const env = {
  SUPERMETRY_APP_ID: 'fixture12',
  SUPERMETRY_INGEST_KEY: 'smk_' + 'a'.repeat(39),
  SUPERMETRY_APP_HOST: 'https://app-intake.invalid',
};
let directory: string;
let instances: DiagnosticReports[];
const originalResourcesPath = Object.getOwnPropertyDescriptor(process, 'resourcesPath');
beforeEach(async () => {
  storageGate.unreadConsent = false;
  storageGate.metadataOpenedFd = undefined;
  storageGate.withoutNoFollow = false;
  storageGate.afterMetadataOpen = undefined;
  directory = await mkdtemp(join(tmpdir(), 'voice-app-intake-'));
  instances = [];
});
afterEach(async () => {
  storageGate.unreadConsent = false;
  storageGate.afterMetadataOpen = undefined;
  storageGate.withoutNoFollow = false;
  for (const instance of instances) await instance.close().catch(() => {});
  await rm(directory, { recursive: true, force: true });
  vi.useRealTimers();
  vi.unstubAllGlobals();
  if (originalResourcesPath) Object.defineProperty(process, 'resourcesPath', originalResourcesPath);
  else Reflect.deleteProperty(process, 'resourcesPath');
});
function reporter(options: Partial<DiagnosticsOptions> = {}) {
  const instance = new DiagnosticReports({
    directory,
    identity,
    destination: legacy,
    env,
    automatic: false,
    ...options,
  });
  instances.push(instance);
  return instance;
}
const accepted = (count: number) =>
  new Response(JSON.stringify({ accepted: count, dropped: [] }), { status: 202 });

it('selects app intake when an explicit valid app configuration exists', async () => {
  const send = vi.fn(async (_url: unknown, _init?: RequestInit) => accepted(1));
  const reports = reporter({ send });
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'generation_failed', stage: 'generation' });
  await reports.flush();
  expect(send).toHaveBeenCalledOnce();
  expect(send.mock.calls[0][0]).toBe('https://app-intake.invalid/api/public/v1/app-events');
  const init = send.mock.calls[0][1] as RequestInit;
  expect(init.headers).toEqual({
    'Content-Type': 'application/json',
    'X-Supermetry-Key': env.SUPERMETRY_INGEST_KEY,
    'User-Agent': 'VoiceStudio/0.5.9-beta.3 (linux; x64)',
  });
  const body = JSON.parse(String(init.body));
  expect(body.site).toBe('fixture12');
  expect(body.events).toHaveLength(1);
  expect(body.events[0]).toMatchObject({
    kind: 'operation_failed',
    handled: true,
    level: 'error',
    runtime: 'electron',
  });
  expect(body.events[0].tags.category).toBe('generation_failed');
  expect(Object.keys(body.events[0].tags)).toEqual([
    'category',
    'source',
    'stage',
    'error_code',
    'engine_id',
    'schema_version',
  ]);
  expect(init).toMatchObject({
    credentials: 'omit',
    redirect: 'manual',
    referrerPolicy: 'no-referrer',
  });
});

it('batches by event count and encoded body bytes without changing failure identities', async () => {
  const send = vi.fn(async (_url: unknown, init?: RequestInit) =>
    accepted(JSON.parse(String(init?.body)).events.length),
  );
  const reports = reporter({ send });
  await reports.initialize();
  await reports.setConsent(true);
  for (let exit_code = 0; exit_code < 26; exit_code++)
    reports.capture({ category: 'app_crash', exit_code });
  await reports.flush();
  const batches = send.mock.calls.map(([, init]) => JSON.parse(String(init?.body)));
  expect(batches.map((batch) => batch.events.length)).toEqual([20, 6]);
  expect(batches.flatMap((batch) => batch.events).map((event) => event.id).length).toBe(
    new Set(batches.flatMap((batch) => batch.events).map((event) => event.id)).size,
  );
  expect(reports.status().pending_count).toBe(0);
  for (const [, init] of send.mock.calls)
    expect(Buffer.byteLength(String(init?.body))).toBeLessThanOrEqual(16_384);
});

it('splits large frame reports before the encoded16KiB limit', async () => {
  const send = vi.fn(async (_url: unknown, init?: RequestInit) =>
    accepted(JSON.parse(String(init?.body)).events.length),
  );
  const reports = reporter({ send });
  await reports.initialize();
  await reports.setConsent(true);
  const frames = Array.from(
    { length: 24 },
    () => 'electron/src/main/diagnostics-capture.ts:10000000:10000000',
  ).join('\n');
  for (let exit_code = 0; exit_code < 20; exit_code++)
    reports.capture({ category: 'app_crash', exit_code, frames });
  await reports.flush();
  expect(send.mock.calls.length).toBeGreaterThan(1);
  expect(send.mock.calls.flatMap(([, init]) => JSON.parse(String(init?.body)).events)).toHaveLength(
    20,
  );
  for (const [, init] of send.mock.calls)
    expect(Buffer.byteLength(String(init?.body))).toBeLessThanOrEqual(16_384);
});

it('acknowledges mixed202 terminal results and retains only the rate-limited identity', async () => {
  let now = 1_800_000_000_000;
  const send = vi.fn(
    async (_url: unknown, _init?: RequestInit) =>
      new Response(
        JSON.stringify({
          accepted: 1,
          dropped: [
            { index: 1, reason: 'rate_limited' },
            { index: 2, reason: 'invalid' },
            { index: 3, reason: 'duplicate' },
            { index: 4, reason: 'too_old' },
          ],
          warnings: [{ index: 0, reason: 'tag_not_allowed', tag: 'PRIVATE_UNKNOWN_TAG' }],
        }),
        { status: 202 },
      ),
  );
  const outcomes: unknown[] = [];
  const reports = reporter({
    send,
    now: () => now,
    retryMs: 1,
    onDeliveryResult: (outcome) => outcomes.push(outcome),
  });
  await reports.initialize();
  await reports.setConsent(true);
  for (let exit_code = 0; exit_code < 5; exit_code++)
    reports.capture({ category: 'app_crash', exit_code });
  await reports.flush();
  const first = JSON.parse(String(send.mock.calls[0][1]?.body));
  expect(reports.status().pending_count).toBe(1);
  expect(JSON.stringify(outcomes)).not.toContain('PRIVATE');
  expect(outcomes).toContainEqual({ reason: 'tag_not_allowed', count: 1 });
  send.mockImplementation(async () => accepted(1));
  now += 2;
  await reports.flush();
  expect(
    JSON.parse(String(send.mock.calls[1][1]?.body)).events.map((event: { id: string }) => event.id),
  ).toEqual([first.events[1].id]);
  expect(reports.status().pending_count).toBe(0);
});

it.each([429, 503, 'network'] as const)(
  'retries %s with stable IDs and timestamps',
  async (failure) => {
    let now = 1_800_000_000_000;
    const send = vi.fn(async (_url: unknown, _init?: RequestInit): Promise<Response> => {
      if (failure === 'network') throw new Error('PRIVATE_NETWORK_CONTENT');
      return new Response('', { status: failure });
    });
    const reports = reporter({ send, now: () => now, retryMs: 1 });
    await reports.initialize();
    await reports.setConsent(true);
    reports.capture({ category: 'app_crash' });
    await reports.flush();
    expect(send).toHaveBeenCalledTimes(3);
    expect(reports.status().pending_count).toBe(1);
    const bodies = send.mock.calls.map(([, init]) => String(init?.body));
    expect(new Set(bodies).size).toBe(1);
    send.mockImplementation(async () => accepted(1));
    now += 2;
    await reports.flush();
    expect(String(send.mock.calls[3][1]?.body)).toBe(bodies[0]);
    expect(reports.status().pending_count).toBe(0);
  },
);

it('pauses401 for the entire process while preserving the queue', async () => {
  const send = vi.fn(
    async (_url: unknown, _init?: RequestInit) => new Response('', { status: 401 }),
  );
  const outcomes: unknown[] = [];
  const reports = reporter({ send, onDeliveryResult: (outcome) => outcomes.push(outcome) });
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'app_crash' });
  await reports.flush();
  await reports.flush();
  expect(send).toHaveBeenCalledOnce();
  expect(reports.status().pending_count).toBe(1);
  expect(outcomes).toContainEqual({ reason: 'unauthorized', count: 1 });
  await reports.setConsent(false);
  await reports.setConsent(true);
  reports.capture({ category: 'operation_failed' });
  await reports.flush();
  expect(send).toHaveBeenCalledOnce();
  expect(reports.status().pending_count).toBe(1);
});

it('drops a400 batch without falling back to product events', async () => {
  const send = vi.fn(
    async (_url: unknown, _init?: RequestInit) => new Response('', { status: 400 }),
  );
  const reports = reporter({ send });
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'app_crash' });
  reports.capture({ category: 'operation_failed' });
  await reports.flush();
  expect(send).toHaveBeenCalledOnce();
  expect(reports.status().pending_count).toBe(0);
});

it.each([
  { accepted: 0, dropped: [] },
  {
    accepted: 0,
    dropped: [
      { index: 0, reason: 'invalid' },
      { index: 0, reason: 'invalid' },
    ],
  },
  { accepted: 0, dropped: [{ index: 0, reason: 'PRIVATE_REASON' }] },
])('does not acknowledge an ambiguous202 reply %#', async (reply) => {
  const send = vi.fn(
    async (_url: unknown, _init?: RequestInit) =>
      new Response(JSON.stringify(reply), { status: 202 }),
  );
  const reports = reporter({ send });
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'app_crash' });
  await reports.flush();
  expect(reports.status().pending_count).toBe(1);
  expect(send).toHaveBeenCalledTimes(3);
});

it('aborts active app intake on withdrawal without retaining keys in reports', async () => {
  let started!: () => void;
  const entered = new Promise<void>((resolve) => {
    started = resolve;
  });
  const send = vi.fn(
    (_url: unknown, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        started();
        init!.signal!.addEventListener('abort', () =>
          reject(new DOMException('cancelled', 'AbortError')),
        );
      }),
  );
  const reports = reporter({ send });
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'app_crash' });
  const pending = reports.flush();
  await entered;
  await reports.setConsent(false);
  await pending;
  expect(reports.status().pending_count).toBe(0);
  expect(send).toHaveBeenCalledOnce();
});

const generated = {
  app_id: 'generated1',
  ingest_key: 'smk_' + 'b'.repeat(39),
  host: 'https://generated.invalid',
  diagnostics: { sessionPings: true },
};
it('sends one explicit session per run and never links a regranted identity', async () => {
  const send = vi.fn(async (_url: unknown, init?: RequestInit) =>
    accepted(JSON.parse(String(init?.body)).events.length),
  );
  const reports = reporter({ send, appConfig: generated });
  await reports.initialize();
  await reports.flush();
  expect(send).not.toHaveBeenCalled();
  await reports.setConsent(true);
  await reports.flush();
  const session = JSON.parse(String(send.mock.calls[0][1]?.body)).events[0];
  expect(session.kind).toBe('session');
  reports.capture({ category: 'generation_failed' });
  await reports.flush();
  const failure = JSON.parse(String(send.mock.calls[1][1]?.body)).events[0];
  expect(failure.session_id).toBe(session.session_id);
  await reports.flush();
  expect(send).toHaveBeenCalledTimes(2);
  await reports.setConsent(false);
  await reports.setConsent(true);
  reports.capture({ category: 'operation_failed' });
  await reports.flush();
  const regranted = JSON.parse(String(send.mock.calls[2][1]?.body)).events;
  expect(regranted).toHaveLength(1);
  expect(regranted[0].kind).toBe('operation_failed');
  expect(regranted[0]).not.toHaveProperty('session_id');
  expect(regranted[0].install_id).not.toBe(session.install_id);
});

it('does not attach a new run session to restored failures', async () => {
  const first = reporter({
    appConfig: generated,
    send: async () => new Response('', { status: 401 }),
  });
  await first.initialize();
  await first.setConsent(true);
  first.capture({ category: 'engine_failed' });
  await first.flush();
  await first.close();
  const send = vi.fn(async (_url: unknown, init?: RequestInit) =>
    accepted(JSON.parse(String(init?.body)).events.length),
  );
  const second = reporter({ appConfig: generated, send });
  await second.initialize();
  second.capture({ category: 'operation_failed' });
  await second.flush();
  const events = JSON.parse(String(send.mock.calls[0][1]?.body)).events;
  const session = events.find((event: { kind: string }) => event.kind === 'session');
  const old = events.find(
    (event: { tags?: { category: string } }) => event.tags?.category === 'engine_failed',
  );
  const fresh = events.find(
    (event: { tags?: { category: string } }) => event.tags?.category === 'operation_failed',
  );
  expect(old).toBeTruthy();
  expect(old).not.toHaveProperty('session_id');
  expect(fresh.session_id).toBe(session.session_id);
});

it('retains the exact legacy envelope and headers when app credentials are absent', async () => {
  const send = vi.fn(
    async (_url: unknown, _init?: RequestInit) => new Response('', { status: 202 }),
  );
  const reports = reporter({ send, env: {}, appConfig: null });
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'app_crash' });
  await reports.flush();
  expect(send.mock.calls[0][0]).toBe('https://legacy.invalid/api/public/v1/events');
  const init = send.mock.calls[0][1]!;
  const body = JSON.parse(String(init.body));
  expect(Object.keys(body)).toEqual([
    'v',
    'id',
    'ts',
    'site',
    'name',
    'url',
    'profile_id',
    'props',
  ]);
  expect(body).toMatchObject({
    v: 1,
    site: legacy.site,
    name: 'diagnostic_report',
    url: 'https://app.invalid/diagnostics',
  });
  expect(init.headers).toEqual({
    'Content-Type': 'application/json',
    Origin: 'https://app.invalid',
    'User-Agent':
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) VoiceStudio/1.0 Chrome/130.0.0.0 Safari/537.36',
  });
  expect(String(init.body)).toBe(
    JSON.stringify({
      v: 1,
      id: body.id,
      ts: body.ts,
      site: legacy.site,
      name: 'diagnostic_report',
      url: legacy.appUrl + '/diagnostics',
      profile_id: body.profile_id,
      props: body.props,
    }),
  );
});

const golden = JSON.parse(
  readFileSync(resolve(__dirname, '../../../tests/fixtures/diagnostic-app-events.json'), 'utf8'),
) as { cases: { name: string; props: DiagnosticProps; expected: Record<string, unknown> }[] };
it.each(golden.cases)('matches the shared app-event mapping: $name', ({ props, expected }) => {
  const event = diagnosticAppFields(props);
  expect(event).toEqual(expected);
  expect(Object.keys(event.tags as object)).toEqual(Object.keys(expected.tags as object));
  expect(Object.keys(event.tags as object).length).toBeLessThanOrEqual(20);
});

it('resolves app config per field with strict env precedence and safe host fallback', () => {
  expect(diagnosticAppDestination({}, generated)).toEqual({
    appId: generated.app_id,
    ingestKey: generated.ingest_key,
    host: generated.host,
    sessionPings: true,
  });
  expect(diagnosticAppDestination(env, generated)).toEqual({
    appId: env.SUPERMETRY_APP_ID,
    ingestKey: env.SUPERMETRY_INGEST_KEY,
    host: env.SUPERMETRY_APP_HOST,
    sessionPings: true,
  });
  expect(
    diagnosticAppDestination({ ...env, SUPERMETRY_APP_HOST: 'http://127.1' }, generated)?.host,
  ).toBe(analyticsConfig.host);
  expect(diagnosticAppDestination({ ...env, SUPERMETRY_APP_ID: 'INVALID' }, generated)).toBeNull();
  expect(diagnosticAppDestination({ ...env, SUPERMETRY_INGEST_KEY: '' }, generated)).toBeNull();
  for (const host of ['http://localhost:4567', 'http://127.0.0.1:4567', 'http://[::1]:4567'])
    expect(diagnosticAppDestination({ ...env, SUPERMETRY_APP_HOST: host }, generated)?.host).toBe(
      host,
    );
  expect(
    diagnosticAppDestination(env, { ...generated, diagnostics: { sessionPings: 'true' } })
      ?.sessionPings,
  ).toBe(false);
});

it('does not add session events to legacy fallback even with sessionPings configured', async () => {
  const send = vi.fn(
    async (_url: unknown, _init?: RequestInit) => new Response('', { status: 202 }),
  );
  const reports = reporter({ send, env: {}, appConfig: { diagnostics: { sessionPings: true } } });
  await reports.initialize();
  await reports.setConsent(true);
  await reports.flush();
  expect(send).not.toHaveBeenCalled();
  reports.capture({ category: 'app_crash' });
  await reports.flush();
  expect(JSON.parse(String(send.mock.calls[0][1]?.body)).name).toBe('diagnostic_report');
  expect(JSON.parse(String(send.mock.calls[0][1]?.body))).not.toHaveProperty('events');
});

it('never stores the app ingest key, app host or arbitrary server reply text', async () => {
  const send = vi.fn(
    async () =>
      new Response(JSON.stringify({ private_message: 'PRIVATE_SERVER_MESSAGE' }), { status: 401 }),
  );
  const reports = reporter({ send });
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'app_crash' });
  await reports.flush();
  const text =
    (await readFile(join(directory, 'outbox.json'), 'utf8')) +
    (await readFile(join(directory, 'session.json'), 'utf8'));
  expect(text).not.toContain(env.SUPERMETRY_INGEST_KEY);
  expect(text).not.toContain(env.SUPERMETRY_APP_HOST);
  expect(text).not.toContain('PRIVATE_SERVER_MESSAGE');
});

it('bounds mapped server fields and omits oversized tag values', () => {
  const event = diagnosticAppFields({
    category: 'operation_failed',
    source: 'x'.repeat(201),
    app_version: '1'.repeat(65),
    build_commit: 'a'.repeat(65),
    error_type: 'E'.repeat(65),
    device_family: 'x'.repeat(33),
    reason: 'x'.repeat(33),
    signal: 'x'.repeat(33),
  });
  for (const key of ['release', 'build', 'error_type', 'device_class', 'exit_reason', 'signal'])
    expect(event).not.toHaveProperty(key);
  expect(event.tags).toEqual({ category: 'operation_failed' });
});

it.each([false, true])(
  'retires reports expiring during a send before another retry (survivor=%s)',
  async (survivor) => {
    let now = 1_800_000_000_000;
    const outcomes: unknown[] = [];
    const send = vi.fn(async (_url: unknown, init?: RequestInit) => {
      const events = JSON.parse(String(init?.body)).events;
      if (send.mock.calls.length === 1) {
        now += 2;
        return new Response('', { status: 503 });
      }
      return new Response(
        JSON.stringify({
          accepted: 0,
          dropped: events.map((_: unknown, index: number) => ({ index, reason: 'duplicate' })),
        }),
        { status: 202 },
      );
    });
    const reports = reporter({
      send,
      now: () => now,
      onDeliveryResult: (result) => outcomes.push(result),
    });
    await reports.initialize();
    await reports.setConsent(true);
    reports.capture({ category: 'app_crash', exit_code: 1 });
    now += TTL_MS - 1;
    if (survivor) reports.capture({ category: 'app_crash', exit_code: 2 });
    await reports.flush();
    expect(send).toHaveBeenCalledTimes(survivor ? 2 : 1);
    expect(outcomes).toContainEqual({ reason: 'too_old', count: 1 });
    expect(reports.status().pending_count).toBe(0);
    if (survivor) {
      const first = JSON.parse(String(send.mock.calls[0][1]?.body)).events;
      const retried = JSON.parse(String(send.mock.calls[1][1]?.body)).events;
      expect(retried).toHaveLength(1);
      expect(retried[0]).toEqual(first[1]);
    }
  },
);

it.each([false, true])(
  'retires app-session correlation after externally changed consent (replacementON=%s)',
  async (replacementON) => {
    const send = vi.fn(async (_url: unknown, init?: RequestInit) =>
      accepted(JSON.parse(String(init?.body)).events.length),
    );
    const reports = reporter({ send, appConfig: generated });
    await reports.initialize();
    await reports.setConsent(true);
    await reports.flush();
    const original = JSON.parse(String(send.mock.calls[0][1]?.body)).events[0];
    const path = join(directory, 'consent.json');
    const consent = JSON.parse(await readFile(path, 'utf8'));
    await writeFile(
      path,
      JSON.stringify({
        ...consent,
        enabled: replacementON,
        epoch: 'f'.repeat(32),
        profile_id: 'install_' + 'e'.repeat(32),
      }),
      'utf8',
    );
    await reports.flush();
    expect(reports.status().enabled).toBe(false);
    await reports.setConsent(true);
    reports.capture({ category: 'operation_failed' });
    await reports.flush();
    const events = JSON.parse(String(send.mock.calls.at(-1)![1]?.body)).events;
    expect(events).toHaveLength(1);
    expect(events[0].install_id).not.toBe(original.install_id);
    expect(events[0]).not.toHaveProperty('session_id');
  },
);

it('preserves a proven same-grant app session across a temporary unreadable consent file', async () => {
  let now = 1_800_000_000_000;
  const send = vi.fn(async (_url: unknown, init?: RequestInit) =>
    accepted(JSON.parse(String(init?.body)).events.length),
  );
  const reports = reporter({ send, appConfig: generated, now: () => now });
  await reports.initialize();
  await reports.setConsent(true);
  await reports.flush();
  const original = JSON.parse(String(send.mock.calls[0][1]?.body)).events[0];
  storageGate.unreadConsent = true;
  await reports.flush();
  expect(reports.status().enabled).toBe(false);
  storageGate.unreadConsent = false;
  now += 60_001;
  await reports.flush();
  expect(reports.status().enabled).toBe(true);
  reports.capture({ category: 'operation_failed' });
  await reports.flush();
  const events = send.mock.calls.flatMap(([, init]) => JSON.parse(String(init?.body)).events);
  expect(events.filter((event: { kind: string }) => event.kind === 'session')).toHaveLength(1);
  expect(events.at(-1)).toMatchObject({
    install_id: original.install_id,
    session_id: original.session_id,
  });
});

it('does not retire the first app session merely because the initial saved choice was OFF', async () => {
  const send = vi.fn(async (_url: unknown, init?: RequestInit) =>
    accepted(JSON.parse(String(init?.body)).events.length),
  );
  const reports = reporter({ send, appConfig: generated });
  await reports.initialize();
  await reports.setConsent(false);
  await reports.flush();
  expect(send).not.toHaveBeenCalled();
  await reports.setConsent(true);
  await reports.flush();
  expect(JSON.parse(String(send.mock.calls[0][1]?.body)).events[0].kind).toBe('session');
});

it.each([
  ['darwin', 'amd64', 'macos', 'x64'],
  ['win32', 'aarch64', 'windows', 'arm64'],
  ['unrecognized', 'unrecognized', 'other', 'other'],
])(
  'normalizes app intake user-agent platform/arch: %s/%s',
  async (platform, arch, normalizedPlatform, normalizedArch) => {
    const send = vi.fn(async (_url: unknown, _init?: RequestInit) => accepted(1));
    const reports = reporter({ send, identity: { ...identity, platform, arch } });
    await reports.initialize();
    await reports.setConsent(true);
    reports.capture({ category: 'app_crash' });
    await reports.flush();
    expect(send.mock.calls[0][1]?.headers).toHaveProperty(
      'User-Agent',
      `VoiceStudio/0.5.9-beta.3 (${normalizedPlatform}; ${normalizedArch})`,
    );
  },
);

async function packagedMetadata(contents?: string | Uint8Array) {
  const resources = join(directory, 'resources');
  const config = join(resources, 'config');
  await mkdir(config, { recursive: true });
  Object.defineProperty(process, 'resourcesPath', { configurable: true, value: resources });
  const file = join(config, 'supermetry-app.generated.json');
  if (contents !== undefined) await writeFile(file, contents);
  return file;
}
const embeddedMetadata = {
  app_id: 'embedded1',
  ingest_key: 'smk_' + 'b'.repeat(39),
  host: 'https://embedded.invalid',
  diagnostics: { sessionPings: false },
};
const packagedConfig = {
  app_id: 'packaged1',
  ingest_key: 'smk_' + 'c'.repeat(39),
  host: 'https://packaged.invalid',
  diagnostics: { sessionPings: false },
};

it('loads packaged private metadata once at real reporter construction without copying its key into reports', async () => {
  vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', embeddedMetadata);
  const file = await packagedMetadata(JSON.stringify(packagedConfig));
  const stored: string[] = [];
  const send = vi.fn(async (_url: unknown, _init?: RequestInit) => {
    stored.push(await readFile(join(directory, 'outbox.json'), 'utf8'));
    return accepted(1);
  });
  const reports = reporter({ env: {}, send });
  await writeFile(file, JSON.stringify(embeddedMetadata));
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'engine_failed' });
  await reports.flush();
  expect(send).toHaveBeenCalledOnce();
  expect(send.mock.calls[0][0]).toBe('https://packaged.invalid/api/public/v1/app-events');
  const request = send.mock.calls[0][1] as RequestInit;
  expect(request.headers).toMatchObject({ 'X-Supermetry-Key': packagedConfig.ingest_key });
  expect(JSON.parse(String(request.body)).site).toBe(packagedConfig.app_id);
  expect(JSON.stringify([reports.status(), stored, request.body])).not.toContain(
    packagedConfig.ingest_key,
  );
});

it.each(['malformed', 'oversized', 'directory', 'invalid-utf8'] as const)(
  'treats present %s packaged metadata as absent credentials instead of rescuing embedded ones',
  async (kind) => {
    vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', embeddedMetadata);
    const file = await packagedMetadata();
    if (kind === 'directory') await mkdir(file);
    else if (kind === 'malformed') await writeFile(file, '{');
    else if (kind === 'invalid-utf8')
      await writeFile(
        file,
        Buffer.concat([
          Buffer.from(JSON.stringify(packagedConfig).slice(0, -1) + ',"ignored":"'),
          Buffer.from([0xff]),
          Buffer.from('"}'),
        ]),
      );
    else await writeFile(file, JSON.stringify(packagedConfig) + ' '.repeat(4096));
    expect(diagnosticAppDestination({})).toBeNull();
    const send = vi.fn(
      async (_url: unknown, _init?: RequestInit) => new Response('', { status: 202 }),
    );
    const reports = reporter({ env: {}, send });
    await reports.initialize();
    await reports.setConsent(true);
    reports.capture({ category: 'engine_failed' });
    await reports.flush();
    expect(send).toHaveBeenCalledOnce();
    expect(send.mock.calls[0][0]).toBe(legacy.host + '/api/public/v1/events');
    const request = send.mock.calls[0][1] as RequestInit;
    expect(request.headers).toEqual({
      'Content-Type': 'application/json',
      Origin: legacy.appUrl,
      'User-Agent':
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) VoiceStudio/1.0 Chrome/130.0.0.0 Safari/537.36',
    });
    expect(JSON.parse(String(request.body)).name).toBe('diagnostic_report');
  },
);

it.runIf(process.platform !== 'win32')(
  'rejects symlinked packaged metadata including a dangling link',
  async () => {
    vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', embeddedMetadata);
    const file = await packagedMetadata();
    const target = join(directory, 'other-config.json');
    await writeFile(target, JSON.stringify(packagedConfig));
    await symlink(target, file);
    expect(diagnosticAppDestination({})).toBeNull();
    await rm(target);
    expect(diagnosticAppDestination({})).toBeNull();
  },
);

it('uses embedded metadata only when packaged metadata is missing and creates no build config', async () => {
  vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', embeddedMetadata);
  await packagedMetadata();
  const before = await readdir(join(directory, 'resources', 'config'));
  expect(diagnosticAppDestination({})?.appId).toBe(embeddedMetadata.app_id);
  expect(await readdir(join(directory, 'resources', 'config'))).toEqual(before);
  Object.defineProperty(process, 'resourcesPath', { configurable: true, value: undefined });
  expect(diagnosticAppDestination({})?.appId).toBe(embeddedMetadata.app_id);
  vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', undefined);
  expect(diagnosticAppDestination({})).toBeNull();
});

it('preserves environment and explicit-option precedence over packaged metadata', async () => {
  vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', embeddedMetadata);
  await packagedMetadata(JSON.stringify(packagedConfig));
  expect(diagnosticAppDestination(env)).toMatchObject({
    appId: env.SUPERMETRY_APP_ID,
    host: env.SUPERMETRY_APP_HOST,
  });
  expect(diagnosticAppDestination({ SUPERMETRY_APP_ID: 'invalid!' })).toBeNull();
  expect(diagnosticAppDestination({ SUPERMETRY_APP_HOST: 'http://public.invalid' })?.host).toBe(
    analyticsConfig.host,
  );
  const send = vi.fn(
    async (_url: unknown, _init?: RequestInit) => new Response('', { status: 202 }),
  );
  const reports = reporter({ env: {}, appConfig: {}, send });
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'engine_failed' });
  await reports.flush();
  expect(send.mock.calls[0][0]).toBe(legacy.host + '/api/public/v1/events');
});

it.runIf(process.platform !== 'win32')(
  'rejects FIFO packaged metadata without waiting for a writer',
  async () => {
    vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', embeddedMetadata);
    const file = await packagedMetadata();
    execFileSync('mkfifo', [file]);
    expect(diagnosticAppDestination({})).toBeNull();
  },
);

it.runIf(process.platform !== 'win32' && process.getuid?.() !== 0)(
  'does not rescue embedded credentials when the packaged file cannot be read',
  async () => {
    vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', embeddedMetadata);
    const file = await packagedMetadata(JSON.stringify(packagedConfig));
    await chmod(file, 0);
    try {
      expect(diagnosticAppDestination({})).toBeNull();
    } finally {
      await chmod(file, 0o600);
    }
  },
);

it('rejects metadata replaced after opening and closes the original descriptor', async () => {
  vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', embeddedMetadata);
  await packagedMetadata(JSON.stringify(packagedConfig));
  storageGate.afterMetadataOpen = (file) => {
    renameSync(file, file + '.previous');
    writeFileSync(file, JSON.stringify(embeddedMetadata));
  };
  expect(diagnosticAppDestination({})).toBeNull();
  expect(storageGate.metadataOpenedFd).toBeTypeOf('number');
  expect(() => fstatSync(storageGate.metadataOpenedFd!)).toThrow();
});

it.runIf(process.platform !== 'win32')(
  'rejects linked metadata without native NOFOLLOW support and still closes its descriptor',
  async () => {
    vi.stubGlobal('__SUPERMETRY_APP_CONFIG__', embeddedMetadata);
    const file = await packagedMetadata();
    const target = join(directory, 'actual-metadata.json');
    await writeFile(target, JSON.stringify(packagedConfig));
    await symlink(target, file);
    // Exercise the real open/fstat/lstat path where this flag is unavailable.
    storageGate.withoutNoFollow = true;
    expect(diagnosticAppDestination({})).toBeNull();
    expect(storageGate.metadataOpenedFd).toBeTypeOf('number');
    expect(() => fstatSync(storageGate.metadataOpenedFd!)).toThrow();
    await rm(target);
    expect(diagnosticAppDestination({})).toBeNull();
  },
);
