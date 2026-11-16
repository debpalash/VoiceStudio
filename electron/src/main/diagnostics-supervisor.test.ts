import { afterEach, expect, it, vi } from 'vitest';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
vi.mock('electron', () => ({
  app: { isPackaged: false, getPath: () => '/nonexistent-diagnostic-fixture' },
}));
import { BackendSupervisor, managedBackendSpawnOptions, setBackendOnlyEnv } from './backend';
import { DiagnosticReports } from './diagnostics';
import { DiagnosticStatusObserver } from './diagnostics-capture';

let directory: string;
let reports: DiagnosticReports;
afterEach(async () => {
  await reports?.close();
  if (directory) await rm(directory, { recursive: true, force: true });
  setBackendOnlyEnv({ OMNIVOICE_DIAGNOSTICS_DIR: undefined });
});

it('delivers a supervisor-classified failed setup without starting or depending on Python', async () => {
  directory = await mkdtemp(join(tmpdir(), 'voice-diagnostic-supervisor-'));
  const send = vi.fn(
    async (_url: unknown, _options?: RequestInit) => new Response('', { status: 202 }),
  );
  reports = new DiagnosticReports({
    directory,
    identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' },
    env: {},
    destination: {
      site: 'fixture',
      host: 'https://fixture.invalid',
      appUrl: 'https://fixture-app.invalid',
    },
    send,
    automatic: false,
  });
  await reports.initialize();
  await reports.setConsent(true);
  const supervisor = new BackendSupervisor();
  const capture = new DiagnosticStatusObserver(reports);
  const unsubscribe = supervisor.subscribe((status) => capture.observe(status));
  const owner = supervisor as unknown as {
    setStage(stage: 'setup_required', patch: { message: string }): void;
  };
  owner.setStage('setup_required', {
    message: 'Failed to download a package: connection reset; PRIVATE_USER_KEY',
  });
  expect(supervisor.status.failure?.code).toBe('RUNTIME_DOWNLOAD_INTERRUPTED');
  await reports.flush();
  expect(send).toHaveBeenCalledTimes(1);
  const event = JSON.parse(String(send.mock.calls[0][1]?.body));
  expect(event.props).toMatchObject({
    category: 'install_failed',
    error_code: 'RUNTIME_DOWNLOAD_INTERRUPTED',
  });
  expect(JSON.stringify(event)).not.toContain('PRIVATE_USER_KEY');
  setBackendOnlyEnv({ OMNIVOICE_DIAGNOSTICS_DIR: directory });
  expect(managedBackendSpawnOptions(3900).env.OMNIVOICE_DIAGNOSTICS_DIR).toBe(directory);
  unsubscribe();
});
