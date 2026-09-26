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
  }),
}));
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { addBreadcrumb, clearBreadcrumbs } from '@shared/utils/breadcrumbs';
import {
  _resetBackendContactForTests,
  recordBackendContact,
} from '@shared/utils/backendContact';
const mock = vi.hoisted(() => ({
  crashed: false,
  native: true,
  api: vi.fn(),
  open: vi.fn().mockResolvedValue(undefined),
  updateState: vi.fn(),
  backendStatus: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api }));
vi.mock('./bridge', () => ({
  getBridge: () =>
    mock.native
      ? {
          files: { openExternal: mock.open },
          updates: { getState: mock.updateState },
          backend: { getStatus: mock.backendStatus },
        }
      : null,
}));
vi.mock('@/router', () => ({ router: { navigate: mock.navigate } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { ReportBug } from './report-bug';
beforeEach(() => {
  clearBreadcrumbs();
  _resetBackendContactForTests();
  mock.updateState.mockResolvedValue({
    status: 'unsupported',
    currentVersion: '0.5.2',
    channel: 'stable',
    progress: 0,
  });
  mock.backendStatus.mockResolvedValue(null);
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
    fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
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
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
  await screen.findByRole('alert');
  expect(open).toHaveBeenCalledWith('about:blank', '_blank');
  expect(mock.api).not.toHaveBeenCalled();
});
it('navigates the reserved browser window after diagnostics resolve', async () => {
  mock.native = false;
  const replace = vi.fn();
  const popup = { opener: window, closed: false, location: { replace }, close: vi.fn() };
  vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  mock.api.mockResolvedValue({});
  render(<ReportBug />);
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
  expect(popup.opener).toBeNull();
  await waitFor(() => expect(replace).toHaveBeenCalledTimes(1));
  expect(replace.mock.calls[0]![0]).toContain('https://github.com/');
});

it('preserves the scrubbed current crash and original chained cause when the API is down', async () => {
  mock.crashed = true;
  mock.api.mockRejectedValue(new Error('offline'));
  render(<ReportBug />);
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
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
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('## Last native backend exit');
  expect(body).toContain('Exit code: 23');
  expect(body).toContain('Uptime (seconds): 4');
  expect(body).toContain('durable native failure');
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
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
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

  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
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

  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
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

  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));

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

  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));

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

  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));

  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  expect(body).toContain('## Current backend log');
  expect(body).toContain('RuntimeError: failed in ~/model');
  expect(body).not.toContain('/Users/alice');
  expect(body).not.toContain(secret);
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
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
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
  fireEvent.click(screen.getByRole('button', { name: 'reportBug.label' }));
  await waitFor(() => expect(mock.open).toHaveBeenCalledTimes(1));
  const body = new URL(mock.open.mock.calls[0]![0]).searchParams.get('body')!;
  const [current, recorded] = body.split('## Last native backend exit');
  expect(current).not.toContain('reportBug.native_fault_cause');
  expect(recorded).toContain('reportBug.native_fault_cause');
});
