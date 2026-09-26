import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// Keep toast side-channels out of the test (timers, portals).
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('../../api/hooks', () => ({
  useSystemInfo: vi.fn(),
  queryKeys: { systemInfo: ['system-info'] },
}));

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock('../../api/client', () => ({
  apiFetch: vi.fn().mockResolvedValue({}),
}));

const { openSettingsTab } = vi.hoisted(() => ({ openSettingsTab: vi.fn() }));
vi.mock('../../store', () => ({
  useAppStore: (selector) => selector({ openSettingsTab }),
}));

import { toast } from 'react-hot-toast';
import { useSystemInfo } from '../../api/hooks';
import { apiFetch } from '../../api/client';
import NetworkTab from './NetworkTab';

describe('NetworkTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiFetch.mockResolvedValue({});
  });

  it('offers Clear for a proxy persisted in a previous session (after reload)', async () => {
    // Fresh mount, nothing saved this session — the persisted proxy comes
    // from the backend. The Clear affordance must NOT depend on having just
    // clicked Save in the current session.
    useSystemInfo.mockReturnValue({ data: { proxy_url: 'http://127.0.0.1:7890' } });

    render(<NetworkTab />);

    // Input is prefilled from the persisted value, the "Set" badge shows,
    // and Clear is available immediately.
    expect(screen.getByLabelText('Proxy URL')).toHaveValue('http://127.0.0.1:7890');
    expect(screen.getByText('✓ Set')).toBeInTheDocument();
    const clear = screen.getByTestId('proxy-clear');

    fireEvent.click(clear);

    await waitFor(() => {
      // All six proxy env vars are cleared on the backend.
      const clearedKeys = apiFetch.mock.calls
        .filter(([path]) => path === '/system/set-env')
        .map(([, opts]) => JSON.parse(opts.body))
        .filter((b) => b.value === '')
        .map((b) => b.key)
        .sort();
      expect(clearedKeys).toEqual([
        'ALL_PROXY',
        'HTTPS_PROXY',
        'HTTP_PROXY',
        'all_proxy',
        'http_proxy',
        'https_proxy',
      ]);
    });

    // The UI reflects the cleared state without waiting for a refetch.
    await waitFor(() => {
      expect(screen.queryByTestId('proxy-clear')).not.toBeInTheDocument();
    });
    expect(screen.getByLabelText('Proxy URL')).toHaveValue('');
    expect(screen.queryByText('✓ Set')).not.toBeInTheDocument();
  });

  it('hides Clear when no proxy is configured', () => {
    useSystemInfo.mockReturnValue({ data: { proxy_url: '' } });
    render(<NetworkTab />);
    expect(screen.queryByTestId('proxy-clear')).not.toBeInTheDocument();
    expect(screen.queryByText('✓ Set')).not.toBeInTheDocument();
  });

  it('shows Clear (and the badge) right after saving in this session', async () => {
    useSystemInfo.mockReturnValue({ data: { proxy_url: '' } });
    render(<NetworkTab />);

    fireEvent.change(screen.getByLabelText('Proxy URL'), {
      target: { value: 'socks5://127.0.0.1:7890' },
    });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(screen.getByTestId('proxy-clear')).toBeInTheDocument();
    expect(screen.getByText('✓ Set')).toBeInTheDocument();
  });

  it('labels the proxy input for assistive tech', () => {
    useSystemInfo.mockReturnValue({ data: {} });
    render(<NetworkTab />);
    expect(screen.getByLabelText('Proxy URL')).toBeInTheDocument();
  });

  it('has NO FFmpeg path control anymore — only the pointer to Audio tools', () => {
    // The override moved to Settings → Audio tools; a second writer of
    // env.FFMPEG_PATH here would fight the new panel.
    useSystemInfo.mockReturnValue({ data: { ffmpeg_ok: true } });
    render(<NetworkTab />);
    expect(screen.queryByLabelText('FFmpeg path')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('open-audio-tools'));
    expect(openSettingsTab).toHaveBeenCalledWith('audio-tools');
  });
});
