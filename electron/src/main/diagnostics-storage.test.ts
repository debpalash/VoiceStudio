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
}));
vi.mock('node:fs/promises', async (original) => {
  const fs = await original<typeof import('node:fs/promises')>();
  return {
    ...fs,
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
import { DiagnosticReports } from './diagnostics';
let root: string | undefined;
let reports: DiagnosticReports | undefined;
afterEach(async () => {
  gate.enabled = false;
  gate.failWrites = false;
  await reports?.close();
  if (root) await rm(root, { recursive: true, force: true });
});
async function create() {
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
  await reports.setConsent(true);
  return { reports, send, root, options };
}

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
