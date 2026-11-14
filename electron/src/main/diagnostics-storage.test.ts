import { afterEach, expect, it, vi } from 'vitest';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { tmpdir } from 'node:os';
const gate = vi.hoisted(() => ({
  enabled: false,
  claimed: false,
  entered: () => {},
  wait: Promise.resolve() as Promise<void>,
  failWrites: false,
  failRenameTarget: '',
  failRemoveTarget: '',
  sessionWrites: 0,
  sessionReads: 0,
  failReadTarget: '',
  failReadCode: 'EACCES',
  removeOnReadTarget: '',
  recordReads: new Map<string, number>(),
  outboxReads: 0,
  publications: [] as string[],
  failDirectorySync: false,
  blockedRead: null as null | { entered: () => void; wait: Promise<void> },
}));
vi.mock('node:fs/promises', async (original) => {
  const fs = await original<typeof import('node:fs/promises')>();
  return {
    ...fs,
    rm: async (...args: Parameters<typeof fs.rm>) => {
      if (gate.failRemoveTarget && String(args[0]).endsWith(gate.failRemoveTarget))
        throw Object.assign(new Error('EACCES'), { code: 'EACCES' });
      return fs.rm(...args);
    },
    rename: async (...args: Parameters<typeof fs.rename>) => {
      if (String(args[1]).endsWith('session.json')) gate.sessionWrites++;
      if (gate.failRenameTarget && String(args[1]).endsWith(gate.failRenameTarget))
        throw Object.assign(new Error('ENOSPC'), { code: 'ENOSPC' });
      await fs.rename(...args);
      gate.publications.push(`rename:${basename(String(args[1]))}`);
    },
    open: async (...args: Parameters<typeof fs.open>) => {
      if (String(args[0]).endsWith('outbox.json')) gate.outboxReads++;
      if (String(args[0]).endsWith('session.json')) gate.sessionReads++;
      const name = basename(String(args[0]));
      if (/^[a-f0-9]{32}\.json$/.test(name))
        gate.recordReads.set(name, (gate.recordReads.get(name) ?? 0) + 1);
      if (gate.removeOnReadTarget && String(args[0]).endsWith(gate.removeOnReadTarget)) {
        gate.removeOnReadTarget = '';
        await fs.rm(String(args[0]), { force: true });
      }
      if (gate.failReadTarget && String(args[0]).endsWith(gate.failReadTarget))
        throw Object.assign(new Error('Temporary read lock'), { code: gate.failReadCode });
      const handle = await fs.open(...args);
      if (String(args[0]).endsWith('outbox.json') && gate.blockedRead) {
        const blocked = gate.blockedRead;
        gate.blockedRead = null;
        blocked.entered();
        await blocked.wait;
      }
      const directory = (await handle.stat()).isDirectory();
      const close = handle.close.bind(handle);
      handle.close = async () => {
        if (directory) gate.publications.push('directory-close');
        return close();
      };
      const write = handle.writeFile.bind(handle);
      handle.writeFile = (async (...values: Parameters<typeof write>) => {
        if (gate.failWrites) throw Object.assign(new Error('ENOSPC'), { code: 'ENOSPC' });
        return write(...values);
      }) as typeof handle.writeFile;
      const sync = handle.sync.bind(handle);
      handle.sync = async () => {
        gate.publications.push(directory ? 'directory-sync' : 'file-sync');
        if (directory && gate.failDirectorySync)
          throw Object.assign(new Error('Directory sync failed'), { code: 'EIO' });
        if (gate.enabled && !gate.claimed) {
          gate.claimed = true;
          gate.entered();
          await gate.wait;
        }
        return sync();
      };
      return handle;
    },
  };
});
import { DiagnosticReports, diagnosticDestination } from './diagnostics';
import { MAX_RECORDS } from './diagnostics-schema';
let root: string | undefined;
let reports: DiagnosticReports | undefined;
afterEach(async () => {
  gate.enabled = false;
  gate.failWrites = false;
  gate.failRenameTarget = '';
  gate.failRemoveTarget = '';
  gate.sessionWrites = 0;
  gate.sessionReads = 0;
  gate.failReadTarget = '';
  gate.failReadCode = 'EACCES';
  gate.removeOnReadTarget = '';
  gate.recordReads.clear();
  gate.outboxReads = 0;
  gate.publications = [];
  gate.failDirectorySync = false;
  gate.blockedRead = null;
  await reports?.close();
  if (root) await rm(root, { recursive: true, force: true });
});
async function create(optIn = true) {
  root = await mkdtemp(join(tmpdir(), 'voice-diagnostic-disk-'));
  const send = vi.fn(
    async (_url: unknown, _request?: RequestInit) => new Response('', { status: 202 }),
  );
  const options = {
    directory: join(root, 'diagnostics'),
    identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' },
    env: {},
    destination: {
      site: 'fixture',
      host: 'https://fixture.invalid',
      appUrl: 'https://app-fixture.invalid',
    },
    send,
    automatic: false,
  };
  reports = new DiagnosticReports(options);
  await reports.initialize();
  if (optIn) await reports.setConsent(true);
  return { reports, send, root, options };
}

it('recovers a recent crash after more than a week of active opted-in runtime', async () => {
  const initial = await create(false);
  let now = 1_794_640_000_000;
  await initial.reports.close();
  const options = { ...initial.options, now: () => now };
  reports = new DiagnosticReports(options);
  await reports.initialize();
  await reports.setConsent(true);
  const started = now;
  now += 8 * 24 * 60 * 60 * 1000;
  await reports.flush();
  const marker = await readFile(join(options.directory, 'session.json'));
  await reports.close();
  await writeFile(join(options.directory, 'session.json'), marker);
  now += 1;
  reports = new DiagnosticReports({
    ...options,
    identity: { ...options.identity, app_version: '1.1.0' },
  });
  await reports.initialize();
  await reports.flush();
  expect(initial.send).toHaveBeenCalledOnce();
  expect(JSON.parse(String(initial.send.mock.calls[0][1]?.body)).props).toMatchObject({
    category: 'app_crash',
    reason: 'unclean_exit',
    app_version: '1.0.0',
  });
  const saved = JSON.parse(marker.toString('utf8'));
  expect(saved.started_at_ms).toBe(started);
  expect(saved.timestamp_ms).toBe(now - 1);
});

it('keeps the session start for journal correlation while refreshing its liveness marker', async () => {
  const initial = await create(false);
  let now = 1_794_640_000_000;
  await initial.reports.close();
  const options = { ...initial.options, now: () => now };
  reports = new DiagnosticReports(options);
  await reports.initialize();
  await reports.setConsent(true);
  await writeFile(
    join(initial.root, 'main-process-errors.json'),
    JSON.stringify([
      {
        at: new Date(now + 1000).toISOString(),
        version: '1.0.0',
        origin: 'uncaughtException',
        detail: 'TypeError: private words',
      },
    ]),
  );
  now += 60_000;
  await reports.flush();
  const marker = await readFile(join(options.directory, 'session.json'));
  await reports.close();
  await writeFile(join(options.directory, 'session.json'), marker);
  now += 1;
  reports = new DiagnosticReports(options);
  await reports.initialize();
  await reports.flush();
  expect(initial.send).toHaveBeenCalledOnce();
  const body = JSON.parse(String(initial.send.mock.calls[0][1]?.body));
  expect(body.props).toMatchObject({ reason: 'unhandled_exception', error_type: 'TypeError' });
  expect(JSON.stringify(body)).not.toContain('private words');
});

it('never refreshes a withdrawn grant or recovers a genuinely stale liveness marker', async () => {
  const initial = await create(false);
  let now = 1_794_640_000_000;
  await initial.reports.close();
  const options = { ...initial.options, now: () => now };
  reports = new DiagnosticReports(options);
  await reports.initialize();
  await reports.setConsent(true);
  const stale = await readFile(join(options.directory, 'session.json'));
  await reports.close();
  await writeFile(join(options.directory, 'session.json'), stale);
  now += 8 * 24 * 60 * 60 * 1000;
  reports = new DiagnosticReports(options);
  await reports.initialize();
  await reports.flush();
  expect(initial.send).not.toHaveBeenCalled();
  await reports.setConsent(false);
  now += 60_000;
  await reports.flush();
  await expect(readFile(join(options.directory, 'session.json'))).rejects.toMatchObject({
    code: 'ENOENT',
  });
});

it('bounds failed heartbeat writes and retries only while the same consent remains enabled', async () => {
  const initial = await create(false);
  let now = 1_794_640_000_000;
  await initial.reports.close();
  const options = { ...initial.options, now: () => now };
  reports = new DiagnosticReports(options);
  await reports.initialize();
  await reports.setConsent(true);
  gate.failRenameTarget = 'session.json';
  now += 60_000;
  await reports.flush();
  const attempted = gate.sessionWrites;
  now += 59_999;
  await reports.flush();
  await reports.flush();
  expect(gate.sessionWrites).toBe(attempted);
  gate.failRenameTarget = '';
  now += 1;
  await reports.flush();
  expect(gate.sessionWrites).toBe(attempted + 1);
  expect(
    JSON.parse(await readFile(join(options.directory, 'session.json'), 'utf8')).timestamp_ms,
  ).toBe(now);
  await reports.setConsent(false);
  const withdrawnWrites = gate.sessionWrites;
  now += 60_000;
  await reports.flush();
  expect(gate.sessionWrites).toBe(withdrawnWrites);
  await expect(readFile(join(options.directory, 'session.json'))).rejects.toMatchObject({
    code: 'ENOENT',
  });
});

it('a superseded ON write cannot remove the previous OFF tombstone before a failing newer OFF', async () => {
  const initial = await create(false);
  await initial.reports.setConsent(false);
  let release!: () => void;
  const entered = new Promise<void>((resolve) => {
    gate.entered = resolve;
  });
  gate.wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  gate.enabled = true;
  gate.claimed = false;
  const on = initial.reports.setConsent(true);
  try {
    await entered;
    gate.failWrites = true;
    gate.failRemoveTarget = 'consent.json';
    const off = initial.reports.setConsent(false).catch((error) => error);
    release();
    await on;
    await off;
    await initial.reports.close();
    gate.failWrites = false;
    gate.failRemoveTarget = '';
    gate.enabled = false;
    reports = new DiagnosticReports(initial.options);
    await reports.initialize();
    expect(reports.status()).toMatchObject({ enabled: false, opted_in: false });
    expect(
      JSON.parse(await readFile(join(initial.options.directory, 'revoked.json'), 'utf8')),
    ).toEqual({ version: 1 });
  } finally {
    release();
  }
});

it('flushes each consent publication parent after the file sync and rename on supported platforms', async () => {
  const initial = await create(false);
  gate.publications = [];
  await initial.reports.setConsent(false);
  const directoryCommit = process.platform === 'win32' ? [] : ['directory-sync', 'directory-close'];
  expect(gate.publications).toEqual([
    'file-sync',
    'rename:revoked.json',
    ...directoryCommit,
    'file-sync',
    'rename:consent.json',
    ...directoryCommit,
  ]);
});

it.skipIf(process.platform === 'win32')(
  'keeps a previous OFF fence and closes the directory if opt-in directory sync fails',
  async () => {
    const initial = await create(false);
    await initial.reports.setConsent(false);
    gate.publications = [];
    gate.failDirectorySync = true;
    await expect(initial.reports.setConsent(true)).rejects.toThrow(
      'Could not save diagnostic consent',
    );
    expect(gate.publications).toContain('directory-sync');
    expect(gate.publications.at(-1)).toBe('directory-close');
    expect(initial.reports.status()).toMatchObject({ enabled: false, opted_in: false });
    expect(
      JSON.parse(await readFile(join(initial.options.directory, 'revoked.json'), 'utf8')),
    ).toEqual({ version: 1 });
    initial.reports.capture({ category: 'generation_failed' });
    await initial.reports.flush();
    expect(initial.send).not.toHaveBeenCalled();
    gate.failDirectorySync = false;
    await initial.reports.close();
    reports = new DiagnosticReports(initial.options);
    await reports.initialize();
    expect(reports.status()).toMatchObject({ enabled: false, opted_in: false });
  },
);

it.each(['capture', 'close'] as const)(
  'preserves a valid unread outbox before later %s can replace it',
  async (action) => {
    const initial = await create();
    initial.reports.capture({ category: 'engine_failed' });
    await initial.reports.close();
    const original = await readFile(join(initial.options.directory, 'outbox.json'), 'utf8');
    gate.failReadTarget = 'outbox.json';
    reports = new DiagnosticReports(initial.options);
    await reports.initialize();
    reports.capture({ category: 'generation_failed' });
    if (action === 'capture') await reports.flush();
    else await reports.close();
    expect(await readFile(join(initial.options.directory, 'outbox.json'), 'utf8')).toBe(original);
  },
);

it('retries a temporarily unread outbox with a cooldown and merges bounded new intake before publication', async () => {
  const initial = await create();
  initial.reports.capture({ category: 'engine_failed' });
  await initial.reports.close();
  const original = JSON.parse(
    await readFile(join(initial.options.directory, 'outbox.json'), 'utf8'),
  ).records[0];
  let now = Date.now();
  gate.failReadTarget = 'outbox.json';
  reports = new DiagnosticReports({ ...initial.options, now: () => now });
  await reports.initialize();
  reports.capture({ category: 'generation_failed' });
  await reports.flush();
  const attempts = gate.outboxReads;
  now += 59_999;
  await reports.flush();
  expect(gate.outboxReads).toBe(attempts);
  gate.failReadTarget = '';
  now += 1;
  await reports.flush();
  const bodies = initial.send.mock.calls.map((call) => JSON.parse(String(call[1]?.body)));
  expect(bodies.map((body) => body.props.category).sort()).toEqual([
    'engine_failed',
    'generation_failed',
  ]);
  expect(bodies.find((body) => body.props.category === 'engine_failed')).toMatchObject({
    id: original.id,
    ts: original.timestamp_ms,
  });
});

it('keeps combined saved and deferred intake within the existing queue bound', async () => {
  const initial = await create();
  initial.reports.capture({ category: 'engine_failed' });
  await initial.reports.close();
  let now = Date.now();
  gate.failReadTarget = 'outbox.json';
  reports = new DiagnosticReports({ ...initial.options, now: () => now });
  await reports.initialize();
  for (let index = 0; index < MAX_RECORDS + 4; index++) {
    now += 60_001;
    reports.capture({ category: 'generation_failed' });
  }
  expect(reports.status().pending_count).toBe(MAX_RECORDS);
  gate.failReadTarget = '';
  await reports.flush();
  expect(initial.send).toHaveBeenCalledTimes(MAX_RECORDS);
  expect(
    initial.send.mock.calls.every(
      (call) => JSON.parse(String(call[1]?.body)).props.category === 'generation_failed',
    ),
  ).toBe(true);
  expect(reports.status().dropped_count).toBe(5);
});

it.each([false, true])(
  'never restores an unread old queue across withdrawal (new grant: %s)',
  async (reenable) => {
    const initial = await create();
    initial.reports.capture({ category: 'engine_failed' });
    await initial.reports.close();
    let release!: () => void;
    const entered = new Promise<void>((resolve) => {
      gate.blockedRead = {
        entered: resolve,
        wait: new Promise<void>((done) => {
          release = done;
        }),
      };
    });
    reports = new DiagnosticReports(initial.options);
    const initializing = reports.initialize();
    try {
      await entered;
      await reports.setConsent(false);
      if (reenable) await reports.setConsent(true);
    } finally {
      release();
      await initializing;
    }
    await reports.flush();
    expect(initial.send).not.toHaveBeenCalled();
    if (reenable) {
      reports.capture({ category: 'generation_failed' });
      await reports.flush();
      expect(initial.send).toHaveBeenCalledOnce();
      expect(JSON.parse(String(initial.send.mock.calls[0][1]?.body)).props.category).toBe(
        'generation_failed',
      );
    } else expect(reports.status().enabled).toBe(false);
  },
);

it.each([false, true])(
  'defers recovery until the saved outbox can deduplicate a prior fatal report (%s)',
  async (priorFatal) => {
    const initial = await create();
    if (priorFatal)
      initial.reports.capture({ category: 'app_crash', reason: 'unhandled_exception' });
    const marker = await readFile(join(initial.options.directory, 'session.json'));
    await initial.reports.close();
    await writeFile(join(initial.options.directory, 'session.json'), marker);
    let now = Date.now();
    gate.failReadTarget = 'outbox.json';
    reports = new DiagnosticReports({ ...initial.options, now: () => now });
    await reports.initialize();
    await reports.flush();
    expect(await readFile(join(initial.options.directory, 'session.json'))).toEqual(marker);
    expect(initial.send).not.toHaveBeenCalled();
    gate.failReadTarget = '';
    now += 60_000;
    await reports.flush();
    expect(initial.send).toHaveBeenCalledOnce();
    expect(JSON.parse(String(initial.send.mock.calls[0][1]?.body)).props.reason).toBe(
      priorFatal ? 'unhandled_exception' : 'unclean_exit',
    );
    await reports.flush();
    expect(initial.send).toHaveBeenCalledOnce();
  },
);

it.each(['EACCES', 'EIO'])(
  'preserves a recent unread session after %s until a bounded retry recovers it once',
  async (code) => {
    const initial = await create();
    const path = join(initial.options.directory, 'session.json');
    const marker = await readFile(path);
    await initial.reports.close();
    await writeFile(path, marker);
    let now = Date.now();
    const writes = gate.sessionWrites;
    gate.failReadTarget = 'session.json';
    gate.failReadCode = code;
    reports = new DiagnosticReports({ ...initial.options, now: () => now });
    await reports.initialize();
    const reads = gate.sessionReads;
    expect(await readFile(path)).toEqual(marker);
    expect(gate.sessionWrites).toBe(writes);
    expect(initial.send).not.toHaveBeenCalled();
    gate.failReadTarget = '';
    now += 59_999;
    await reports.flush();
    expect(gate.sessionReads).toBe(reads);
    expect(await readFile(path)).toEqual(marker);
    now++;
    await reports.flush();
    expect(initial.send).toHaveBeenCalledOnce();
    expect(JSON.parse(String(initial.send.mock.calls[0][1]?.body)).props.reason).toBe(
      'unclean_exit',
    );
    await reports.flush();
    expect(initial.send).toHaveBeenCalledOnce();
  },
);

it('preserves an unread previous-session marker through an orderly close', async () => {
  const initial = await create();
  const path = join(initial.options.directory, 'session.json');
  const marker = await readFile(path);
  await initial.reports.close();
  await writeFile(path, marker);
  gate.failReadTarget = 'session.json';
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  await reports.close();
  expect(await readFile(path)).toEqual(marker);
  gate.failReadTarget = '';
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  await reports.flush();
  expect(initial.send).toHaveBeenCalledOnce();
});

it.each([false, true])(
  'does not recover an unread session after withdrawal (new grant: %s)',
  async (newGrant) => {
    const initial = await create();
    const path = join(initial.options.directory, 'session.json');
    const marker = await readFile(path);
    await initial.reports.close();
    await writeFile(path, marker);
    gate.failReadTarget = 'session.json';
    reports = new DiagnosticReports(initial.options);
    await reports.initialize();
    expect(await readFile(path)).toEqual(marker);
    await reports.setConsent(false);
    gate.failReadTarget = '';
    if (newGrant) await reports.setConsent(true);
    await reports.flush();
    expect(initial.send).not.toHaveBeenCalled();
    expect(reports.status().enabled).toBe(newGrant);
  },
);

it.each(['missing', 'malformed', 'invalid'])(
  'replaces a proven %s session without delaying current reports',
  async (kind) => {
    const initial = await create();
    const path = join(initial.options.directory, 'session.json');
    await initial.reports.close();
    if (kind !== 'missing') await writeFile(path, kind === 'malformed' ? '{broken' : '[]');
    reports = new DiagnosticReports(initial.options);
    await reports.initialize();
    reports.capture({ category: 'generation_failed' });
    await reports.flush();
    expect(initial.send).toHaveBeenCalledOnce();
    expect(JSON.parse(String(initial.send.mock.calls[0][1]?.body)).props.category).toBe(
      'generation_failed',
    );
  },
);

it('replaces a confirmed malformed outbox without suppressing fresh opted-in delivery', async () => {
  const initial = await create();
  await initial.reports.close();
  await writeFile(join(initial.options.directory, 'outbox.json'), '{broken');
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  reports.capture({ category: 'generation_failed' });
  await reports.flush();
  expect(initial.send).toHaveBeenCalledOnce();
  expect(
    JSON.parse(await readFile(join(initial.options.directory, 'outbox.json'), 'utf8')).records,
  ).toEqual([]);
});

async function managedInbox() {
  const initial = await create();
  initial.reports.capture({ category: 'engine_failed' });
  await initial.reports.close();
  const record = JSON.parse(await readFile(join(initial.options.directory, 'outbox.json'), 'utf8'))
    .records[0];
  record.props.source = 'backend';
  await rm(join(initial.options.directory, 'outbox.json'));
  const inbox = join(initial.options.directory, 'inbox');
  await mkdir(inbox, { recursive: true });
  const file = join(inbox, `${record.id}.json`);
  const json = JSON.stringify(record);
  const text = json + ' '.repeat(8192 - Buffer.byteLength(json));
  await writeFile(file, text);
  return { ...initial, inbox, record, file, text };
}

it.each(['EACCES', 'EIO'])(
  'retains an unread 8KiB managed record after %s and retries without starving healthy intake',
  async (code) => {
    const initial = await managedInbox();
    const healthy = {
      ...initial.record,
      id: 'b'.repeat(32),
      props: { ...initial.record.props, category: 'generation_failed' },
    };
    await writeFile(join(initial.inbox, `${healthy.id}.json`), JSON.stringify(healthy));
    let now = Date.now();
    gate.failReadTarget = basename(initial.file);
    gate.failReadCode = code;
    reports = new DiagnosticReports({ ...initial.options, now: () => now });
    await reports.initialize();
    await reports.flush();
    await expect(readFile(initial.file, 'utf8')).resolves.toBe(initial.text);
    expect(initial.send).toHaveBeenCalledOnce();
    expect(JSON.parse(String(initial.send.mock.calls[0][1]?.body)).props.category).toBe(
      'generation_failed',
    );
    const attempts = gate.recordReads.get(basename(initial.file));
    now += 59_999;
    await reports.flush();
    await reports.flush();
    expect(gate.recordReads.get(basename(initial.file))).toBe(attempts);
    gate.failReadTarget = '';
    now += 1;
    await reports.flush();
    expect(initial.send).toHaveBeenCalledTimes(2);
    const recovered = JSON.parse(String(initial.send.mock.calls[1][1]?.body));
    expect(recovered).toMatchObject({ id: initial.record.id, ts: initial.record.timestamp_ms });
    await expect(readFile(initial.file)).rejects.toMatchObject({ code: 'ENOENT' });
  },
);

it('treats a consumed managed record disappearing during its read as normal and continues', async () => {
  const initial = await managedInbox();
  const healthy = { ...initial.record, id: 'b'.repeat(32) };
  await writeFile(join(initial.inbox, `${healthy.id}.json`), JSON.stringify(healthy));
  gate.removeOnReadTarget = basename(initial.file);
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  await reports.flush();
  expect(initial.send).toHaveBeenCalledOnce();
  expect(JSON.parse(String(initial.send.mock.calls[0][1]?.body)).id).toBe(healthy.id);
  await expect(readFile(initial.file)).rejects.toMatchObject({ code: 'ENOENT' });
});

it('discards confirmed malformed managed JSON without delaying the next valid record', async () => {
  const initial = await managedInbox();
  await writeFile(initial.file, '{broken');
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  await reports.flush();
  await expect(readFile(initial.file)).rejects.toMatchObject({ code: 'ENOENT' });
  expect(initial.send).not.toHaveBeenCalled();
  await writeFile(initial.file, initial.text);
  await reports.flush();
  expect(initial.send).toHaveBeenCalledOnce();
});

it('never lets an unread managed record borrow a later consent grant', async () => {
  const initial = await managedInbox();
  gate.failReadTarget = basename(initial.file);
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  await reports.flush();
  await reports.setConsent(false);
  await reports.setConsent(true);
  gate.failReadTarget = '';
  await mkdir(initial.inbox, { recursive: true });
  await writeFile(initial.file, initial.text);
  await reports.flush();
  expect(initial.send).not.toHaveBeenCalled();
  await expect(readFile(initial.file)).rejects.toMatchObject({ code: 'ENOENT' });
  reports.capture({ category: 'generation_failed' });
  await reports.flush();
  expect(initial.send).toHaveBeenCalledOnce();
});

it('requires HTTPS diagnostic URLs except exact literal loopback HTTP', () => {
  const destination = (host: string, appUrl = 'https://app.invalid') =>
    diagnosticDestination({
      SUPERMETRY_SITE_ID: 'fixture',
      SUPERMETRY_HOST: host,
      SUPERMETRY_APP_URL: appUrl,
    });
  for (const url of [
    'https://collector.invalid',
    'https://localhost:9443',
    'http://localhost:3900',
    'http://127.0.0.1:3900',
    'http://[::1]:3900',
  ]) {
    expect(destination(url)).not.toBeNull();
    expect(destination('https://collector.invalid', url + '/app')).not.toBeNull();
  }
  for (const url of [
    'http://collector.invalid',
    'http://192.168.1.4',
    'http://localhost.example.invalid',
    'http://127.0.0.1.example.invalid',
    'http://127.0.0.2',
    'http://127.1',
    'http://2130706433',
    'http://[0:0:0:0:0:0:0:1]',
    'http://%6cocalhost',
    'https://user:secret@collector.invalid',
    'https://collector.invalid/#fragment',
    'https://collector.invalid/?secret',
  ]) {
    expect(destination(url)).toBeNull();
    expect(destination('https://collector.invalid', url)).toBeNull();
  }
});

it('acknowledges saved opt-in even if its best-effort session marker cannot be published', async () => {
  const initial = await create(false);
  gate.failRenameTarget = 'session.json';
  await expect(initial.reports.setConsent(true)).resolves.toMatchObject({
    enabled: true,
    opted_in: true,
  });
  expect(
    JSON.parse(await readFile(join(initial.options.directory, 'consent.json'), 'utf8')).enabled,
  ).toBe(true);
  await expect(readFile(join(initial.options.directory, 'session.json'))).rejects.toMatchObject({
    code: 'ENOENT',
  });
  initial.reports.capture({ category: 'operation_failed' });
  await initial.reports.flush();
  expect(initial.send).toHaveBeenCalledTimes(1);

  await expect(initial.reports.setConsent(false)).resolves.toMatchObject({
    enabled: false,
    opted_in: false,
  });
  await initial.reports.close();
  gate.failRenameTarget = '';
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  await reports.flush();
  expect(reports.status()).toMatchObject({ enabled: false, opted_in: false, pending_count: 0 });
  expect(initial.send).toHaveBeenCalledTimes(1);
});

it('removes the orderly-exit marker even when outbox publication fails', async () => {
  const initial = await create();
  expect(
    JSON.parse(await readFile(join(initial.options.directory, 'session.json'), 'utf8')).active,
  ).toBe(true);
  initial.reports.capture({ category: 'generation_failed' });
  gate.failRenameTarget = 'outbox.json';
  await expect(initial.reports.close()).rejects.toMatchObject({ code: 'ENOSPC' });
  await expect(readFile(join(initial.options.directory, 'session.json'))).rejects.toMatchObject({
    code: 'ENOENT',
  });

  gate.failRenameTarget = '';
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  await reports.flush();
  expect(reports.status()).toMatchObject({ enabled: true, pending_count: 0 });
  expect(initial.send).not.toHaveBeenCalled();
});

it('keeps withdrawal durable when both replacing and removing the old grant fail', async () => {
  const initial = await create();
  const old = JSON.parse(await readFile(join(initial.options.directory, 'consent.json'), 'utf8'));
  initial.reports.capture({ category: 'generation_failed' });
  await initial.reports.close();
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  expect(reports.status().pending_count).toBe(1);
  gate.failRenameTarget = 'consent.json';
  gate.failRemoveTarget = 'consent.json';
  await expect(reports.setConsent(false)).rejects.toThrow('Could not save');
  expect(reports.status()).toMatchObject({ enabled: false, opted_in: false, prompted: true });
  expect(
    JSON.parse(await readFile(join(initial.options.directory, 'revoked.json'), 'utf8')),
  ).toEqual({ version: 1 });
  expect(
    JSON.parse(await readFile(join(initial.options.directory, 'consent.json'), 'utf8')).enabled,
  ).toBe(true);
  await expect(readFile(join(initial.options.directory, 'outbox.json'))).rejects.toMatchObject({
    code: 'ENOENT',
  });
  await reports.close();

  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  await reports.flush();
  expect(reports.status()).toMatchObject({
    enabled: false,
    opted_in: false,
    prompted: true,
    pending_count: 0,
  });
  expect(initial.send).not.toHaveBeenCalled();
  gate.failRenameTarget = '';
  gate.failRemoveTarget = '';
  await reports.flush();
  await reports.setConsent(true);
  const current = JSON.parse(
    await readFile(join(initial.options.directory, 'consent.json'), 'utf8'),
  );
  expect(current.epoch).not.toBe(old.epoch);
  expect(current.profile_id).not.toBe(old.profile_id);
});

it('requires a fresh saved grant and removal of its revocation marker before enabling again', async () => {
  const initial = await create();
  await initial.reports.setConsent(false);
  gate.failRemoveTarget = 'revoked.json';
  await expect(initial.reports.setConsent(true)).rejects.toThrow('Could not save');
  expect(initial.reports.status()).toMatchObject({ enabled: false, opted_in: false });
  initial.reports.capture({ category: 'generation_failed' });
  await initial.reports.flush();
  expect(initial.send).not.toHaveBeenCalled();
  await initial.reports.close();
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  expect(reports.status()).toMatchObject({ enabled: false, opted_in: false });
  gate.failRemoveTarget = '';
  await reports.setConsent(true);
  expect(reports.status()).toMatchObject({ enabled: true, opted_in: true });
  reports.capture({ category: 'generation_failed' });
  await reports.flush();
  expect(initial.send).toHaveBeenCalledOnce();
});

it('retries durable hard-kill revocation after storage becomes writable without reviving the grant', async () => {
  const initial = await create();
  await initial.reports.close();
  gate.failWrites = true;
  gate.failRemoveTarget = 'consent.json';
  reports = new DiagnosticReports({
    ...initial.options,
    env: { OMNIVOICE_DIAGNOSTICS_DISABLED: '1' },
  });
  await reports.initialize();
  expect(reports.status()).toMatchObject({ enabled: false, opted_in: false });
  gate.failWrites = false;
  // The old grant remains locked, but a separate private marker can now be saved.
  gate.failRenameTarget = 'consent.json';
  await reports.flush();
  await reports.close();
  reports = new DiagnosticReports(initial.options);
  await reports.initialize();
  await reports.flush();
  expect(reports.status()).toMatchObject({ enabled: false, opted_in: false, prompted: true });
  expect(initial.send).not.toHaveBeenCalled();
});

it('retires the session marker before waiting for an active worker during intentional close', async () => {
  const initial = await create();
  let release!: (response: Response) => void;
  initial.send.mockImplementationOnce(
    () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      }),
  );
  initial.reports.capture({ category: 'operation_failed' });
  const flushing = initial.reports.flush();
  await vi.waitFor(() => expect(initial.send).toHaveBeenCalledOnce());
  const closing = initial.reports.close();
  try {
    await vi.waitFor(async () => {
      await expect(readFile(join(initial.options.directory, 'session.json'))).rejects.toMatchObject(
        { code: 'ENOENT' },
      );
    });
  } finally {
    release(new Response('', { status: 202 }));
    await flushing;
    await closing;
  }
});

it('does not resurrect an outbox whose fsync completes after revocation', async () => {
  const { reports, send, root } = await create();
  let release!: () => void;
  const entering = new Promise<void>((resolve) => {
    gate.entered = resolve;
  });
  gate.wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  gate.enabled = true;
  gate.claimed = false;
  reports.capture({ category: 'app_crash', reason: 'unhandled_exception' });
  const work = reports.flush();
  await entering;
  const revoked = reports.setConsent(false);
  try {
    await vi.waitFor(async () =>
      expect(
        JSON.parse(await readFile(join(root, 'diagnostics', 'consent.json'), 'utf8')).enabled,
      ).toBe(false),
    );
    expect(send).not.toHaveBeenCalled();
  } finally {
    release();
  }
  await revoked;
  await work;
  await expect(readFile(join(root, 'diagnostics', 'outbox.json'))).rejects.toMatchObject({
    code: 'ENOENT',
  });
  expect(send).not.toHaveBeenCalled();
});

it.skipIf(process.platform === 'win32')(
  'replaces a malicious private-state symlink without overwriting its target',
  async () => {
    const { reports, root } = await create();
    const unrelated = join(root, 'important-user-file');
    await writeFile(unrelated, 'PRIVATE_USER_CONTENT');
    await symlink(unrelated, join(root, 'diagnostics', 'outbox.json'));
    reports.capture({ category: 'app_crash', reason: 'unhandled_exception' });
    await reports.close();
    expect(await readFile(unrelated, 'utf8')).toBe('PRIVATE_USER_CONTENT');
    expect(await readFile(join(root, 'diagnostics', 'outbox.json'), 'utf8')).not.toContain(
      'PRIVATE_USER_CONTENT',
    );
  },
);

it('still delivers the opted-in disk-full diagnostic if local outbox persistence cannot write', async () => {
  const { reports, send } = await create();
  gate.failWrites = true;
  reports.capture({ category: 'install_failed', stage: 'setup', error_code: 'DISK_FULL' });
  await reports.flush();
  expect(send).toHaveBeenCalledTimes(1);
  expect(reports.status().pending_count).toBe(0);
});

it.each(['OMNIVOICE_DIAGNOSTICS_DISABLED', 'OMNIVOICE_ANALYTICS_DISABLED'])(
  'removes an old grant if a %s launch cannot save its revocation',
  async (key) => {
    const original = await create();
    const old = JSON.parse(
      await readFile(join(original.root, 'diagnostics', 'consent.json'), 'utf8'),
    );
    original.reports.capture({ category: 'generation_failed', stage: 'generation' });
    await original.reports.close();
    gate.failWrites = true;
    reports = new DiagnosticReports({ ...original.options, env: { [key]: '1' } });
    await reports.initialize();
    expect(reports.status()).toMatchObject({ enabled: false, opted_in: false });
    await expect(
      readFile(join(original.root, 'diagnostics', 'consent.json')),
    ).rejects.toMatchObject({ code: 'ENOENT' });
    await reports.close();
    gate.failWrites = false;
    reports = new DiagnosticReports(original.options);
    await reports.initialize();
    await reports.flush();
    expect(reports.status()).toMatchObject({ enabled: false, opted_in: false, pending_count: 0 });
    expect(original.send).not.toHaveBeenCalled();
    await reports.setConsent(true);
    const current = JSON.parse(
      await readFile(join(original.root, 'diagnostics', 'consent.json'), 'utf8'),
    );
    expect(current.epoch).not.toBe(old.epoch);
    expect(current.profile_id).not.toBe(old.profile_id);
  },
);
