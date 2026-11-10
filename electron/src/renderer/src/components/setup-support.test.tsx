import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import i18n from '@/i18n';
import type { BackendStatus } from '../../../preload/index.d';
import {
  SetupCpuFallback,
  SetupLogButton,
  SetupStallNotice,
  setupConnectionError,
  setupNeedsHelp,
} from './setup-support';

const mock = vi.hoisted(() => ({
  diagnostics: vi.fn(),
  save: vi.fn(),
  check: vi.fn(),
  external: vi.fn(),
  useCpu: vi.fn(),
}));
vi.mock('./bridge', () => ({
  getBridge: () => ({
    backend: {
      getSetupDiagnostics: mock.diagnostics,
      checkSetupConnection: mock.check,
      setupRuntimeOnCpu: mock.useCpu,
    },
    files: { saveData: mock.save, openExternal: mock.external },
  }),
}));
const status = (seconds: number): BackendStatus => ({
  stage: 'installing',
  baseUrl: 'http://127.0.0.1:3900',
  port: 3900,
  managed: true,
  remote: false,
  elapsedMs: 900_000,
  logTail: [],
  setupPhase: 'installing_deps',
  setupProgress: {
    completedDownloads: 0,
    downloadsComplete: false,
    stalled: true,
    stalledSeconds: seconds,
  },
});
beforeEach(() => {
  vi.clearAllMocks();
  mock.diagnostics.mockResolvedValue('Setup log: redacted');
  mock.save.mockResolvedValue({ canceled: false });
  mock.external.mockResolvedValue(undefined);
  mock.check.mockResolvedValue({
    status: 'ok',
    host: 'download-r2.pytorch.org',
    bytesPerSecond: 1024,
  });
});
it('escalates at five minutes without progress and clears when bytes resume', () => {
  const { rerender } = render(<SetupStallNotice status={status(299)} />);
  expect(screen.queryByTestId('setup-stall-help')).not.toBeInTheDocument();
  expect(setupNeedsHelp(status(299))).toBe(false);
  rerender(<SetupStallNotice status={status(300)} />);
  expect(screen.getByTestId('setup-stall-help')).toHaveTextContent(i18n.t('setupHelp.title'));
  expect(setupNeedsHelp(status(300))).toBe(true);
  const moving = status(0);
  moving.setupProgress!.stalled = false;
  rerender(<SetupStallNotice status={moving} />);
  expect(screen.queryByTestId('setup-stall-help')).not.toBeInTheDocument();
  expect(mock.check).not.toHaveBeenCalled();
  expect(mock.diagnostics).not.toHaveBeenCalled();
});
it('needs help immediately after a setup failure and recognises TLS errors', () => {
  const failed: BackendStatus = {
    ...status(0),
    stage: 'setup_required',
    message: 'Runtime setup exited with code 1',
    logTail: ["cannot decrypt peer's message"],
  };
  expect(setupNeedsHelp(failed)).toBe(true);
  expect(setupConnectionError(failed)).toBe(true);
  expect(setupConnectionError({ ...failed, logTail: ['uv sync failed'] })).toBe(false);
});
it('exports during setup without requiring a backend and handles save cancellation', async () => {
  mock.save.mockResolvedValue({ canceled: true });
  render(<SetupLogButton />);
  fireEvent.click(screen.getByRole('button', { name: i18n.t('setupHelp.download_log') }));
  await waitFor(() => expect(mock.save).toHaveBeenCalled());
  expect(new TextDecoder().decode(mock.save.mock.calls[0][0].data)).toBe('Setup log: redacted');
  expect(mock.save.mock.calls[0][0].suggestedName).toBe('voicestudio-setup.log');
  expect(mock.external).not.toHaveBeenCalled();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('checks only on a user click and shows the bounded sample result', async () => {
  render(<SetupStallNotice status={status(300)} />);
  expect(mock.check).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: i18n.t('setupHelp.check_connection') }));
  await waitFor(() =>
    expect(
      screen.getByText(i18n.t('setupHelp.check_ok', { host: 'download-r2.pytorch.org', rate: 1 })),
    ).toBeVisible(),
  );
  expect(mock.check).toHaveBeenCalledTimes(1);
  expect(mock.check).toHaveBeenCalledWith();
});
it('keeps log export retryable if saving fails', async () => {
  mock.save.mockRejectedValue(new Error('disk full'));
  render(<SetupLogButton />);
  const button = screen.getByRole('button', { name: i18n.t('setupHelp.download_log') });
  fireEvent.click(button);
  expect(await screen.findByRole('alert')).toHaveTextContent(i18n.t('common.error'));
  expect(button).toBeEnabled();
});

it('offers the CPU install only when main says it would help, and starts it', async () => {
  mock.useCpu.mockResolvedValue(undefined);
  const { rerender } = render(<SetupCpuFallback status={status(300)} />);
  expect(screen.queryByRole('button', { name: i18n.t('setupHelp.use_cpu') })).toBeNull();

  rerender(<SetupCpuFallback status={{ ...status(300), cpuSetupAvailable: true }} />);
  expect(screen.getByText(i18n.t('setupHelp.use_cpu_hint'))).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: i18n.t('setupHelp.use_cpu') }));
  await waitFor(() => expect(mock.useCpu).toHaveBeenCalledOnce());
});
