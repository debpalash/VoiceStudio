import { afterEach, expect, it, vi } from 'vitest';
import { mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
const gate = vi.hoisted(() => ({
  enabled: false,
  claimed: false,
  entered: () => {},
  wait: Promise.resolve() as Promise<void>,
  failWrites: false,
  failRenameTarget: '',
  failRemoveTarget: '',
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
      if (gate.failRenameTarget && String(args[1]).endsWith(gate.failRenameTarget))
        throw Object.assign(new Error('ENOSPC'), { code: 'ENOSPC' });
      return fs.rename(...args);
    },
    open: async (...args: Parameters<typeof fs.open>) => {
      const handle = await fs.open(...args);
      const write = handle.writeFile.bind(handle);
      handle.writeFile = (async (...values: Parameters<typeof write>) => {
        if (gate.failWrites) throw Object.assign(new Error('ENOSPC'), { code: 'ENOSPC' });
        return write(...values);
      }) as typeof handle.writeFile;
      const sync = handle.sync.bind(handle);
      handle.sync = async () => {
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
let root: string | undefined;
let reports: DiagnosticReports | undefined;
afterEach(async () => {
  gate.enabled = false;
  gate.failWrites = false;
  gate.failRenameTarget = '';
  gate.failRemoveTarget = '';
  await reports?.close();
  if (root) await rm(root, { recursive: true, force: true });
});
async function create(optIn = true) {
  root = await mkdtemp(join(tmpdir(), 'voice-diagnostic-disk-'));
  const send = vi.fn(async () => new Response('', { status: 202 }));
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
