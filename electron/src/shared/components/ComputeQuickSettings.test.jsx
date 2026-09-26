/**
 * The footer compute chip. Two rules it must not break: it stays invisible for
 * a user who never opted into remote workers (the status bar is 28px and every
 * permanent chip in it is a tax on everyone), and it reports the RESOLVED
 * target rather than the stored one — a green dot beside "Local" while the
 * machine you picked is asleep is the exact lie the resolved-answer rule
 * exists to prevent.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }));
vi.mock('qrcode', () => ({
  default: { toDataURL: vi.fn().mockResolvedValue('data:image/png,QR') },
}));

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('../api/client', () => ({ apiFetch }));

import ComputeQuickSettings from './ComputeQuickSettings';
import { useAppStore } from '../store';

const respond = (body, { ok = true, status = 200 } = {}) => ({
  ok,
  status,
  json: async () => body,
});

const SNAPSHOT = {
  enabled: true,
  running: true,
  workers: [{ id: 'w1', name: 'Desktop 4090', enabled: true, connected: true, breakers: [] }],
};
const TARGET = {
  target: 'w1',
  active: { remote: true, worker_id: 'w1', label: 'Desktop 4090' },
  targets: [
    { id: 'local', label: 'Local', is_local: true, status: 'ready', available: true },
    { id: 'w1', label: 'Desktop 4090', status: 'ready', available: true, active_tasks: 0 },
  ],
};

function route(overrides = {}) {
  const table = { '/workers': SNAPSHOT, '/workers/target': TARGET, ...overrides };
  apiFetch.mockImplementation((path) => Promise.resolve(respond(table[path] ?? {})));
}

function renderChip() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ComputeQuickSettings />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('ComputeQuickSettings', () => {
  it('renders nothing for a user who never opted in', async () => {
    route({ '/workers': { enabled: false, running: false, workers: [] } });
    const { container } = renderChip();

    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('stays available once a machine is enrolled, even with the feature off', async () => {
    route({ '/workers': { ...SNAPSHOT, enabled: false, running: false } });
    renderChip();

    // Otherwise turning it back on would mean a trip to Settings — which is
    // what this control exists to avoid.
    expect(await screen.findByRole('button', { name: /Compute/ })).toBeInTheDocument();
  });

  it('shows the machine work is actually running on', async () => {
    route();
    renderChip();
    expect(await screen.findByText('Desktop 4090')).toBeInTheDocument();
  });

  it('reports Local — in amber — when the chosen worker could not take the work', async () => {
    route({
      '/workers/target': {
        ...TARGET,
        active: { remote: false, reason: 'Desktop 4090 is offline' },
      },
    });
    renderChip();

    const label = await screen.findByText('Local');
    // The themed warn token, not a palette-fixed `amber` class.
    expect(label.className).toContain('--color-warn');
  });

  it('switches target through the API', async () => {
    route();
    renderChip();

    fireEvent.click(await screen.findByRole('button', { name: /Compute/ }));
    fireEvent.click(await screen.findByText('Local'));

    await waitFor(() =>
      expect(
        apiFetch.mock.calls.some(
          ([p, o]) => p === '/workers/target' && o?.body === JSON.stringify({ target: 'local' }),
        ),
      ).toBe(true),
    );
  });

  it('mints a join code with a QR without leaving the workspace', async () => {
    route({ '/workers/enrollments': { token: 'ovw_footer', expires_at: 0 } });
    renderChip();

    fireEvent.click(await screen.findByRole('button', { name: /Compute/ }));
    fireEvent.click(await screen.findByText('Add a machine'));

    expect(await screen.findByText('ovw_footer')).toBeInTheDocument();
  });

  it('turns the whole feature off from the bar, no trip to Settings', async () => {
    route();
    renderChip();

    fireEvent.click(await screen.findByRole('button', { name: /Compute/ }));
    fireEvent.click(await screen.findByRole('switch'));

    await waitFor(() =>
      expect(
        apiFetch.mock.calls.some(
          ([p, o]) => p === '/workers/enabled' && o?.body === JSON.stringify({ enabled: false }),
        ),
      ).toBe(true),
    );
  });

  it('opens the full panel for anything it deliberately does not carry', async () => {
    route();
    const openSettingsTab = vi.fn();
    useAppStore.setState({ openSettingsTab });
    renderChip();

    fireEvent.click(await screen.findByRole('button', { name: /Compute/ }));
    fireEvent.click(await screen.findByText('Remote worker settings'));

    expect(openSettingsTab).toHaveBeenCalledWith('workers');
  });
});
