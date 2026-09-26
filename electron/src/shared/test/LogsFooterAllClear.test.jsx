import React, { Profiler } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, QueryObserver } from '@tanstack/react-query';

const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), toasts) }));

const queries = vi.hoisted(() => ({
  backend: {},
  tauri: {},
  notifications: {},
  frontend: [],
}));
vi.mock('../utils/consoleBuffer', () => ({
  getFrontendLogs: () => queries.frontend,
  clearFrontendLogs: () => {
    queries.frontend = [];
  },
}));
vi.mock('../api/hooks', async (original) => ({
  ...(await original()),
  useSystemLogs: () => queries.backend,
  useTauriLogs: () => queries.tauri,
  useVisibleNotifications: () => queries.notifications,
}));
vi.mock('../api/system', async (original) => ({
  ...(await original()),
  clearSystemLogs: vi.fn(async () => {}),
  clearTauriLogs: vi.fn(async () => {}),
}));
vi.mock('../components/NetworkToggle', () => ({ default: () => null }));

import LogsFooter from '../components/LogsFooter';

const ALL_CLEAR = /All clear/;
function footer(onRender = () => {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => (
    <QueryClientProvider client={client}>
      <Profiler id="logs" onRender={onRender}>
        <LogsFooter />
      </Profiler>
    </QueryClientProvider>
  );
  const result = render(tree());
  act(() => window.dispatchEvent(new CustomEvent('omni:open-notifications')));
  return { ...result, update: () => result.rerender(tree()) };
}

beforeEach(() => {
  localStorage.clear();
  toasts.success.mockClear();
  toasts.error.mockClear();
  queries.frontend = [];
  for (const source of ['backend', 'tauri']) {
    queries[source] = { data: { lines: [] }, isSuccess: true, refetch: vi.fn(async () => {}) };
  }
  queries.notifications = { notifications: [], isSuccess: true };
});

describe('LogsFooter all-clear evidence', () => {
  it('shows all clear only after every source succeeds without warnings', () => {
    footer();
    expect(screen.getByText(ALL_CLEAR)).toBeInTheDocument();
  });

  it.each(['backend', 'tauri', 'notifications'])(
    'withholds all clear while %s is pending, then shows it on success',
    (source) => {
      queries[source].isSuccess = false;
      queries[source].isPending = true;
      const view = footer();
      expect(screen.queryByText(ALL_CLEAR)).toBeNull();
      queries[source] = { ...queries[source], isSuccess: true, isPending: false };
      view.update();
      expect(screen.getByText(ALL_CLEAR)).toBeInTheDocument();
    },
  );

  it.each(['backend', 'tauri', 'notifications'])(
    'withholds all clear when %s retrieval fails, even with cached empty data',
    (source) => {
      const view = footer();
      expect(screen.getByText(ALL_CLEAR)).toBeInTheDocument();
      queries[source] = { ...queries[source], isSuccess: false, isError: true };
      view.update();
      expect(screen.queryByText(ALL_CLEAR)).toBeNull();
    },
  );

  it.each(['backend', 'tauri'])(
    'never commits stale all-clear when %s gains a warning',
    (source) => {
      const commits = [];
      const view = footer(() => commits.push(Boolean(screen.queryByText(ALL_CLEAR))));
      expect(screen.getByText(ALL_CLEAR)).toBeInTheDocument();
      commits.length = 0;
      queries[source] = { ...queries[source], data: { lines: ['WARNING model unavailable'] } };
      view.update();
      expect(commits.length).toBeGreaterThan(0);
      expect(commits).not.toContain(true);
      expect(screen.queryByText(ALL_CLEAR)).toBeNull();
    },
  );

  it('reads frontend warnings on mount and pulls the cleared ring immediately', () => {
    queries.frontend = [{ t: 1, level: 'warn', msg: 'render warning' }];
    footer();
    expect(screen.queryByText(ALL_CLEAR)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Frontend logs/ }));
    fireEvent.click(screen.getByRole('button', { name: /Clear/i }));
    act(() => window.dispatchEvent(new CustomEvent('omni:open-notifications')));
    expect(screen.getByText(ALL_CLEAR)).toBeInTheDocument();
  });

  it.each(['backend', 'tauri'])(
    'refreshes the authoritative %s query after clearing',
    async (source) => {
      footer();
      fireEvent.click(
        screen.getByRole('button', {
          name: source === 'backend' ? /^Backend logs/ : /^Tauri logs/,
        }),
      );
      fireEvent.click(screen.getByRole('button', { name: /Clear/i }));
      await waitFor(() => expect(queries[source].refetch).toHaveBeenCalledOnce());
    },
  );
});

it.each(['backend', 'tauri'])(
  'reports refresh failure after clearing %s without claiming success',
  async (source) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const observer = new QueryObserver(client, {
      queryKey: ['failed-refresh', source],
      queryFn: async () => {
        throw new Error('log refresh unavailable');
      },
    });
    queries[source].refetch = (options) => observer.refetch(options);
    const view = footer();
    fireEvent.click(
      screen.getByRole('button', { name: source === 'backend' ? /^Backend logs/ : /^Tauri logs/ }),
    );
    fireEvent.click(screen.getByRole('button', { name: /Clear/i }));
    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith(expect.stringContaining('log refresh unavailable')),
    );
    expect(toasts.success).not.toHaveBeenCalled();
    queries[source] = { ...queries[source], isSuccess: false, isError: true };
    view.update();
    act(() => window.dispatchEvent(new CustomEvent('omni:open-notifications')));
    expect(screen.queryByText(ALL_CLEAR)).toBeNull();
    observer.destroy();
    client.clear();
  },
);
