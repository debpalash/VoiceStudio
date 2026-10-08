import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { UpdateState } from '../../../../preload/index.d';

const mocks = vi.hoisted(() => ({
  state: {
    status: 'idle',
    currentVersion: '0.5.2',
    progress: 0,
  } as UpdateState,
  listener: undefined as ((state: UpdateState) => void) | undefined,
  getState: vi.fn(),
  getStateError: undefined as Error | undefined,
  check: vi.fn(),
  download: vi.fn(),
  dismiss: vi.fn(),
  install: vi.fn(),
  listReleases: vi.fn(),
  flushDubDraft: vi.fn(),
  api: vi.fn(),
  toastError: vi.fn(),
  activeWork: false,
}));

vi.mock('@/components/bridge', () => ({
  appVersion: () => '0.5.2',
  getBridge: () => ({
    updates: {
      getState: mocks.getState,
      check: mocks.check,
      download: mocks.download,
      dismiss: mocks.dismiss,
      install: mocks.install,
      listReleases: mocks.listReleases,
      onState: (listener: (state: UpdateState) => void) => {
        mocks.listener = listener;
        return () => {
          mocks.listener = undefined;
        };
      },
    },
  }),
}));

vi.mock('@/features/dub/dub-session', () => ({
  dubSession: { state: { phase: 'idle', recovery: null, batchProgress: null } },
  flushDubDraft: mocks.flushDubDraft,
  useDubSession: () => ({ phase: 'idle', recovery: null, batchProgress: null }),
}));

vi.mock('@/lib/app-activity', () => ({
  hasActiveAppWork: () => mocks.activeWork,
  useAppActivityCount: () => (mocks.activeWork ? 1 : 0),
}));

vi.mock('@/lib/api/client', () => ({ apiJson: mocks.api, describeError: String }));
vi.mock('sonner', () => ({ toast: { error: mocks.toastError, info: vi.fn() } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { UpdateSettings } from './update-settings';

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UpdateSettings />
    </QueryClientProvider>,
  );
}

describe('UpdateSettings', () => {
  beforeEach(() => {
    mocks.state = {
      status: 'idle',
      currentVersion: '0.5.2',
      progress: 0,
    };
    mocks.listener = undefined;
    mocks.activeWork = false;
    mocks.getStateError = undefined;
    vi.clearAllMocks();
    mocks.getState.mockImplementation(async () => {
      if (mocks.getStateError) throw mocks.getStateError;
      return mocks.state;
    });
    mocks.check.mockResolvedValue(mocks.state);
    mocks.download.mockResolvedValue(mocks.state);
    mocks.dismiss.mockResolvedValue(mocks.state);
    mocks.install.mockResolvedValue(undefined);
    mocks.listReleases.mockResolvedValue([]);
    mocks.api.mockImplementation(async (path: string) => {
      if (path.includes('/models/install/status')) return { jobs: [] };
      if (path.includes('/batch/jobs')) return [];
      if (path.includes('/changelog')) return { available: false, releases: [] };
      if (path.includes('/db-backup')) return { available: false, latest: null };
      return {};
    });
  });

  afterEach(cleanup);

  test('checks for updates and offers no update-channel choice', async () => {
    renderSettings();
    fireEvent.click(await screen.findByRole('button', { name: 'updates.check_now' }));
    await waitFor(() => expect(mocks.check).toHaveBeenCalledOnce());

    // No Preview feed is published, so a channel picker would only mislead.
    expect(screen.queryByText('about.update_channel')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'about.channel_preview' })).not.toBeInTheDocument();
  });

  test('lists tagged releases only', async () => {
    mocks.listReleases.mockResolvedValue([
      { version: '0.5.2', name: 'v0.5.2', date: '2026-09-12T00:00:00Z', prerelease: false, notes: '' },
      { version: '0.5.3-7', name: 'preview', date: '2026-09-13T00:00:00Z', prerelease: true, notes: '' },
    ]);
    renderSettings();
    expect(await screen.findByText('v0.5.2')).toBeInTheDocument();
    expect(screen.queryByText('v0.5.3-7')).not.toBeInTheDocument();
    expect(mocks.listReleases).toHaveBeenCalledWith();
  });

  test('keeps progress live and downloads an available update', async () => {
    mocks.state = {
      ...mocks.state,
      status: 'available',
      availableVersion: '0.5.3',
    };
    mocks.download.mockResolvedValue({ ...mocks.state, status: 'downloading', progress: 0 });
    renderSettings();

    fireEvent.click(await screen.findByRole('button', { name: 'update.download' }));
    await waitFor(() => expect(mocks.download).toHaveBeenCalledOnce());

    act(() => {
      mocks.listener?.({
        ...mocks.state,
        status: 'downloading',
        progress: 47,
        transferredBytes: 470,
        totalBytes: 1_000,
        bytesPerSecond: 100,
        etaSeconds: 6,
      });
    });
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '47');
    expect(screen.getByText('update.download_detail')).toBeInTheDocument();
  });

  test('retries or dismisses a persistent update error', async () => {
    mocks.state = {
      ...mocks.state,
      status: 'error',
      error: 'feed unavailable',
    };
    mocks.dismiss.mockResolvedValue({ ...mocks.state, status: 'idle', error: undefined });
    renderSettings();

    fireEvent.click(await screen.findByRole('button', { name: 'update.dismiss' }));
    await waitFor(() => expect(mocks.dismiss).toHaveBeenCalledOnce());
  });

  test('downloads during active work but waits for a safe restart', async () => {
    mocks.activeWork = true;
    mocks.state = {
      ...mocks.state,
      status: 'available',
      availableVersion: '0.5.3',
    };
    const view = renderSettings();
    expect(await screen.findByRole('button', { name: 'update.download' })).toBeEnabled();

    mocks.state = { ...mocks.state, status: 'downloaded', progress: 100 };
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <UpdateSettings />
      </QueryClientProvider>,
    );
    act(() => mocks.listener?.(mocks.state));
    expect(await screen.findByRole('button', { name: 'update.restart' })).toBeDisabled();
  });

  test('flushes the current draft before restarting into a downloaded update', async () => {
    mocks.state = {
      ...mocks.state,
      status: 'downloaded',
      availableVersion: '0.5.3',
      progress: 100,
    };
    renderSettings();

    fireEvent.click(await screen.findByRole('button', { name: 'update.restart' }));
    await waitFor(() => expect(mocks.install).toHaveBeenCalledOnce());
    expect(mocks.flushDubDraft).toHaveBeenCalledOnce();
  });

  test('contains rejected updater IPC instead of leaking unhandled promises', async () => {
    mocks.getStateError = new Error('state IPC failed');
    const view = renderSettings();
    expect(await screen.findByText(/state IPC failed/)).toBeInTheDocument();
    view.unmount();
    mocks.getStateError = undefined;

    mocks.state = {
      ...mocks.state,
      status: 'downloaded',
      availableVersion: '0.5.3',
      progress: 100,
    };
    mocks.install.mockRejectedValueOnce(new Error('install IPC failed'));
    renderSettings();
    fireEvent.click(await screen.findByRole('button', { name: 'update.restart' }));
    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('update.failed'));
  });

  test('renders remote release notes as inert markdown-lite content', async () => {
    mocks.listReleases.mockResolvedValue([
      {
        version: '0.5.1',
        name: 'VoiceStudio 0.5.1',
        date: '2026-09-12T00:00:00Z',
        prerelease: false,
        notes: '### Fixed\n- **Safe notes** use `native updates`.\n\n<script>alert(1)</script>',
      },
    ]);
    renderSettings();

    fireEvent.click(await screen.findByText('v0.5.1'));
    expect(await screen.findByRole('heading', { name: 'Fixed' })).toBeInTheDocument();
    expect(screen.getByText('Safe notes', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('native updates', { selector: 'code' })).toBeInTheDocument();
    expect(screen.queryByRole('script')).not.toBeInTheDocument();
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument();
  });
});
