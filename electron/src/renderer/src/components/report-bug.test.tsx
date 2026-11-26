vi.mock('@/hooks/use-backend-status', () => ({
  getBackendStatusSnapshot: () => ({
    stage: mock.crashed ? 'crashed' : 'ready',
    managed: true,
    exitCode: 1,
    message: 'failed in /home/private-user/runtime',
    logTail: [
      'ValueError: original failure',
      'The above exception was the direct cause of the following exception:',
      'x'.repeat(2000),
      'RuntimeError: wrapper',
    ],
    failure: mock.snapshotFailure,
  }),
}));
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { NativeCrashRecord } from '../../../preload/index.d';
import { nativeFaultSummary } from '@shared/utils/crashReport';
import { addBreadcrumb, clearBreadcrumbs } from '@shared/utils/breadcrumbs';
import {
  _resetBackendContactForTests,
  recordBackendContact,
} from '@shared/utils/backendContact';
const mock = vi.hoisted(() => ({
  saveData: vi.fn(),
  snapshotFailure: undefined as { code: string; stage: string } | undefined,
  crashed: false,
  native: true,
  api: vi.fn(),
  open: vi.fn().mockResolvedValue(undefined),
  updateState: vi.fn(),
  backendStatus: vi.fn(),
  hostSnapshot: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api }));
vi.mock('./bridge', () => ({
  getBridge: () =>
    mock.native
      ? {
          files: { openExternal: mock.open, saveData: mock.saveData },
          updates: { getState: mock.updateState },
          backend: {
            getStatus: mock.backendStatus,
            getHostSnapshot: mock.hostSnapshot,
            getSetupDiagnostics: async () =>
              "Setup attempt: 900 second timeout\ncannot decrypt peer's message in /home/private-user/runtime",
          },
        }
      : null,
}));
vi.mock('@/router', () => ({ router: { navigate: mock.navigate } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { ReportBug } from './report-bug';
beforeEach(() => {
  mock.snapshotFailure = undefined;
  mock.saveData.mockReset();
  clearBreadcrumbs();
  _resetBackendContactForTests();
  mock.updateState.mockResolvedValue({
    status: 'unsupported',
    currentVersion: '0.5.2',
    channel: 'stable',
    progress: 0,
  });
  mock.backendStatus.mockResolvedValue(null);
  mock.hostSnapshot.mockResolvedValue(null);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  mock.native = true;
  mock.crashed = false;
  clearBreadcrumbs();
  _resetBackendContactForTests();
});
async function submitReport(summary = 'Dubbing stops at 40%') {
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
  fireEvent.change(await screen.findByRole('textbox', { name: 'reportBug.summary_label' }), {
    target: { value: summary },
  });
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.submit' }));
}
it.each([true, false])(
  'opens a reviewable report with redacted or unavailable context (%s)',
  async (online) => {
    const secret = 'hf_' + 'a'.repeat(34);
    mock.api.mockImplementation(() =>
      online
        ? Promise.resolve({
            gpu_name: secret,
            platform: '/home/private-user/bin',
            transcript: 'Never include this',
            tts: { active: 'omnivoice' },
          })
        : Promise.reject(new Error('offline')),
    );
    render(<ReportBug />);
    expect(mock.api).not.toHaveBeenCalled();
    await submitReport();
    await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
    const url = new URL(mock.open.mock.calls[0]![0]);
    expect(url.origin).toBe('https://github.com');
    const body = url.searchParams.get('body')!;
    expect(body).toContain('Electron');
    expect(body).not.toContain(secret);
    expect(body).not.toContain('private-user');
    expect(body).not.toContain('Never include this');
    expect(mock.api.mock.calls.every(([, init]) => !init.method)).toBe(true);
  },
);

it('opens browser context before awaiting diagnostics and reports blocked popups', async () => {
  mock.native = false;
  const open = vi.spyOn(window, 'open').mockReturnValue(null);
  render(<ReportBug />);
  await submitReport();
  await screen.findByRole('alert');
  expect(open).toHaveBeenCalledWith('about:blank', '_blank');
  expect(mock.api).not.toHaveBeenCalled();
});

it('includes setup diagnostics in a reviewable GitHub report without a working backend', async () => {
  mock.api.mockRejectedValue(new Error('backend not installed'));
  mock.backendStatus.mockResolvedValue({
    stage: 'setup_required',
    logTail: [],
    setupProgress: { stalledSeconds: 300 },
  });
  render(<ReportBug error="Runtime setup failed" setup />);
  await submitReport('Torch download stopped at 13 MB');
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('Runtime setup');
  expect(body).toContain('900 second timeout');
  expect(body).toContain("cannot decrypt peer's message");
  expect(body).toContain('voicestudio-setup.log');
  expect(body).not.toContain('private-user');
});
it('navigates the reserved browser window after diagnostics resolve', async () => {
  mock.native = false;
  const replace = vi.fn();
  const popup = { opener: window, closed: false, location: { replace }, close: vi.fn() };
  vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  mock.api.mockResolvedValue({});
  render(<ReportBug />);
  await submitReport();
  expect(popup.opener).toBeNull();
  await waitFor(() => expect(replace).toHaveBeenCalledTimes(1));
  expect(replace.mock.calls[0]![0]).toContain('https://github.com/');
});

it('preserves the scrubbed current crash and original chained cause when the API is down', async () => {
  mock.crashed = true;
  mock.api.mockRejectedValue(new Error('offline'));
  render(<ReportBug />);
  await submitReport();
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('## Current backend failure');
  expect(body).toContain('Exit code: 1');
  expect(body).toContain('ValueError: original failure');
  expect(body).toContain('RuntimeError: wrapper');
  expect(body).not.toContain('private-user');
});

it('includes the durable native crash journal when the backend API is unavailable', async () => {
  mock.api.mockRejectedValue(new Error('offline'));
  mock.backendStatus.mockResolvedValue({
    stage: 'crashed',
    managed: true,
    remote: false,
    exitCode: 23,
    message: 'Backend exited unexpectedly',
    logTail: ['current failure'],
    lastCrash: {
      timestamp: 1_000_000,
      version: '0.5.2',
      exitCode: 23,
      signal: null,
      uptimeMs: 4_200,
      logTail: ['durable native failure'],
    },
  });
  render(<ReportBug />);
  await submitReport();
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('## Last native backend exit');
  expect(body).toContain('Exit code: 23');
  expect(body).toContain('Uptime (seconds): 4');
  expect(body).toContain('durable native failure');
});

it.each([false, true])(
  'keeps the actual journal faulting thread ahead of verbose exit tails (error prop: %s)',
  async (withError) => {
    // Exercise real Node-owned journal/storage code through the test runner's
    // fixture loader, without importing it into the browser TypeScript project.
    const { mkdtempSync, rmSync } = await vi.importActual<{
      mkdtempSync(prefix: string): string;
      rmSync(
        path: string,
        options: { recursive: boolean; force: boolean; maxRetries?: number; retryDelay?: number },
      ): void;
    }>('node:fs');
    const { tmpdir } = await vi.importActual<{ tmpdir(): string }>('node:os');
    const { join } = await vi.importActual<{ join(...parts: string[]): string }>('node:path');
    const { CrashJournal } = await vi.importActual<{
      CrashJournal: new (
        path: string,
        version: string,
      ) => {
        startProcess(): void;
        captureLine(line: string): void;
        record(
          exitCode: number,
          signal: null,
          uptimeMs: number,
          logTail: string[],
        ): NativeCrashRecord | undefined;
        latest(): NativeCrashRecord | undefined;
      };
    }>('../../../main/crash-journal');
    const directory = mkdtempSync(join(tmpdir(), 'native-report-context-'));
    try {
      const journal = new CrashJournal(join(directory, 'crashes.json'), '0.5.8');
      journal.startProcess();
      journal.captureLine(
        'VOICESTUDIO_CRASH_CONTEXT=' +
          JSON.stringify({
            kind: 'model_load',
            engine: 'omnivoice',
            device: 'cpu',
            ram_total_gb: 32,
            ram_free_gb: 8,
          }),
      );
      const prefix = String.raw`C:\Users\private-user\VoiceStudio\runtime\project`;
      const dump = [
        'Windows fatal exception: access violation',
        'Thread 0x111 (most recent call first):',
        `  File "${prefix}\\.venv\\Lib\\threading.py", line 324 in wait`,
        'Current thread 0x222 (most recent call first):',
        `  File "${prefix}\\.venv\\Lib\\site-packages\\torch\\storage.py", line 468 in __getitem__`,
        `  File "${prefix}\\.venv\\Lib\\site-packages\\transformers\\core_model_loading.py", line 186 in _materialize_copy`,
        `  File "${prefix}\\omnivoice\\models\\omnivoice.py", line 583 in from_pretrained`,
        `  File "${prefix}\\backend\\services\\model_manager.py", line 1607 in _load_model_sync`,
        ...Array.from({ length: 30 }, (_, index) => [
          `Thread 0x${index + 300} (most recent call first):`,
          `  File "${prefix}\\.venv\\Lib\\threading.py", line 324 in wait`,
          `  File "${prefix}\\.venv\\Lib\\site-packages\\uvicorn\\server.py", line 77 in run`,
          `  File "${prefix}\\.venv\\Lib\\asyncio\\runners.py", line 190 in run`,
        ]).flat(),
        'Extension modules: torch._C, numpy.core',
      ];
      for (const line of dump) journal.captureLine(line);
      const lastCrash = journal.record(0xc0000005, null, 120_000, dump)!;
      const restored = new CrashJournal(join(directory, 'crashes.json'), '0.5.8').latest()!;
      expect(restored).toEqual(lastCrash);
      expect(restored.logTail.join('\n')).toContain('_materialize_copy');
      // The supervisor's existing failure message quotes only the newest20
      // stderr lines, whereas the durable journal already keeps the fault first.
      const tail = dump.slice(-20).join('\n').slice(-3000);
      expect(tail).not.toContain('_materialize_copy');
      const message = `Backend exited unexpectedly (exit code 3221225477). Last output: ${nativeFaultSummary(restored.logTail.join('\n'))}\nLast stderr:\n${tail}`;
      mock.backendStatus.mockResolvedValue({
        stage: 'crashed',
        managed: true,
        remote: false,
        exitCode: 0xc0000005,
        message,
        logTail: dump.slice(-40),
        lastCrash: restored,
        failure: { code: 'NATIVE_ACCESS_VIOLATION', stage: 'crashed', exitCode: 0xc0000005 },
      });
      mock.api.mockImplementation((path: string) =>
        Promise.resolve(
          path === '/system/info'
            ? { platform: 'win32', arch: 'x64', device: 'cpu', ram_total_gb: 32 }
            : path === '/engines'
              ? { tts: { active: 'omnivoice' } }
              : path.startsWith('/system/logs')
                ? { lines: dump.slice(-40) }
                : {},
        ),
      );
      render(<ReportBug error={withError ? message : undefined} />);
      await submitReport('Local engine stops while loading');
      await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
      const url = new URL(mock.open.mock.calls[0]![0]);
      const body = url.searchParams.get('body')!;
      expect(body).toContain('Current thread 0x222');
      expect(body).toContain('transformers/core_model_loading.py');
      expect(body).toContain('_materialize_copy');
      expect(body).toContain('from_pretrained');
      expect(body).toContain('NATIVE_ACCESS_VIOLATION');
      expect(body).toContain('Version: 0.5.8');
      expect(body).toContain('Device: cpu');
      expect(body).toContain('**App:** VoiceStudio');
      expect(body.indexOf('_materialize_copy')).toBeLessThan(
        body.indexOf('## Current backend failure'),
      );
      expect(body).not.toContain('private-user');
      expect(url.toString().length).toBeLessThan(8000);
    } finally {
      rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
    }
  },
);

it('retains ordinary error, environment and failure section ordering', async () => {
  mock.api.mockResolvedValue({});
  mock.backendStatus.mockResolvedValue({
    stage: 'crashed',
    managed: true,
    remote: false,
    exitCode: 1,
    message: 'ordinary failure',
    logTail: ['ordinary traceback'],
    lastCrash: {
      timestamp: 1000000,
      version: '0.5.8',
      exitCode: 1,
      signal: null,
      uptimeMs: 1000,
      logTail: ['previous ordinary traceback'],
    },
  });
  render(<ReportBug error="Current ordinary error" />);
  await submitReport();
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  const sections = [
    '## Error',
    '## Environment',
    '## Current backend failure',
    '## Last native backend exit',
  ];
  expect(sections.map((section) => body.indexOf(section))).toEqual(
    [...sections.map((section) => body.indexOf(section))].sort((a, b) => a - b),
  );
});

it('names the exact build, not just the version', async () => {
  mock.api.mockRejectedValue(new Error('offline'));
  render(<ReportBug />);
  await submitReport();
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toMatch(/\*\*App:\*\* VoiceStudio \S+ \(build [0-9a-f]{9}\)/);
});
it('describes the machine and runtime from the app when the backend never started (#3156)', async () => {
  mock.api.mockRejectedValue(new Error('offline'));
  mock.hostSnapshot.mockResolvedValue({
    os: 'win32 Windows 11 Pro (10.0.26100)',
    arch: 'x64',
    ramGb: 32,
    diskFreeGb: 12.5,
    runtime: { pythonPresent: false, uvPresent: true, pathFlags: ['onedrive', 'non-ascii'] },
  });
  render(<ReportBug />);
  await submitReport();
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('os (app): win32 Windows 11 Pro (10.0.26100)');
  expect(body).toContain('runtime python: MISSING');
  expect(body).toContain('runtime path flags: onedrive, non-ascii');
});
it('does not repeat machine facts the backend already reported', async () => {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(path === '/system/info' ? { platform: 'linux' } : {}),
  );
  mock.hostSnapshot.mockResolvedValue({
    os: 'linux 6.1',
    runtime: { pythonPresent: true, uvPresent: true, pathFlags: [] },
  });
  render(<ReportBug />);
  await submitReport();
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).not.toContain('os (app)');
  expect(body).toContain('runtime python: present');
});
it('labels the previous unclean shutdown separately and does not acknowledge it', async () => {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/system/last-run-crash'
        ? {
            acknowledged: false,
            record: {
              detected_at: 1000,
              version: '1.2.3',
              uptime_hint_s: 42,
              last_activity: { kind: 'generation', detail: '/home/private-user/output' },
              log_tail: ['Previous failure'],
            },
          }
        : {},
    ),
  );
  render(<ReportBug />);
  await submitReport();
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('## Previous unclean shutdown');
  expect(body).not.toContain('## Current backend failure');
  expect(body).toContain('1970-01-01T00:16:40.000Z');
  expect(body).toContain('Previous failure');
  expect(body).not.toContain('private-user');
  expect(mock.api.mock.calls.some(([path]) => path.endsWith('/ack'))).toBe(false);
});

it('offers the known update before gathering diagnostics and opens Updates', async () => {
  mock.updateState.mockResolvedValue({
    status: 'available',
    currentVersion: '0.5.2',
    availableVersion: '0.6.0',
    channel: 'stable',
    progress: 0,
  });
  render(<ReportBug />);

  await submitReport();
  await screen.findByText('reportBug.staleTitle');
  expect(mock.api).not.toHaveBeenCalled();
  expect(mock.open).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'reportBug.staleView' }));
  await waitFor(() => expect(mock.navigate).toHaveBeenCalledWith({ to: '/settings/updates' }));
  expect(mock.open).not.toHaveBeenCalled();
});

it('files anyway after the stale-build warning and records that context', async () => {
  mock.updateState.mockResolvedValue({
    status: 'downloaded',
    currentVersion: '0.5.2',
    availableVersion: '0.6.0',
    channel: 'stable',
    progress: 100,
  });
  mock.api.mockResolvedValue({});
  render(<ReportBug />);

  await submitReport();
  await screen.findByText('reportBug.staleTitle');
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.staleFileAnyway' }));

  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('**Build status:** OUTDATED');
  expect(body).toContain('v0.6.0');
});

it('still opens a report when the updater state is unavailable', async () => {
  mock.updateState.mockRejectedValue(new Error('Updater unavailable'));
  mock.api.mockResolvedValue({});
  render(<ReportBug />);

  await submitReport();

  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  expect(screen.queryByText('reportBug.staleTitle')).not.toBeInTheDocument();
});

it('includes privacy-safe route history and pre-report backend reachability', async () => {
  recordBackendContact(Date.now() - 5_000);
  addBreadcrumb('view:clone');
  addBreadcrumb('generate:start (clone)');
  mock.api.mockResolvedValue({});
  render(
    <ReportBug
      error={Object.assign(new Error('Transport failed'), {
        status: 0,
        payload: {
          detail: {
            firstFailureTs: 1_000,
            attempts: 3,
            mode: 'desktop',
            transport: 'connection reset',
          },
        },
      })}
    />,
  );

  await submitReport();

  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('## Backend reachability');
  expect(body).toContain('**Connection:** `managed local`');
  expect(body).toContain('**Last backend response:** 5 s before this report');
  expect(body).toContain('**HTTP status:** transport failure');
  expect(body).toContain('**Attempts before giving up:** 3');
  expect(body).toContain('## Recent actions');
  expect(body).toContain('view:clone');
  expect(body).toContain('generate:start (clone)');
});

it('attaches the scrubbed current backend log when a live operation fails', async () => {
  const secret = `hf_${'A'.repeat(34)}`;
  mock.api.mockImplementation((path: string) => {
    if (path === '/system/logs?tail=120') {
      return Promise.resolve({
        lines: [`RuntimeError: failed in /Users/alice/model with ${secret}`],
      });
    }
    return Promise.resolve({});
  });
  render(<ReportBug error={new Error('generation failed')} />);

  await submitReport();

  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('## Current backend log');
  expect(body).toContain('RuntimeError: failed in ~/model');
  expect(body).not.toContain('/Users/alice');
  expect(body).not.toContain(secret);
});

it('includes arch, disk free and active ASR engine when the backend answers (#2416)', async () => {
  mock.api.mockImplementation((path: string) => {
    if (path === '/system/info')
      return Promise.resolve({ arch: 'arm64', disk_free_gb: 42.5, platform: 'darwin' });
    if (path === '/engines')
      return Promise.resolve({ tts: { active: 'omnivoice' }, asr: { active: 'faster-whisper' } });
    return Promise.resolve({});
  });
  render(<ReportBug />);

  await submitReport();

  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('arch');
  expect(body).toContain('arm64');
  expect(body).toContain('disk_free_gb');
  expect(body).toContain('42.5');
  expect(body).toContain('TTS: omnivoice');
  expect(body).toContain('ASR: faster-whisper');
});

it.each([
  [3221225477, null, true],
  [-1073741819, null, true],
  [null, 'SIGSEGV', true],
  [null, 'SIGILL', true],
  [1, null, false],
  [null, 'SIGTERM', false],
])('classifies current and recorded native failures (%s, %s)', async (exitCode, signal, nativeFault) => {
  mock.api.mockRejectedValue(new Error('offline'));
  mock.backendStatus.mockResolvedValue({
    stage: 'crashed', managed: true, remote: false, exitCode, exitSignal: signal,
    message: 'Backend exited', logTail: [],
    lastCrash: { timestamp: 1000000, version: '0.5.4', exitCode, signal, uptimeMs: 1000, logTail: [] },
  });
  render(<ReportBug />);
  await submitReport();
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body.split('reportBug.native_fault_cause').length - 1).toBe(nativeFault ? 2 : 0);
  if (exitCode === 3221225477) expect(body).toContain('STATUS_ACCESS_VIOLATION');
  if (signal) expect(body.split('## Last native backend exit')[0]).toContain('Signal: ' + signal);
});

it('does not label an unreachable backend using a previous run’s signal', async () => {
  mock.api.mockRejectedValue(new Error('offline'));
  mock.backendStatus.mockResolvedValue({
    stage: 'crashed', managed: false, remote: false,
    message: 'Backend stopped answering', logTail: [],
    lastCrash: { timestamp: 1000000, version: '0.5.4', exitCode: null, signal: 'SIGSEGV', uptimeMs: 1000, logTail: [] },
  });
  render(<ReportBug />);
  await submitReport();
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  const [current, recorded] = body.split('## Last native backend exit');
  expect(current).not.toContain('reportBug.native_fault_cause');
  expect(recorded).toContain('reportBug.native_fault_cause');
});

it('requires a one-line description before opening anything', async () => {
  render(<ReportBug />);
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
  await screen.findByRole('textbox', { name: 'reportBug.summary_label' });
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.submit' }));
  expect(await screen.findByText('reportBug.summary_required')).toBeInTheDocument();
  expect(mock.open).not.toHaveBeenCalled();
  expect(mock.api).not.toHaveBeenCalled();
});

it('lists what will be sent and puts the description in the issue title and body', async () => {
  mock.api.mockResolvedValue({});
  render(<ReportBug />);
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
  expect(await screen.findByText('reportBug.sent_title')).toBeInTheDocument();
  expect(screen.getByText('reportBug.sent_logs')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'reportBug.summary_label' }), {
    target: { value: 'Dubbing stops at 40% in /home/private-user/x' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.submit' }));
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const url = new URL(mock.open.mock.calls[0]![0]);
  expect(url.searchParams.get('title')).toMatch(/^\[Bug\] Dubbing stops at 40%/);
  expect(url.searchParams.get('title')).not.toContain('private-user');
  expect(url.searchParams.get('body')).toContain('**Summary:** Dubbing stops at 40%');
});

it('never splits an emoji when truncating the description for the title', async () => {
  mock.api.mockResolvedValue({});
  render(<ReportBug />);
  await submitReport('a'.repeat(99) + '😀 tail');
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const title = new URL(mock.open.mock.calls[0]![0]).searchParams.get('title')!;
  expect(title).toBe('[Bug] ' + 'a'.repeat(99) + '😀');
});

it('survives a lone surrogate in the description', async () => {
  mock.api.mockResolvedValue({});
  render(<ReportBug />);
  await submitReport('broken \ud83d text');
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
});

it('titles a coded failure by code and leads the report with its facts', async () => {
  mock.api.mockRejectedValue(new Error('offline'));
  mock.backendStatus.mockResolvedValue({
    stage: 'failed',
    managed: true,
    message: 'Could not start python.exe: spawn UNKNOWN',
    logTail: Array(40).fill('spawn UNKNOWN'),
    failure: {
      code: 'EXEC_REFUSED',
      stage: 'failed',
      program: 'python.exe',
      programLocation: 'runtime',
      smartAppControl: 'off',
      signature: 'NotSigned',
    },
  });
  render(<ReportBug />);
  await submitReport('spawn UNKNOWN');
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const url = new URL(mock.open.mock.calls[0]![0]);
  const body = url.searchParams.get('body')!;
  expect(url.searchParams.get('title')).toBe('[Install] EXEC_REFUSED: spawn UNKNOWN');
  expect(url.searchParams.get('labels')).toBe('bug,install');
  expect(body).toContain('<!-- vs-failure:EXEC_REFUSED:1 -->');
  expect(body).toContain('- Program: python.exe (runtime)');
  expect(body).toContain('- Smart App Control: off');
  expect(body.indexOf('## Install failure')).toBeLessThan(body.indexOf('## Environment'));
  expect(body).toContain('spawn UNKNOWN (×40)');
});

it('shows a failed diagnostics save inside the report dialog', async () => {
  mock.api.mockRejectedValue(new Error('offline'));
  mock.snapshotFailure = { code: 'EXEC_REFUSED', stage: 'failed' };
  render(<ReportBug />);
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
  mock.saveData.mockRejectedValue(new Error('disk full'));
  fireEvent.click(await screen.findByRole('button', { name: 'reportBug.save_diagnostics' }));
  expect((await within(screen.getByRole('dialog')).findByRole('alert')).textContent).toBe(
    'reportBug.save_diagnostics_failed',
  );
});

it('saves diagnostics once when the button is clicked twice', async () => {
  mock.api.mockRejectedValue(new Error('offline'));
  mock.snapshotFailure = { code: 'EXEC_REFUSED', stage: 'failed' };
  let finish = () => {};
  mock.saveData.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  render(<ReportBug />);
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
  const save = await screen.findByRole('button', { name: 'reportBug.save_diagnostics' });
  fireEvent.click(save);
  await waitFor(() => expect(mock.saveData).toHaveBeenCalledTimes(1));
  fireEvent.click(save);
  finish();
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
  expect(mock.saveData).toHaveBeenCalledTimes(1);
});
