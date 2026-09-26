// LogsFooter notifications tab — dismissible system notes. Render-level
// coverage of the dismissed-ids filter through the REAL hook chain
// (useVisibleNotifications → useNotifications → TanStack Query): the API
// layer is mocked, a real QueryClient serves the render, and the store is
// the real one. Contract: an info note carries a dismiss button that hides
// it everywhere (tab + shared visible list), an error note does not, and
// dismissing never triggers the row's own action.
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { backendData, notificationData } = vi.hoisted(() => ({
  backendData: { lines: [] },
  notificationData: { current: [] },
}));

const NOTES = [
  {
    id: 'gpu-unavailable',
    level: 'info',
    title: 'Running on CPU',
    message: 'No GPU detected.',
    action: null,
  },
  {
    id: 'ffmpeg-missing',
    level: 'error',
    title: 'Media engine unavailable',
    message: 'ffmpeg is missing.',
    action: { label: 'Open Audio tools', type: 'settings-tab', target: 'audio-tools' },
  },
  {
    // Dismissible AND carrying a real action — the case stopPropagation
    // actually protects (a note with action: null never navigates anyway).
    id: 'vram-low',
    level: 'warn',
    title: 'Low VRAM',
    message: 'Close other GPU apps.',
    action: { label: 'Open Engines', type: 'settings-tab', target: 'engines' },
  },
];

vi.mock('../api/hooks', async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    useSystemLogs: () => ({ data: backendData, refetch: vi.fn() }),
    useTauriLogs: () => ({ data: null, refetch: vi.fn() }),
  };
});
vi.mock('../api/system', async (importOriginal) => ({
  // Partial: the footer's other chrome (engine quick switch, compute chip)
  // keeps whatever it imports from here without this file tracking it.
  ...(await importOriginal()),
  clearSystemLogs: vi.fn(),
  clearTauriLogs: vi.fn(),
  // The poll behind useNotifications — the filter under test runs REAL.
  systemNotifications: vi.fn(async () => ({ notifications: notificationData.current })),
}));
// NetworkToggle fetches /system/network/state on mount — out of scope here.
vi.mock('../components/NetworkToggle', () => ({ default: () => null }));

import LogsFooter from '../components/LogsFooter';
vi.mock('../components/EngineQuickSwitch', () => ({
  default: () => <button>Footer engine switcher</button>,
}));
import { useAppStore } from '../store';

function renderFooter() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LogsFooter />
    </QueryClientProvider>,
  );
}

function openNotificationsTab() {
  act(() => {
    window.dispatchEvent(new CustomEvent('omni:open-notifications'));
  });
}

const rowOf = (title) => screen.getByText(title).closest('div[class*="rounded-md"]');
const dismissBtnIn = (row) => row.querySelector('button[aria-label]');

beforeEach(() => {
  localStorage.clear();
  backendData.lines = [];
  notificationData.current = NOTES;
  useAppStore.setState({ dismissedNotificationIds: [] });
});

describe('LogsFooter notifications tab — dismissals', () => {
  it('does not duplicate the workspace engine switcher', () => {
    renderFooter();
    expect(
      screen.queryByRole('button', { name: 'Footer engine switcher' }),
    ).not.toBeInTheDocument();
  });
  it('does not claim all clear when raw logs contain an error', async () => {
    backendData.lines = ['ERROR failed to load the selected model'];
    notificationData.current = [];
    renderFooter();
    openNotificationsTab();

    expect(
      await screen.findByRole('button', { name: 'Backend logs, 1 errors' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('✅ All clear — no issues detected')).toBeNull();
  });

  it('shows both notes; only the info note offers a dismiss button', async () => {
    renderFooter();
    openNotificationsTab();

    await screen.findByText('Running on CPU');
    expect(dismissBtnIn(rowOf('Running on CPU'))).not.toBeNull();
    expect(dismissBtnIn(rowOf('Media engine unavailable'))).toBeNull();
  });

  it('dismissing the info note hides it and records the id', async () => {
    renderFooter();
    openNotificationsTab();

    await screen.findByText('Running on CPU');
    fireEvent.click(dismissBtnIn(rowOf('Running on CPU')));

    expect(screen.queryByText('Running on CPU')).toBeNull();
    expect(screen.getByText('Media engine unavailable')).not.toBeNull();
    expect(useAppStore.getState().dismissedNotificationIds).toEqual(['gpu-unavailable']);
  });

  it('dismiss does not fire the row action (stopPropagation)', async () => {
    // #1192 review: click ✕ on a note that HAS a real action — without
    // stopPropagation the row's own onClick would navigate.
    const openSettingsTab = vi.fn();
    useAppStore.setState({ openSettingsTab });
    renderFooter();
    openNotificationsTab();

    await screen.findByText('Low VRAM');
    fireEvent.click(dismissBtnIn(rowOf('Low VRAM')));
    expect(openSettingsTab).not.toHaveBeenCalled();
    expect(screen.queryByText('Low VRAM')).toBeNull();
  });
});
