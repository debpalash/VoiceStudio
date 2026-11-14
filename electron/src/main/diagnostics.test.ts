import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { EventEmitter } from 'node:events';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { DiagnosticReports, type DiagnosticsOptions } from './diagnostics';
import {
  DiagnosticStatusObserver,
  installDiagnosticProcessCapture,
  captureProcessGone,
} from './diagnostics-capture';
import { sanitizeProperties, sanitizeFrames, validUiInput } from './diagnostics-schema';
import type { BackendStatus } from '../preload/index.d';

let root: string;
let instances: DiagnosticReports[];
const identity = {
  app_version: '0.5.9-beta.3',
  build_commit: 'abcdef012',
  platform: 'linux',
  arch: 'x64',
};
const destination = {
  site: 'fixture-only',
  host: 'https://diagnostic-test.invalid',
  appUrl: 'https://app-test.invalid',
};
const success = () =>
  vi.fn(async (_url: unknown, _options?: RequestInit) => new Response('', { status: 202 }));
function reporter(options: Partial<DiagnosticsOptions> = {}) {
  const instance = new DiagnosticReports({
    directory: join(root, 'diagnostics'),
    identity,
    destination,
    send: success(),
    env: {},
    automatic: false,
    ...options,
  });
  instances.push(instance);
  return instance;
}
async function json(name: string) {
  return JSON.parse(await readFile(join(root, 'diagnostics', name), 'utf8'));
}
async function exists(name: string) {
  return readFile(join(root, 'diagnostics', name)).then(
    () => true,
    () => false,
  );
}
const failure = (extra: Partial<BackendStatus> = {}): BackendStatus =>
  ({
    stage: 'setup_required',
    managed: true,
    remote: false,
    port: 3900,
    baseUrl: 'http://127.0.0.1:3900',
    elapsedMs: 120_000,
    logTail: ['PRIVATE_TRANSCRIPT'],
    message: '/home/private-user/PRIVATE_KEY_TOKEN',
    failure: {
      code: 'RUNTIME_DOWNLOAD_STALLED',
      stage: 'setup_required',
      downloadHost: 'SECRET_PRIVATE_HOST',
      program: 'PRIVATE_MODEL_NAME',
    },
    ...extra,
  }) as BackendStatus;

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'voice-diagnostics-'));
  instances = [];
});
afterEach(async () => {
  await Promise.all(instances.map((instance) => instance.close().catch(() => {})));
  await rm(root, { recursive: true, force: true });
  vi.useRealTimers();
});

describe('independent desktop diagnostic permission', () => {
  it('collects nothing without explicit consent, even when product analytics was enabled', async () => {
    const send = success();
    const reports = reporter({
      send,
      env: { SUPERMETRY_SITE_ID: 'fixture-only', OMNIVOICE_ANALYTICS_ENABLED: '1' },
    });
    await reports.initialize();
    new DiagnosticStatusObserver(reports).observe(failure());
    await reports.flush();
    expect(reports.status()).toMatchObject({
      enabled: false,
      opted_in: false,
      prompted: false,
      pending_count: 0,
    });
    expect(send).not.toHaveBeenCalled();
    expect(await exists('outbox.json')).toBe(false);
    expect(await exists('consent.json')).toBe(false);
    const events = new EventEmitter();
    const stop = installDiagnosticProcessCapture(reports, events);
    const stack = vi.fn(() => 'PRIVATE_STACK');
    const error = new Error();
    Object.defineProperty(error, 'stack', { get: stack });
    events.emit('uncaughtExceptionMonitor', error, 'uncaughtException');
    expect(stack).not.toHaveBeenCalled();
    stop();
  });

  it.each(['OMNIVOICE_DIAGNOSTICS_DISABLED', 'OMNIVOICE_ANALYTICS_DISABLED'])(
    'honors %s independently of saved opt-in',
    async (key) => {
      const send = success();
      const reports = reporter({ send, env: { [key]: '1' } });
      await reports.initialize();
      await reports.setConsent(true);
      new DiagnosticStatusObserver(reports).observe(failure());
      await reports.flush();
      expect(reports.status()).toMatchObject({
        opted_in: false,
        enabled: false,
        available: false,
        pending_count: 0,
      });
      expect(send).not.toHaveBeenCalled();
      expect(await exists('consent.json')).toBe(false);
    },
  );

  it.each(['OMNIVOICE_DIAGNOSTICS_DISABLED', 'OMNIVOICE_ANALYTICS_DISABLED'])(
    'revokes a persisted grant on a %s launch and requires a fresh choice after relaunch',
    async (key) => {
      const first = reporter();
      await first.initialize();
      await first.setConsent(true);
      const old = await json('consent.json');
      first.capture({ category: 'generation_failed', stage: 'generation' });
      await first.close();
      expect((await json('outbox.json')).records).toHaveLength(1);
      const send = success();
      const killedRun = reporter({ send, env: { [key]: '1' } });
      await killedRun.initialize();
      await killedRun.setConsent(true);
      expect(killedRun.status()).toMatchObject({
        enabled: false,
        opted_in: false,
        available: false,
      });
      await killedRun.close();
      // A crash during the killed period must not become old-epoch recovery.
      await writeFile(
        join(root, 'diagnostics', 'session.json'),
        JSON.stringify({
          version: 1,
          epoch: old.epoch,
          active: true,
          timestamp_ms: Date.now(),
        }),
      );
      const next = reporter({ send });
      await next.initialize();
      next.capture({ category: 'backend_crash', stage: 'startup' });
      await next.flush();
      expect(next.status()).toMatchObject({
        enabled: false,
        opted_in: false,
        prompted: true,
        pending_count: 0,
      });
      expect(send).not.toHaveBeenCalled();
      expect(await exists('session.json')).toBe(false);
      expect(await exists('outbox.json')).toBe(false);
      await next.setConsent(true);
      const current = await json('consent.json');
      expect(current.epoch).not.toBe(old.epoch);
      expect(current.profile_id).not.toBe(old.profile_id);
      next.capture({ category: 'operation_failed', stage: 'other' });
      await next.flush();
      expect(send).toHaveBeenCalledTimes(1);
      expect(JSON.parse(String(send.mock.calls[0][1]?.body)).props.category).toBe(
        'operation_failed',
      );
    },
  );

  it.each(['OMNIVOICE_DIAGNOSTICS_DISABLED', 'OMNIVOICE_ANALYTICS_DISABLED'])(
    'latches a live %s observation until durable revocation or a fresh explicit grant',
    async (key) => {
      const env: NodeJS.ProcessEnv = {};
      const send = success();
      const reports = reporter({ env, send });
      await reports.initialize();
      await reports.setConsent(true);
      const old = await json('consent.json');
      reports.capture({ category: 'generation_failed', stage: 'generation' });
      env[key] = '1';
      reports.capture({ category: 'backend_crash', stage: 'startup' });
      delete env[key];
      await reports.flush();
      expect(reports.status()).toMatchObject({ enabled: false, opted_in: false, pending_count: 0 });
      expect(send).not.toHaveBeenCalled();
      await reports.setConsent(true);
      expect((await json('consent.json')).epoch).not.toBe(old.epoch);
      reports.capture({ category: 'operation_failed', stage: 'other' });
      await reports.flush();
      expect(send).toHaveBeenCalledTimes(1);
    },
  );

  it('persists revocation when a kill is observed during an active delivery', async () => {
    const env: NodeJS.ProcessEnv = {};
    let entered!: () => void;
    let release!: () => void;
    const entering = new Promise<void>((resolve) => {
      entered = resolve;
    });
    const completed = new Promise<void>((resolve) => {
      release = resolve;
    });
    const send = vi.fn(async () => {
      entered();
      await completed;
      return new Response('', { status: 202 });
    });
    const reports = reporter({ env, send });
    await reports.initialize();
    await reports.setConsent(true);
    reports.capture({ category: 'generation_failed', stage: 'generation' });
    const work = reports.flush();
    await entering;
    env.OMNIVOICE_DIAGNOSTICS_DISABLED = '1';
    expect(reports.status().enabled).toBe(false);
    delete env.OMNIVOICE_DIAGNOSTICS_DISABLED;
    release();
    await work;
    expect((await json('consent.json')).enabled).toBe(false);
    const restarted = reporter({ send });
    await restarted.initialize();
    await restarted.flush();
    expect(restarted.status().enabled).toBe(false);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('delivers an actual classified setup failure while Python is permanently unavailable', async () => {
    const send = success();
    const reports = reporter({ send });
    await reports.initialize();
    await reports.setConsent(true);
    const observer = new DiagnosticStatusObserver(reports);
    observer.observe(failure());
    observer.observe(failure());
    await reports.flush();
    expect(send).toHaveBeenCalledTimes(1);
    const [url, options] = send.mock.calls[0];
    const event = JSON.parse(String(options?.body));
    expect(url).toBe('https://diagnostic-test.invalid/api/public/v1/events');
    expect(event).toMatchObject({
      name: 'diagnostic_report',
      v: 1,
      url: 'https://app-test.invalid/diagnostics',
      props: {
        category: 'install_stalled',
        error_code: 'RUNTIME_DOWNLOAD_STALLED',
        source: 'electron_main',
      },
    });
    expect(event.profile_id).toMatch(/^install_[a-f0-9]{32}$/);
    expect(JSON.stringify(event)).not.toMatch(/PRIVATE|SECRET|127\.0\.0\.1|private-user/);
    expect(options).toMatchObject({
      redirect: 'manual',
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
    });
    expect(options?.headers).toMatchObject({
      'Content-Type': 'application/json',
      Origin: 'https://app-test.invalid',
      'User-Agent': expect.stringContaining('VoiceStudio/1.0'),
    });
    expect(reports.status().pending_count).toBe(0);
  });

  it('keeps report ID and original timestamp through an offline relaunch', async () => {
    const first = reporter();
    await first.initialize();
    await first.setConsent(true);
    first.capture({
      category: 'engine_failed',
      stage: 'engine',
      engine_id: 'omnivoice',
      error_type: 'RuntimeError',
    });
    await first.close();
    const queued = (await json('outbox.json')).records[0];
    const send = success();
    const second = reporter({ send });
    await second.initialize();
    await second.flush();
    const event = JSON.parse(String(send.mock.calls[0][1]?.body));
    expect(event.id).toBe(queued.id);
    expect(event.ts).toBe(queued.timestamp_ms);
    expect(event).not.toHaveProperty('epoch');
  });

  it('rotates identity and deletes old reports on withdrawal and re-enable', async () => {
    const reports = reporter();
    await reports.initialize();
    await reports.setConsent(true);
    const old = await json('consent.json');
    reports.capture({ category: 'operation_failed', stage: 'other' });
    await reports.setConsent(false);
    expect(await exists('outbox.json')).toBe(false);
    expect(await exists('session.json')).toBe(false);
    await reports.setConsent(true);
    const current = await json('consent.json');
    expect(current.epoch).not.toBe(old.epoch);
    expect(current.profile_id).not.toBe(old.profile_id);
    expect(reports.status().pending_count).toBe(0);
    await mkdir(join(root, 'diagnostics', 'inbox'), { recursive: true });
    await writeFile(
      join(root, 'diagnostics', 'inbox', `${'a'.repeat(32)}.json`),
      JSON.stringify({
        version: 1,
        id: 'a'.repeat(32),
        epoch: old.epoch,
        timestamp_ms: Date.now(),
        props: { category: 'backend_crash', source: 'backend' },
      }),
    );
    await reports.flush();
    expect(reports.status().pending_count).toBe(0);
  });

  it('aborts a connecting request and never retries or resurrects its queue after opt-out', async () => {
    let started!: () => void;
    const entering = new Promise<void>((resolve) => {
      started = resolve;
    });
    const send = vi.fn(async (_url: unknown, options?: RequestInit): Promise<Response> => {
      started();
      return new Promise((_resolve, reject) =>
        options?.signal?.addEventListener('abort', () => reject(new Error('aborted')), {
          once: true,
        }),
      );
    });
    const reports = reporter({ send });
    await reports.initialize();
    await reports.setConsent(true);
    reports.capture({ category: 'backend_crash', stage: 'startup' });
    const work = reports.flush();
    await entering;
    await reports.setConsent(false);
    await work;
    expect(send).toHaveBeenCalledTimes(1);
    expect(reports.status()).toMatchObject({ enabled: false, pending_count: 0 });
    expect(await exists('outbox.json')).toBe(false);
    await reports.flush();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('stops an already scheduled retry backoff immediately on withdrawal', async () => {
    const send = vi.fn(async () => new Response('', { status: 503 }));
    const reports = reporter({ send });
    await reports.initialize();
    await reports.setConsent(true);
    vi.useFakeTimers();
    reports.capture({ category: 'backend_crash' });
    const work = reports.flush();
    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(0);
    await reports.setConsent(false);
    await work;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(send).toHaveBeenCalledTimes(1);
    expect(await exists('outbox.json')).toBe(false);
  });
});

describe('strict data projection and durable recovery', () => {
  it('validates values, not just property names or identifier-shaped strings', () => {
    const props = sanitizeProperties({
      category: 'engine_failed',
      source: 'backend',
      engine_id: 'hf_ALPHANUMERIC_PRIVATE_TOKEN',
      error_code: 'PRIVATE_TRANSCRIPT',
      error_type: 'PrivateUserException',
      device_family: 'SECRET_GPU_NAME',
      frames: 'backend/PRIVATE_PROJECT.py:12\nbackend/services/model_manager.py:123',
      message: 'PRIVATE_PROMPT',
      fingerprint: 'secret',
      occurrences: Infinity,
    });
    expect(props).toMatchObject({
      engine_id: 'unknown',
      error_code: 'UNKNOWN',
      error_type: 'OtherError',
      device_family: 'unknown',
      occurrences: 1,
      frames: 'backend/services/model_manager.py:123',
    });
    expect(JSON.stringify(props)).not.toMatch(/PRIVATE|SECRET|secret/);
    expect(sanitizeFrames('assets/PRIVATE_SECRET_HASH.js:20:30')).toBe('renderer:20:30');
    expect(validUiInput({ category: 'operation_failed', message: 'secret' })).toBe(false);
    expect(validUiInput({ category: 'operation_failed', source: 'backend' })).toBe(false);
  });

  it('imports only sanitized same-epoch inbox records and never trusts extra persisted fields', async () => {
    const send = success();
    const reports = reporter({ send });
    await reports.initialize();
    await reports.setConsent(true);
    const consent = await json('consent.json');
    await mkdir(join(root, 'diagnostics', 'inbox'), { recursive: true });
    await writeFile(
      join(root, 'diagnostics', 'inbox', `${'b'.repeat(32)}.json`),
      JSON.stringify({
        version: 1,
        id: 'b'.repeat(32),
        timestamp_ms: Date.now(),
        epoch: consent.epoch,
        props: {
          category: 'model_load_failed',
          source: 'backend',
          engine_id: 'omnivoice',
          error_type: 'ValueError',
          message: 'PRIVATE_AUDIO_TRANSCRIPT',
          frames: '/home/secret/userfile.py:5\nbackend/services/model_manager.py:10',
        },
      }),
    );
    await reports.flush();
    const event = JSON.parse(String(send.mock.calls[0][1]?.body));
    expect(event.props).toMatchObject({
      category: 'model_load_failed',
      engine_id: 'omnivoice',
      frames: 'backend/services/model_manager.py:10',
    });
    expect(JSON.stringify(event)).not.toMatch(/PRIVATE|secret|userfile/);
  });

  it('deduplicates a producer ID left in both inbox and outbox by a crash during handoff', async () => {
    const first = reporter();
    await first.initialize();
    await first.setConsent(true);
    await first.close();
    const consent = await json('consent.json');
    const record = {
      version: 1,
      id: 'c'.repeat(32),
      timestamp_ms: Date.now(),
      epoch: consent.epoch,
      props: {
        category: 'engine_failed',
        source: 'backend',
        engine_id: 'omnivoice',
        occurrences: 1,
      },
    };
    await writeFile(
      join(root, 'diagnostics', 'outbox.json'),
      JSON.stringify({ version: 1, records: [record] }),
    );
    await mkdir(join(root, 'diagnostics', 'inbox'), { recursive: true });
    await writeFile(
      join(root, 'diagnostics', 'inbox', `${record.id}.json`),
      JSON.stringify(record),
    );
    const send = success();
    const second = reporter({ send });
    await second.initialize();
    await second.flush();
    expect(send).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(send.mock.calls[0][1]?.body))).toMatchObject({
      id: record.id,
      props: { occurrences: 1 },
    });
  });

  it('coalesces identical failures and caps a flood without persisting raw input', async () => {
    const reports = reporter();
    await reports.initialize();
    await reports.setConsent(true);
    for (let n = 0; n < 100; n++)
      reports.capture({ category: 'generation_failed', stage: 'generation' });
    expect(reports.status().pending_count).toBe(1);
    for (let n = 1; n <= 100; n++)
      reports.capture({
        category: 'generation_failed',
        stage: 'generation',
        frames: `backend/services/model_manager.py:${n}`,
      });
    expect(reports.status().pending_count).toBe(32);
    expect(reports.status().dropped_count).toBeGreaterThan(0);
    await reports.close();
    expect(
      Buffer.byteLength(await readFile(join(root, 'diagnostics', 'outbox.json'), 'utf8')),
    ).toBeLessThanOrEqual(512 * 1024);
  });

  it('fails closed on corrupt consent rather than treating queued data as permission', async () => {
    await mkdir(join(root, 'diagnostics'), { recursive: true });
    await writeFile(join(root, 'diagnostics', 'consent.json'), '{broken');
    await writeFile(
      join(root, 'diagnostics', 'outbox.json'),
      JSON.stringify({ enabled: true, records: [{ message: 'secret' }] }),
    );
    const send = success();
    const reports = reporter({ send });
    await reports.initialize();
    reports.capture({ category: 'app_crash' });
    await reports.flush();
    expect(reports.status()).toMatchObject({ enabled: false, prompted: false });
    expect(send).not.toHaveBeenCalled();
    expect(await exists('outbox.json')).toBe(false);
  });

  it('recovers only a consent-stamped unclean session and extracts app-owned frames, never raw journal messages', async () => {
    const first = reporter();
    await first.initialize();
    await first.setConsent(true);
    await writeFile(
      join(root, 'main-process-errors.json'),
      JSON.stringify([
        {
          at: new Date().toISOString(),
          version: identity.app_version,
          origin: 'uncaughtException',
          detail:
            'TypeError: PRIVATE_TRANSCRIPT\n at load (/home/PRIVATE_USER/electron/src/main/index.ts:123:4)\n at user (/home/secret/project.js:1:2)',
        },
      ]),
    );
    const send = success();
    const second = reporter({ send });
    await second.initialize();
    await second.flush();
    const event = JSON.parse(String(send.mock.calls[0][1]?.body));
    expect(event.props).toMatchObject({
      category: 'app_crash',
      error_type: 'TypeError',
      reason: 'unhandled_exception',
      frames: 'electron/src/main/index.ts:123:4',
    });
    expect(JSON.stringify(event)).not.toMatch(/PRIVATE|secret|project\.js/);
  });

  it('does not turn an orderly exit or a pre-consent old journal into a crash report', async () => {
    const first = reporter();
    await first.initialize();
    await first.setConsent(true);
    await first.close();
    await writeFile(
      join(root, 'main-process-errors.json'),
      JSON.stringify([
        {
          at: new Date().toISOString(),
          version: identity.app_version,
          origin: 'uncaughtException',
          detail: 'Error: private old history',
        },
      ]),
    );
    const send = success();
    const second = reporter({ send });
    await second.initialize();
    await second.flush();
    expect(send).not.toHaveBeenCalled();
  });

  it('attributes recovered exits to the previous build rather than the version that discovers it', async () => {
    const first = reporter();
    await first.initialize();
    await first.setConsent(true);
    const send = success();
    const second = reporter({ send, identity: { ...identity, app_version: '0.5.9-beta.4' } });
    await second.initialize();
    await second.flush();
    expect(JSON.parse(String(send.mock.calls[0][1]?.body)).props.app_version).toBe(
      identity.app_version,
    );
  });

  it('captures fatal JS/native process reasons without doing network work in the handler or counting expected exits', async () => {
    const send = success();
    const reports = reporter({ send });
    await reports.initialize();
    await reports.setConsent(true);
    const events = new EventEmitter();
    const stop = installDiagnosticProcessCapture(reports, events);
    events.emit('uncaughtExceptionMonitor', new TypeError('PRIVATE_PROMPT'), 'uncaughtException');
    events.emit(
      'unhandledRejection',
      Object.assign(new Error('PRIVATE_CANCEL'), { name: 'AbortError' }),
    );
    captureProcessGone(reports, true, 'clean-exit', 0);
    captureProcessGone(reports, false, 'killed', 9, true);
    captureProcessGone(reports, true, 'oom', 1);
    expect(send).not.toHaveBeenCalled();
    expect(reports.status().pending_count).toBe(2);
    stop();
    await reports.flush();
    expect(send).toHaveBeenCalledTimes(2);
    expect(JSON.stringify(send.mock.calls)).not.toMatch(/PRIVATE_PROMPT|PRIVATE_CANCEL/);
  });

  it('does not call a slow or canceled install stalled without actual no-progress evidence', async () => {
    const send = success();
    const reports = reporter({ send });
    await reports.initialize();
    await reports.setConsent(true);
    const observer = new DiagnosticStatusObserver(reports);
    observer.observe(
      failure({
        stage: 'installing',
        failure: undefined,
        elapsedMs: 3_600_000,
        setupProgress: { completedDownloads: 2, downloadedBytes: 100, stalled: false },
      }),
    );
    observer.observe(
      failure({ stage: 'setup_required', failure: undefined, setupIssue: undefined }),
    );
    await reports.flush();
    expect(send).not.toHaveBeenCalled();
    observer.observe(
      failure({
        stage: 'installing',
        failure: undefined,
        setupProgress: { completedDownloads: 2, stalled: true, stalledSeconds: 700 },
      }),
    );
    await reports.flush();
    expect(send).toHaveBeenCalledTimes(1);
  });
});

it('fails closed and clears collection when consent becomes unreadable after startup', async () => {
  const send = success();
  const reports = reporter({ send });
  await reports.initialize();
  await reports.setConsent(true);
  reports.capture({ category: 'app_crash' });
  await writeFile(join(root, 'diagnostics', 'consent.json'), '{broken');
  await reports.flush();
  expect(send).not.toHaveBeenCalled();
  expect(reports.status()).toMatchObject({ enabled: false, pending_count: 0 });
  reports.capture({ category: 'app_crash' });
  expect(reports.status().pending_count).toBe(0);
  expect(await exists('outbox.json')).toBe(false);
});

it('expires offline records after the seven-day retention window', async () => {
  const first = reporter();
  await first.initialize();
  await first.setConsent(true);
  first.capture({ category: 'operation_failed' });
  await first.close();
  const send = success();
  const second = reporter({ send, now: () => Date.now() + 8 * 86400_000 });
  await second.initialize();
  await second.flush();
  expect(send).not.toHaveBeenCalled();
  expect(second.status().pending_count).toBe(0);
});

it('bounds a failed delivery cycle to three attempts and retains the report for later', async () => {
  const send = vi.fn(async () => new Response('', { status: 503 }));
  const reports = reporter({ send });
  await reports.initialize();
  await reports.setConsent(true);
  vi.useFakeTimers();
  reports.capture({ category: 'operation_failed' });
  const work = reports.flush();
  await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(3), { timeout: 5000 });
  await work;
  expect(reports.status().pending_count).toBe(1);
  await reports.flush();
  expect(send).toHaveBeenCalledTimes(3);
  await reports.setConsent(false);
  expect(reports.status().pending_count).toBe(0);
});

it('does not attribute a later native exit to an earlier nonfatal rejected task', async () => {
  const first = reporter();
  await first.initialize();
  await first.setConsent(true);
  await writeFile(
    join(root, 'main-process-errors.json'),
    JSON.stringify([
      {
        at: new Date().toISOString(),
        version: identity.app_version,
        origin: 'unhandledRejection',
        detail: 'TypeError: PRIVATE_OLD_TASK',
      },
    ]),
  );
  const send = success();
  const second = reporter({ send });
  await second.initialize();
  await second.flush();
  expect(JSON.parse(String(send.mock.calls[0][1]?.body)).props.reason).toBe('unclean_exit');
});

it('does not duplicate a fatal report already persisted before the process died', async () => {
  const first = reporter();
  await first.initialize();
  await first.setConsent(true);
  const marker = await json('session.json');
  first.capture({
    category: 'app_crash',
    stage: 'other',
    reason: 'unhandled_exception',
    error_type: 'TypeError',
  });
  await first.close();
  // Disk state if the process dies after report commit but before marker removal.
  await writeFile(join(root, 'diagnostics', 'session.json'), JSON.stringify(marker));
  const send = success();
  const second = reporter({ send });
  await second.initialize();
  await second.flush();
  expect(send).toHaveBeenCalledTimes(1);
});

it('uses the real local HTTP intake without a backend or any production event', async () => {
  const bodies: string[] = [];
  const requests: string[] = [];
  const server = createServer((request, response) => {
    requests.push(request.url ?? '');
    if (request.url === '/private') {
      response.writeHead(200);
      response.end();
      return;
    }
    let body = '';
    request.on('data', (chunk) => {
      body += String(chunk);
    });
    request.on('end', () => {
      bodies.push(body);
      response.writeHead(JSON.parse(body).props.category === 'renderer_crash' ? 307 : 202, {
        Location: '/private',
      });
      response.end();
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  try {
    const port = (server.address() as AddressInfo).port;
    const reports = reporter({
      destination: { ...destination, host: `http://127.0.0.1:${port}` },
      send: fetch,
    });
    await reports.initialize();
    await reports.setConsent(true);
    new DiagnosticStatusObserver(reports).observe(failure());
    await reports.flush();
    expect(bodies).toHaveLength(1);
    expect(JSON.parse(bodies[0]).site).toBe('fixture-only');
    reports.capture({ category: 'renderer_crash', stage: 'render', reason: 'crashed' });
    await reports.flush();
    expect(requests).toEqual(['/api/public/v1/events', '/api/public/v1/events']);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
