import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import toast from 'react-hot-toast';

import StorageUsagePanel, { _resetCriticalToastForTests } from './StorageUsagePanel';

const REPORT = {
  generated_at: 1750000000,
  cached: false,
  min_free_gb: 10,
  volumes: [
    {
      path: '/',
      total_bytes: 1000 * 1024 ** 3,
      used_bytes: 400 * 1024 ** 3,
      free_bytes: 600 * 1024 ** 3,
      used_percent: 40.0,
      roots: ['data', 'hf_cache', 'engine_venvs', 'temp'],
    },
  ],
  categories: [
    {
      id: 'hf_cache',
      path: '/home/u/.cache/huggingface',
      exists: true,
      bytes: 12 * 1024 ** 3,
      complete: true,
      items: [
        { name: 'Org/BigModel', bytes: 8 * 1024 ** 3 },
        { name: 'Org/SmallModel', bytes: 4 * 1024 ** 3 },
      ],
    },
    {
      id: 'data',
      path: '/home/u/.omnivoice',
      exists: true,
      bytes: 3 * 1024 ** 3,
      complete: true,
      children: [
        { id: 'voices', path: '/home/u/.omnivoice/voices', bytes: 1024 ** 3, complete: true },
        { id: 'outputs', path: '/home/u/.omnivoice/outputs', bytes: 2 * 1024 ** 3, complete: true },
        { id: 'logs', path: '/home/u/.omnivoice', bytes: 1024 ** 2, complete: true },
      ],
    },
    {
      id: 'engine_venvs',
      path: '/app/backend/engines',
      exists: true,
      bytes: 5 * 1024 ** 3,
      complete: true,
      items: [{ name: 'indextts', bytes: 5 * 1024 ** 3 }],
    },
    { id: 'temp', path: '/tmp', exists: true, bytes: 0, complete: true, items: [] },
  ],
  warnings: [],
};

const CRITICAL_WARNING = {
  kind: 'low_disk',
  severity: 'critical',
  path: '/',
  free_gb: 4.2,
  min_free_gb: 10,
  roots: ['data', 'hf_cache'],
};

function mockFetchWith(body) {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
  global.fetch = fn;
  return fn;
}

describe('StorageUsagePanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    _resetCriticalToastForTests();
  });

  it('renders every category with human sizes, top models, and the disk gauge', async () => {
    mockFetchWith(REPORT);
    render(<StorageUsagePanel />);

    await waitFor(() => expect(screen.getByTestId('storage-cat-hf_cache')).toBeInTheDocument());
    expect(screen.getByTestId('storage-cat-data')).toBeInTheDocument();
    expect(screen.getByTestId('storage-cat-engine_venvs')).toBeInTheDocument();
    expect(screen.getByTestId('storage-cat-temp')).toBeInTheDocument();
    expect(screen.getByTestId('storage-category-scroll')).toHaveClass('overflow-y-auto');
    expect(screen.getByTestId('storage-category-scroll')).toHaveClass('overscroll-contain');

    // Human sizes from fmtBytes
    expect(screen.getByText('12.00 GB')).toBeInTheDocument();
    // Top-models sublist under the HF cache row
    expect(screen.getByText('Org/BigModel')).toBeInTheDocument();
    // Data child subtotals
    expect(screen.getByText('Voices')).toBeInTheDocument();
    expect(screen.getByText('Outputs')).toBeInTheDocument();
    // Disk gauge for the data volume
    expect(screen.getByTestId('storage-disk-gauge')).toBeInTheDocument();
    expect(screen.getByText(/600\.00 GB free of 1000\.00 GB/)).toBeInTheDocument();
    // Reclaim + housekeeping actions
    expect(screen.getByTestId('storage-manage-models')).toBeInTheDocument();
    expect(screen.getByTestId('storage-clear-logs')).toBeInTheDocument();
    expect(screen.getByTestId('storage-open-data')).toBeInTheDocument();
    // No warnings → no banner
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a critical warning banner and fires the session toast exactly once', async () => {
    const errSpy = vi.spyOn(toast, 'error');
    mockFetchWith({ ...REPORT, warnings: [CRITICAL_WARNING] });

    const first = render(<StorageUsagePanel />);
    await waitFor(() => expect(screen.getByTestId('storage-warning-low_disk')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent(/Critically low disk space/);
    expect(screen.getByRole('alert')).toHaveTextContent(/4\.2 GB/);
    await waitFor(() => expect(errSpy).toHaveBeenCalledTimes(1));

    // Re-mounting (user leaves + reopens Settings) must NOT toast again.
    first.unmount();
    render(<StorageUsagePanel />);
    await waitFor(() => expect(screen.getByTestId('storage-warning-low_disk')).toBeInTheDocument());
    expect(errSpy).toHaveBeenCalledTimes(1);
  });

  it('renders a partial-scan note and warn banner for unreadable warnings', async () => {
    const report = {
      ...REPORT,
      categories: REPORT.categories.map((c) =>
        c.id === 'hf_cache' ? { ...c, complete: false } : c,
      ),
      warnings: [
        {
          kind: 'unreadable',
          severity: 'warning',
          category_id: 'hf_cache',
          path: '/home/u/.cache/huggingface',
          reason: 'timeout',
        },
      ],
    };
    mockFetchWith(report);
    render(<StorageUsagePanel />);
    await waitFor(() =>
      expect(screen.getByTestId('storage-warning-unreadable')).toBeInTheDocument(),
    );
    expect(screen.getByText(/partial — scan timed out/)).toBeInTheDocument();
  });

  it('Refresh hits the endpoint with refresh=1', async () => {
    const fetchMock = mockFetchWith(REPORT);
    render(<StorageUsagePanel />);
    await waitFor(() => expect(screen.getByTestId('storage-refresh')).not.toBeDisabled());
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/settings\/storage$/);

    fireEvent.click(screen.getByTestId('storage-refresh'));
    await waitFor(() => {
      const refreshed = fetchMock.mock.calls.find(([u]) => /refresh=1/.test(u));
      expect(refreshed).toBeTruthy();
      expect(refreshed[0]).toMatch(/\/api\/settings\/storage\?refresh=1$/);
    });
  });

  it('Clear logs is confirm-gated: declining leaves the logs untouched', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const fetchMock = mockFetchWith(REPORT);
    render(<StorageUsagePanel />);
    await waitFor(() => expect(screen.getByTestId('storage-clear-logs')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('storage-clear-logs'));

    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    expect(confirmSpy.mock.calls[0][0]).toMatch(/cannot be undone/i);
    expect(fetchMock.mock.calls.filter(([u]) => /\/system\/logs\/clear/.test(u))).toHaveLength(0);
  });

  it('Clear logs POSTs after the user confirms', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const fetchMock = mockFetchWith(REPORT);
    render(<StorageUsagePanel />);
    await waitFor(() => expect(screen.getByTestId('storage-clear-logs')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('storage-clear-logs'));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => /\/system\/logs\/clear/.test(u));
      expect(call).toBeTruthy();
      expect(call[1]?.method).toBe('POST');
    });
  });

  it('Clear temp files is confirm-gated and hits the temp-clear endpoint', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const report = {
      ...REPORT,
      categories: REPORT.categories.map((c) =>
        c.id === 'temp' ? { ...c, bytes: 512 * 1024 ** 2 } : c,
      ),
    };
    const fetchMock = mockFetchWith(report);
    render(<StorageUsagePanel />);
    await waitFor(() => expect(screen.getByTestId('storage-clear-temp')).toBeInTheDocument());
    expect(screen.getByTestId('storage-clear-temp')).not.toBeDisabled();

    fireEvent.click(screen.getByTestId('storage-clear-temp'));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) =>
        u.endsWith('/api/settings/storage/temp/clear'),
      );
      expect(call).toBeTruthy();
      expect(call[1]?.method).toBe('POST');
    });
  });

  it('Clear temp files is disabled when there is nothing to reclaim', async () => {
    mockFetchWith(REPORT); // REPORT's temp category is 0 bytes
    render(<StorageUsagePanel />);
    await waitFor(() => expect(screen.getByTestId('storage-clear-temp')).toBeInTheDocument());
    expect(screen.getByTestId('storage-clear-temp')).toBeDisabled();
  });

  it('shows the load error state when the endpoint fails', async () => {
    // An HTTP error (not a transport failure) — apiFetch surfaces it without retrying.
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => ({ detail: 'Failed to compute storage report' }),
      text: async () => JSON.stringify({ detail: 'Failed to compute storage report' }),
    });
    render(<StorageUsagePanel />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent(/Failed to compute storage report/);
  });
});
