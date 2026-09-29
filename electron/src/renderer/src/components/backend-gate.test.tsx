import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import i18n from '@/i18n';
import type { BackendStatus } from '../../../preload/index.d';
import { BackendGate } from './backend-gate';

const { backendStatus, platform } = vi.hoisted(() => ({
  backendStatus: {
    stage: 'setup_required',
    baseUrl: 'http://127.0.0.1:3900',
    port: 3900,
    managed: false,
    remote: false,
    elapsedMs: 0,
    logTail: [],
    runtimePath: 'C:\\VoiceStudio\\runtime',
    runtimeInterrupted: true,
    runtimeRegion: 'auto',
  } as BackendStatus,
  platform: { current: 'linux' },
}));

vi.mock('@/hooks/use-backend-status', () => ({
  useBackendStatus: () => backendStatus,
}));

vi.mock('./bridge', () => ({
  getBridge: () => null,
  isMac: () => platform.current === 'darwin',
}));

beforeEach(() => {
  backendStatus.stage = 'setup_required';
  backendStatus.elapsedMs = 0;
  backendStatus.logTail = [];
  delete backendStatus.message;
  delete backendStatus.setupIssue;
  delete backendStatus.setupPhase;
  delete backendStatus.setupProgress;
  platform.current = 'linux';
});

it('presents an interrupted runtime as a resumable install', () => {
  render(
    <BackendGate>
      <div>workspace</div>
    </BackendGate>,
  );

  expect(screen.getByText(i18n.t('modelMaintenance.repairDescription'))).toBeInTheDocument();
  expect(screen.getByRole('button', { name: i18n.t('common.resume') })).toBeEnabled();
  expect(
    screen.queryByRole('button', { name: i18n.t('backend.setup_required') }),
  ).not.toBeInTheDocument();
});

it('keeps branded chrome outside the scrolling installer content', () => {
  backendStatus.stage = 'installing';
  backendStatus.elapsedMs = 30_000;

  render(
    <BackendGate>
      <div>workspace</div>
    </BackendGate>,
  );

  const header = screen.getByRole('banner');
  const scrollRegion = screen.getByTestId('backend-gate-scroll');
  expect(header).toHaveTextContent(i18n.t('app.name'));
  expect(scrollRegion).not.toContainElement(header);
});

it('keeps the startup brand clear of macOS traffic lights only on macOS', () => {
  backendStatus.stage = 'starting';
  platform.current = 'darwin';

  const { rerender } = render(
    <BackendGate>
      <div>workspace</div>
    </BackendGate>,
  );

  expect(screen.getByRole('banner')).toHaveClass('pl-24');

  platform.current = 'win32';
  rerender(
    <BackendGate>
      <div>workspace</div>
    </BackendGate>,
  );
  expect(screen.getByRole('banner')).not.toHaveClass('pl-24');
});

it('shows package installation after the last large download instead of a stale package', () => {
  backendStatus.stage = 'installing';
  backendStatus.setupPhase = 'installing_deps';
  backendStatus.logTail = ['Downloaded scipy'];
  backendStatus.setupProgress = {
    resolvedPackages: 227,
    completedDownloads: 42,
    downloadedBytes: 3.6 * 1024 ** 3,
    totalBytes: 3.6 * 1024 ** 3,
    downloadsComplete: true,
  };

  render(
    <BackendGate>
      <div>workspace</div>
    </BackendGate>,
  );

  expect(screen.getByText(i18n.t('bootstrap.downloads_complete'))).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  expect(screen.queryByText('Downloaded scipy')).not.toBeInTheDocument();
});

it('keeps agent repair available when the backend is down', () => {
  backendStatus.stage = 'failed';

  render(
    <BackendGate repairDock={<div>repair dock</div>}>
      <div>workspace</div>
    </BackendGate>,
  );

  expect(screen.getByText('repair dock')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: i18n.t('repairAgent.fix') })).toBeEnabled();
});

it('explains unsupported Windows proxy bypass rules before retrying setup', () => {

  backendStatus.message = 'VOICESTUDIO_PROXY_BYPASS_UNSUPPORTED';
  render(
    <BackendGate>
      <div>workspace</div>
    </BackendGate>,
  );
  expect(screen.getByText(i18n.t('backend.proxy_bypass_help'))).toBeVisible();
  expect(i18n.t('backend.proxy_bypass_help')).toMatch(
    /quit VoiceStudio.*launch VoiceStudio from that terminal/,
  );
});

it('offers a remote backend instead of a doomed local install on Intel Macs', () => {
  backendStatus.setupIssue = 'unsupported_platform';
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <BackendGate>
        <div>workspace</div>
      </BackendGate>
    </QueryClientProvider>,
  );

  expect(screen.getByText(i18n.t('backend.setup_unsupported_platform'))).toBeVisible();
  expect(
    screen.queryByRole('button', { name: i18n.t('backend.setup_required') }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('textbox', { name: i18n.t('settings.remote_backend_url') }),
  ).toBeVisible();
});
