import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { UpdateState } from '../../../../preload/index.d';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
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
    mocks.listener = undefined;
    mocks.state = {
      status: 'available',
      currentVersion: '0.5.2',
      availableVersion: '0.5.3',
      channel: 'stable',
      progress: 0,
    } as UpdateState;
  });
  afterEach(cleanup);

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

  test('opens while notifications are still loading', async () => {
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

    const trigger = await screen.findByRole('button', { name: 'preferences.loading' });
    expect(trigger).toBeEnabled();
    expect(trigger).toHaveClass('app-no-drag');
    fireEvent.click(trigger);
    expect(await screen.findByText('preferences.loading')).toBeVisible();
  });
});
