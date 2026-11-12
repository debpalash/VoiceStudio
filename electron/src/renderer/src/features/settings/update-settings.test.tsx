import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  setChannel: vi.fn(),
  setAutoDownload: vi.fn(),
  later: vi.fn(),
  openExternal: vi.fn(),
  install: vi.fn(),
  listReleases: vi.fn(),
  flushDubDraft: vi.fn(),
  api: vi.fn(),
  toastError: vi.fn(),
  activeWork: false,
  noBridge: false,
  stage: 'ready',
}));

vi.mock('@/components/bridge', () => {
  // Electron exposes one stable bridge. Recreating it on every render reruns
  // the state-subscription effect and can overwrite a completed update check.
  const bridge = {
    updates: {
      getState: mocks.getState,
      check: mocks.check,
      download: mocks.download,
      dismiss: mocks.dismiss,
      setChannel: mocks.setChannel,
      setAutoDownload: mocks.setAutoDownload,
      later: mocks.later,
      install: mocks.install,
      listReleases: mocks.listReleases,
      onState: (listener: (state: UpdateState) => void) => {
        mocks.listener = listener;
        return () => {
          mocks.listener = undefined;
        };
      },
    },
    files: { openExternal: mocks.openExternal },
  };
  return { appVersion: () => '0.5.2', getBridge: () => (mocks.noBridge ? null : bridge) };
});

vi.mock('@/features/dub/dub-session', () => ({
  dubSession: { state: { phase: 'idle', recovery: null, batchProgress: null } },
  flushDubDraft: mocks.flushDubDraft,
  useDubSession: () => ({ phase: 'idle', recovery: null, batchProgress: null }),
}));

vi.mock('@/lib/app-activity', () => ({
  hasActiveAppWork: () => mocks.activeWork,
  activeAppWorkCount: () => (mocks.activeWork ? 1 : 0),
  useAppActivityCount: () => (mocks.activeWork ? 1 : 0),
}));

vi.mock('@/lib/api/client', () => ({ apiJson: mocks.api, describeError: String }));
vi.mock('@/hooks/use-backend-status', async () => ({
  ...(await vi.importActual<object>('@shared/utils/backendStage')),
  useBackendStatus: () => ({ stage: mocks.stage }),
}));
vi.mock('sonner', () => ({ toast: { error: mocks.toastError, info: vi.fn() } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { UpdateSettings } from './update-settings';
import { UpdateRestartConfirm } from '@/components/update-restart-confirm';

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UpdateSettings />
      <UpdateRestartConfirm />
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
    mocks.noBridge = false;
    mocks.stage = 'ready';
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
    mocks.setAutoDownload.mockImplementation(async (enabled: boolean) => ({
      ...mocks.state,
      autoDownload: enabled,
    }));
    mocks.openExternal.mockResolvedValue(undefined);
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

  test("renders What's new markdown without raw tables, links or asterisks", async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.includes('/changelog'))
        return {
          available: true,
          releases: [
            {
              version: '1.0.0',
              date: '2026-10-01',
              intro:
                '**Big news.** It ships.\n\n| Windows | [Installer](https://example.test/a.exe) |\n\nSee the [guide](https://example.test/g).',
              sections: [{ title: 'Fixed', bullets: ['**Lead.** Uses `code`. (#1)'] }],
            },
          ],
        };
      if (path.includes('/db-backup')) return { available: false, latest: null };
      return {};
    });
    const { container } = renderSettings();
    expect(await screen.findByText('Big news.')).toBeInTheDocument();
    expect(screen.getByText('See the guide.')).toBeInTheDocument();
    expect(screen.getByText('code').tagName).toBe('CODE');
    const text = container.textContent ?? '';
    expect(text).not.toContain('|');
    expect(text).not.toContain('**');
    expect(text).not.toContain('https://');
  });

  test('checks for updates and hides the channel choice when the build cannot switch', async () => {
    renderSettings();
    fireEvent.click(await screen.findByRole('button', { name: 'updates.check_now' }));
    await waitFor(() => expect(mocks.check).toHaveBeenCalledOnce());

    // Store and dev builds report no channel, so a picker would do nothing.
    expect(screen.queryByText('update.channel')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'update.channel_beta' })).not.toBeInTheDocument();
  });

  test.each([
    ['development', 'settings.updater_development'],
    ['package-manager', 'settings.updater_package_manager'],
    ['store', 'settings.updater_store'],
  ] as const)('says why a %s build cannot update itself', async (reason, key) => {
    mocks.state = {
      status: 'unsupported',
      currentVersion: '0.5.2',
      progress: 0,
      unsupportedReason: reason,
    };
    renderSettings();
    expect(await screen.findByText(key)).toBeInTheDocument();
    expect(screen.queryByText('settings.updater_desktop')).not.toBeInTheDocument();
  });

  test('tells the browser UI that updates belong to the desktop app', () => {
    mocks.noBridge = true;
    renderSettings();
    expect(screen.getByText('settings.updater_desktop')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'updates.check_now' })).not.toBeInTheDocument();
  });

  test('switches to Beta and checks that channel straight away', async () => {
    mocks.state = { ...mocks.state, channel: 'stable' };
    const beta = { ...mocks.state, channel: 'beta' as const };
    mocks.setChannel.mockResolvedValue(beta);
    mocks.check.mockResolvedValue({
      ...beta,
      status: 'available',
      availableVersion: '0.5.9-beta.1',
    });
    renderSettings();

    const stable = await screen.findByRole('radio', { name: 'update.channel_stable' });
    expect(stable).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: 'update.channel_beta' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
    expect(screen.getByRole('radiogroup', { name: 'update.channel' })).toBeInTheDocument();
    expect(screen.getByText('update.channel_current')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'update.channel_beta' }));

    await waitFor(() => expect(mocks.setChannel).toHaveBeenCalledWith('beta'));
    await waitFor(() => expect(mocks.check).toHaveBeenCalledOnce());
    expect(await screen.findByText('update.available')).toBeInTheDocument();
    expect(mocks.getState).toHaveBeenCalledOnce();
  });

  test('switches channel with the arrow keys', async () => {
    mocks.state = { ...mocks.state, channel: 'stable' };
    const beta = { ...mocks.state, channel: 'beta' as const };
    mocks.setChannel.mockResolvedValue(beta);
    mocks.check.mockResolvedValue(beta);
    renderSettings();

    const stable = await screen.findByRole('radio', { name: 'update.channel_stable' });
    expect(stable).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('radio', { name: 'update.channel_beta' })).toHaveAttribute(
      'tabindex',
      '-1',
    );
    fireEvent.keyDown(stable, { key: 'ArrowRight' });
    await waitFor(() => expect(mocks.setChannel).toHaveBeenCalledWith('beta'));
    await waitFor(() =>
      expect(screen.getByRole('radio', { name: 'update.channel_beta' })).toHaveFocus(),
    );
  });

  test('does not check when the channel switch is refused', async () => {
    mocks.state = { ...mocks.state, channel: 'stable' };
    mocks.setChannel.mockResolvedValue(mocks.state);
    renderSettings();

    fireEvent.click(await screen.findByRole('radio', { name: 'update.channel_beta' }));
    await waitFor(() => expect(mocks.setChannel).toHaveBeenCalledWith('beta'));
    expect(mocks.check).not.toHaveBeenCalled();
  });

  test('locks the channel while an update is downloading', async () => {
    mocks.state = { ...mocks.state, channel: 'stable', status: 'downloading', progress: 5 };
    renderSettings();
    expect(await screen.findByRole('radio', { name: 'update.channel_beta' })).toBeDisabled();
  });

  test('lists betas only for Beta users', async () => {
    mocks.state = { ...mocks.state, channel: 'beta' };
    mocks.listReleases.mockResolvedValue([
      {
        version: '0.5.9-beta.1',
        name: 'v0.5.9-beta.1',
        date: '2026-09-13T00:00:00Z',
        prerelease: true,
        notes: '',
      },
    ]);
    renderSettings();
    expect(await screen.findByText('v0.5.9-beta.1')).toBeInTheDocument();
  });

  test('Beta keeps stables out of its list and offers a newer stable as a switch', async () => {
    mocks.state = { ...mocks.state, channel: 'beta', currentVersion: '0.5.8-beta.5' };
    mocks.listReleases.mockResolvedValue([
      { version: '0.5.8', name: 'v0.5.8', date: '2026-11-12T08:53:00Z', prerelease: false, notes: '' },
      {
        version: '0.5.8-beta.5',
        name: 'v0.5.8-beta.5',
        date: '2026-11-12T00:27:00Z',
        prerelease: true,
        notes: '',
      },
    ]);
    renderSettings();
    expect(await screen.findByText('v0.5.8-beta.5')).toBeInTheDocument();
    expect(screen.queryByText('v0.5.8')).not.toBeInTheDocument();
    const suggestion = screen.getByTestId('newer-stable');
    expect(suggestion).toHaveTextContent('updates.stable_newer');
    fireEvent.click(within(suggestion).getByRole('button', { name: 'updates.switch_to_stable' }));
    await waitFor(() => expect(mocks.setChannel).toHaveBeenCalledWith('stable'));
  });

  test('lists tagged releases only', async () => {
    mocks.listReleases.mockResolvedValue([
      {
        version: '0.5.2',
        name: 'v0.5.2',
        date: '2026-09-12T00:00:00Z',
        prerelease: false,
        notes: '',
      },
      {
        version: '0.5.3-7',
        name: 'preview',
        date: '2026-09-13T00:00:00Z',
        prerelease: true,
        notes: '',
      },
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

  test('asks before a restart stops running work, and restarts only when confirmed', async () => {
    mocks.activeWork = true;
    mocks.state = {
      ...mocks.state,
      status: 'downloaded',
      availableVersion: '0.5.3',
      progress: 100,
    };
    renderSettings();

    fireEvent.click(await screen.findByRole('button', { name: 'update.restart' }));
    expect(await screen.findByText('update.restart_confirm')).toBeInTheDocument();
    expect(mocks.install).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'update.toast_later' }));
    await waitFor(() => expect(screen.queryByText('update.restart_confirm')).toBeNull());
    expect(mocks.install).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'update.restart' }));
    fireEvent.click(await screen.findByRole('button', { name: 'update.restart_anyway' }));
    await waitFor(() => expect(mocks.install).toHaveBeenCalledOnce());
    expect(mocks.flushDubDraft).toHaveBeenCalledOnce();
  });

  test('shows a ready update with a success icon and says when it installs', async () => {
    mocks.state = {
      ...mocks.state,
      status: 'downloaded',
      availableVersion: '0.5.3',
      progress: 100,
      installOnQuit: true,
    };
    renderSettings();
    expect(await screen.findByText('update.ready_hint_quit')).toBeInTheDocument();
    const icon = document.querySelector('[data-slot="update-status-icon"] svg');
    expect(icon).toHaveClass('text-success');
    expect(icon?.getAttribute('class')).not.toMatch(/rotate|refresh/);
  });

  test('toggles automatic downloads', async () => {
    mocks.state = { ...mocks.state, autoDownload: true, installOnQuit: false };
    renderSettings();
    const toggle = await screen.findByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('update.auto_download_hint_restart')).toBeInTheDocument();
    fireEvent.click(toggle);
    await waitFor(() => expect(mocks.setAutoDownload).toHaveBeenCalledWith(false));
  });

  test('hides the automatic download setting where the app cannot update itself', async () => {
    mocks.state = { ...mocks.state, status: 'unsupported', unsupportedReason: 'development' };
    renderSettings();
    expect(await screen.findByText('settings.updater_development')).toBeInTheDocument();
    expect(screen.queryByRole('switch')).toBeNull();
  });

  test.each([
    ['check', 'update.error_check'],
    ['download', 'update.error_download'],
    ['verify', 'update.error_verify'],
    ['install', 'update.error_install'],
    ['not-applied', 'update.error_not_applied'],
  ] as const)('names a %s failure and offers a manual download', async (errorKind, title) => {
    mocks.state = {
      ...mocks.state,
      status: 'error',
      availableVersion: '0.5.3',
      error: 'boom',
      errorKind,
    };
    renderSettings();
    expect(await screen.findByText(title)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'update.download_manually' }));
    await waitFor(() =>
      expect(mocks.openExternal).toHaveBeenCalledWith(
        'https://github.com/debpalash/VoiceStudio/releases/tag/v0.5.3',
      ),
    );
  });

  test('keeps the chosen channel legible while switching is locked', async () => {
    mocks.state = {
      ...mocks.state,
      status: 'downloaded',
      availableVersion: '0.5.3',
      channel: 'beta',
    };
    renderSettings();
    const beta = await screen.findByRole('radio', { name: 'update.channel_beta' });
    const stable = screen.getByRole('radio', { name: 'update.channel_stable' });
    expect(beta).toHaveAttribute('aria-checked', 'true');
    expect(stable).toHaveAttribute('aria-checked', 'false');
    expect(beta).toBeDisabled();
    // The selected option is not dimmed with the locked one.
    expect(beta.className).not.toMatch(/opacity/);
    expect(stable.className).toMatch(/disabled:opacity/);
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

  test('says Database backup needs the runtime instead of loading forever', async () => {
    mocks.stage = 'stopped';
    renderSettings();
    expect(await screen.findByText('updates.backup_needs_runtime')).toBeInTheDocument();
    expect(screen.queryByText('common.loading')).not.toBeInTheDocument();
    expect(mocks.api).not.toHaveBeenCalledWith(
      expect.stringContaining('/db-backup'),
      expect.anything(),
    );
  });

  test('shows a retry when the backup status cannot load', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.includes('/db-backup')) throw new Error('down');
      return path.includes('/changelog') ? { available: false, releases: [] } : {};
    });
    renderSettings();
    const retry = await screen.findByRole('button', { name: 'updates.retry_load' });
    mocks.api.mockImplementation(async (path: string) =>
      path.includes('/db-backup') ? { available: false, latest: null } : {},
    );
    fireEvent.click(retry);
    expect(await screen.findByText('updates.backup_none')).toBeInTheDocument();
  });

  test('shows a retry when the release list fails', async () => {
    mocks.listReleases.mockRejectedValue(new Error('504'));
    renderSettings();
    const retry = await screen.findByRole('button', { name: 'updates.retry_load' });
    expect(screen.getByText('updates.load_error')).toBeInTheDocument();
    mocks.listReleases.mockResolvedValue([]);
    fireEvent.click(retry);
    expect(await screen.findByText('updates.none')).toBeInTheDocument();
  });

  test('shows skeletons, then an error with Retry after 10 seconds, never a bare Loading', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mocks.listReleases.mockReturnValue(new Promise(() => undefined));
      mocks.api.mockImplementation((path: string) =>
        path.includes('/db-backup') ? new Promise(() => undefined) : Promise.resolve({}),
      );
      renderSettings();
      expect(await screen.findByRole('status', { name: 'common.loading' })).toBeInTheDocument();
      expect(screen.getByRole('status', { name: 'updates.loading' })).toBeInTheDocument();
      expect(screen.queryByText('common.loading')).toBeNull();
      expect(screen.queryByText('updates.loading')).toBeNull();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_500);
      });
      expect(await screen.findByText('updates.backup_load_error')).toBeInTheDocument();
      expect(screen.getByText('updates.load_error')).toBeInTheDocument();
      fireEvent.click(screen.getByText('updates.open_releases'));
      await waitFor(() =>
        expect(mocks.openExternal).toHaveBeenCalledWith(
          'https://github.com/debpalash/VoiceStudio/releases',
        ),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  test('stops loading releases when GitHub never answers', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mocks.listReleases.mockReturnValue(new Promise(() => undefined));
      renderSettings();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(40_000);
      });
      expect(await screen.findByText('updates.load_error')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
