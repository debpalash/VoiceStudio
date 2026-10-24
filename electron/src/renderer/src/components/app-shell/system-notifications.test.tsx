import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { UpdateState } from '../../../../preload/index.d';
import { apiJson } from '@/lib/api/client';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  openExternal: vi.fn().mockResolvedValue(undefined),
  toastError: vi.fn(),
  listener: undefined as ((state: UpdateState) => void) | undefined,
  state: {
    status: 'available',
    currentVersion: '0.5.2',
    availableVersion: '0.5.3',
    channel: 'stable',
    progress: 0,
  } as UpdateState,
}));

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mocks.navigate }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({
    files: { openExternal: mocks.openExternal },
    updates: {
      getState: vi.fn(async () => mocks.state),
      onState: (listener: (state: UpdateState) => void) => {
        mocks.listener = listener;
        return () => {
          mocks.listener = undefined;
        };
      },
    },
  }),
}));
vi.mock('sonner', () => ({ toast: { error: mocks.toastError } }));
vi.mock('@/lib/api/client', () => ({
  apiJson: vi.fn(async () => ({ notifications: [] })),
  apiFetch: vi.fn(),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { version?: string }) =>
      values?.version ? `${key} ${values.version}` : key,
  }),
}));

import { SystemNotifications } from './system-notifications';

describe('SystemNotifications desktop updates', () => {
  beforeEach(() => {
    mocks.navigate.mockReset();
    mocks.openExternal.mockReset().mockResolvedValue(undefined);
    mocks.toastError.mockReset();
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
    vi.restoreAllMocks();
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
    fireEvent.click(await screen.findByText('common.open'));
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith({ to: '/settings/updates' }));
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

  test('reports a refused notification link without leaking its rejection', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mocks.openExternal.mockRejectedValueOnce(new Error('Default browser is unavailable'));
    vi.mocked(apiJson).mockResolvedValueOnce({
      notifications: [
        {
          id: 'help-link',
          level: 'info',
          title: 'Documentation',
          action: { type: 'link', target: 'https://voicestudio.sh/docs' },
        },
      ],
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SystemNotifications enabled />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: /Documentation/ }));
    fireEvent.click(await screen.findByText('Documentation'));
    await waitFor(() =>
      expect(mocks.toastError).toHaveBeenCalledWith('integrationCatalog.websiteOpenFailed'),
    );
    expect(mocks.openExternal).toHaveBeenCalledWith('https://voicestudio.sh/docs');
    expect(warning).toHaveBeenCalledWith(
      'Could not open external link:',
      'Default browser is unavailable',
    );
    warning.mockRestore();
  });
});
