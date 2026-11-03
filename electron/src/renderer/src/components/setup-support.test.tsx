import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import i18n from '@/i18n';
import type { BackendStatus } from '../../../preload/index.d';
import { SetupSupport } from './setup-support';

const mock = vi.hoisted(() => ({
  diagnostics: vi.fn(),
  save: vi.fn(),
  check: vi.fn(),
  external: vi.fn(),
}));
vi.mock('./bridge', () => ({
  getBridge: () => ({
    backend: { getSetupDiagnostics: mock.diagnostics, checkSetupConnection: mock.check },
    files: { saveData: mock.save, openExternal: mock.external },
  }),
}));
vi.mock('./report-bug', () => ({
  ReportBug: ({ setup }: { setup: boolean }) => (
    <button>{setup ? 'Setup GitHub report' : 'Other report'}</button>
  ),
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
it('escalates at five minutes without progress and clears help when bytes resume', () => {
  const { rerender } = render(<SetupSupport status={status(299)} />);
  expect(screen.queryByText('Setup GitHub report')).not.toBeInTheDocument();
  rerender(<SetupSupport status={status(300)} />);
  expect(screen.getByText('Setup GitHub report')).toBeVisible();
  expect(screen.getByRole('link', { name: i18n.t('setupHelp.discord') })).toHaveAttribute(
    'href',
    'https://discord.gg/bzQavDfVV9',
  );
  const moving = status(0);
  moving.setupProgress!.stalled = false;
  rerender(<SetupSupport status={moving} />);
  expect(screen.queryByText('Setup GitHub report')).not.toBeInTheDocument();
  expect(mock.check).not.toHaveBeenCalled();
  expect(mock.diagnostics).not.toHaveBeenCalled();
});
it('offers support immediately after a setup failure and explains TLS errors', () => {
  render(
    <SetupSupport
      status={{
        ...status(0),
        stage: 'setup_required',
        message: 'Runtime setup exited with code 1',
        logTail: ["cannot decrypt peer's message"],
      }}
    />,
  );
  expect(screen.getByText('Setup GitHub report')).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent(i18n.t('setupHelp.connection_error'));
});
it('exports during setup without requiring a backend and handles save cancellation', async () => {
  mock.save.mockResolvedValue({ canceled: true });
  render(<SetupSupport status={status(0)} />);
  fireEvent.click(screen.getByRole('button', { name: i18n.t('setupHelp.download_log') }));
  await waitFor(() => expect(mock.save).toHaveBeenCalled());
  expect(new TextDecoder().decode(mock.save.mock.calls[0][0].data)).toBe('Setup log: redacted');
  expect(mock.save.mock.calls[0][0].suggestedName).toBe('voicestudio-setup.log');
  expect(mock.external).not.toHaveBeenCalled();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('checks only on a user click and shows the bounded sample result', async () => {
  render(<SetupSupport status={status(300)} />);
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
  render(<SetupSupport status={status(0)} />);
  const button = screen.getByRole('button', { name: i18n.t('setupHelp.download_log') });
  fireEvent.click(button);
  expect(await screen.findByRole('alert')).toHaveTextContent(i18n.t('common.error'));
  expect(button).toBeEnabled();
});
