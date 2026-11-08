import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { UpdateState } from '../../../../preload/index.d';
import { apiJson } from '@/lib/api/client';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  download: vi.fn(async () => ({})),
  install: vi.fn(async () => {}),
  listener: undefined as ((state: UpdateState) => void) | undefined,
  state: {
    status: 'available',
    currentVersion: '0.5.2',
    availableVersion: '0.5.3',
    channel: 'stable',
    progress: 0,
  } as UpdateState,
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
  Link: ({ to, children }: { to: string; children: unknown }) => (
    <a href={to}>{children as never}</a>
  ),
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({
    updates: {
      getState: vi.fn(async () => mocks.state),
      download: mocks.download,
      install: mocks.install,
      onState: (listener: (state: UpdateState) => void) => {
        mocks.listener = listener;
        return () => {
          mocks.listener = undefined;
        };
      },
    },
  }),
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: vi.fn(async () => ({ notifications: [] })),
  apiFetch: vi.fn(),
}));
// The downloads section has its own query and tests (download-activity.test.tsx).
const downloads = vi.hoisted(() => ({ items: [] as unknown[], active: 0 }));
vi.mock('./download-activity', () => ({
  useDownloadActivity: () => ({ ...downloads, pending: null }),
  DownloadActivityList: ({ activity }: { activity: { items: unknown[] } }) =>
    activity.items.length ? <section aria-label="downloads">downloads</section> : null,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { version?: string; count?: number }) =>
      values?.version
        ? `${key} ${values.version}`
        : values?.count !== undefined
          ? `${key} ${values.count}`
          : key,
  }),
}));

import { SystemNotifications } from './system-notifications';
import { publishLongformActivity } from '@/lib/longform-activity';

describe('SystemNotifications desktop updates', () => {
  beforeEach(() => {
    downloads.items = [];
    downloads.active = 0;
    mocks.navigate.mockReset();
    mocks.listener = undefined;
    mocks.state = {
      status: 'available',
      currentVersion: '0.5.2',
      availableVersion: '0.5.3',
      channel: 'stable',
      progress: 0,
    } as UpdateState;
  });
  afterEach(() => {
    cleanup();
    publishLongformActivity(null, null);
  });

  test('keeps an available update actionable while the Python backend is offline', async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SystemNotifications enabled={false} />
      </QueryClientProvider>,
    );

    const trigger = await screen.findByRole('button', { name: /update\.available 0\.5\.3/ });
    expect(trigger).toBeEnabled();
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByText('update.download'));
    await waitFor(() => expect(mocks.download).toHaveBeenCalledOnce());
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  test('a downloaded update restarts from the bell', async () => {
    mocks.state = { ...mocks.state, status: 'downloaded' } as UpdateState;
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SystemNotifications enabled={false} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: /update\.ready 0\.5\.3/ }));
    fireEvent.click(await screen.findByText('update.restart'));
    await waitFor(() => expect(mocks.install).toHaveBeenCalledOnce());
  });

  test('shows unavailable instead of endless loading when the backend is offline', async () => {
    mocks.state = {
      status: 'idle',
      currentVersion: '0.5.2',
      channel: 'stable',
      progress: 0,
    } as UpdateState;
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SystemNotifications enabled={false} titlebar />
      </QueryClientProvider>,
    );

    const trigger = await screen.findByRole('button', { name: 'modelSettings.unavailable' });
    expect(trigger).toBeEnabled();
    expect(trigger).toHaveClass('app-no-drag');
    fireEvent.click(trigger);
    expect(await screen.findByText('modelSettings.unavailable')).toBeVisible();
  });

  test('opens Storage from a low-disk warning', async () => {
    mocks.state = {
      status: 'idle',
      currentVersion: '0.5.2',
      channel: 'stable',
      progress: 0,
    } as UpdateState;
    vi.mocked(apiJson).mockResolvedValueOnce({
      notifications: [
        {
          id: 'disk-low',
          level: 'warn',
          title: 'Low disk space (1.8 GB free)',
          message: 'VoiceStudio needs disk space for models, audio, and temp files.',
          action: { type: 'settings-tab', target: 'storage' },
        },
      ],
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SystemNotifications enabled />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Low disk space/ }));
    const warning = (await screen.findByText('Low disk space (1.8 GB free)')).closest('button');
    expect(warning).toBeEnabled();
    expect(warning).toHaveTextContent('settings.storage');
    fireEvent.click(warning!);
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith({ to: '/settings/storage' }));
  });

  test('counts running downloads in the bell and lists them first', async () => {
    mocks.state = {
      status: 'idle',
      currentVersion: '0.5.2',
      channel: 'stable',
      progress: 0,
    } as UpdateState;
    downloads.items = [{ key: 'a' }, { key: 'b' }];
    downloads.active = 2;
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SystemNotifications enabled />
      </QueryClientProvider>,
    );
    const bell = await screen.findByRole('button', { name: /modelInstall.activeCount 2/ });
    expect(bell).toHaveTextContent('2');
    fireEvent.click(bell);
    expect(await screen.findByRole('region', { name: 'downloads' })).toBeInTheDocument();
    expect(screen.queryByText('logs.all_clear')).not.toBeInTheDocument();
  });

  test('lists a running Stories render with a way back to it and Stop', async () => {
    mocks.state = {
      status: 'idle',
      currentVersion: '0.5.2',
      channel: 'stable',
      progress: 0,
    } as UpdateState;
    const stop = vi.fn();
    publishLongformActivity(
      {
        mode: 'stories',
        title: 'My tale',
        stage: 'rendering',
        completed: 2,
        total: 5,
        percent: 40,
      },
      stop,
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SystemNotifications enabled />
      </QueryClientProvider>,
    );
    const bell = await screen.findByRole('button', {
      name: /nav\.stories: audiobook\.progress_summary/,
    });
    expect(bell).toHaveTextContent('1');
    fireEvent.click(bell);
    const card = await screen.findByRole('region', { name: 'nav.stories' });
    expect(card).toHaveTextContent('My tale');
    expect(screen.getByRole('link', { name: /audiobook\.show_progress/ })).toHaveAttribute(
      'href',
      '/stories',
    );
    fireEvent.click(screen.getByRole('button', { name: 'common.stop' }));
    expect(stop).toHaveBeenCalledOnce();
    expect(screen.queryByText('logs.all_clear')).not.toBeInTheDocument();
  });
});
