import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import StorageTab from './StorageTab';

const INFO = {
  data_dir: '/home/u/.omnivoice',
  outputs_dir: '/home/u/.omnivoice/outputs',
  crash_log_path: '/home/u/.omnivoice/crash_log.txt',
};

/** URL-aware fetch mock: /system/info + history-retention + export/reveal. */
function mockFetch() {
  const fn = vi.fn(async (url) => {
    const body = /\/system\/info/.test(url)
      ? INFO
      : /history-retention/.test(url)
        ? { cap: 200, default: 200 }
        : { success: true };
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  });
  global.fetch = fn;
  return fn;
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <StorageTab />
    </QueryClientProvider>,
  );
}

describe('StorageTab', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('labels the data dir as app data (not uploads) and offers Open folder on every path', async () => {
    mockFetch();
    renderTab();
    await waitFor(() => expect(screen.getByText(`${INFO.data_dir}/`)).toBeInTheDocument());
    expect(screen.getByText('App data stored at')).toBeInTheDocument();
    expect(screen.getByTestId('storage-open-data-dir')).toBeInTheDocument();
    expect(screen.getByTestId('storage-open-outputs-dir')).toBeInTheDocument();
    expect(screen.getByTestId('storage-open-crash-log')).toBeInTheDocument();
  });

  it('Open folder reveals the path via /export/reveal', async () => {
    const fetchMock = mockFetch();
    renderTab();
    const btn = await screen.findByTestId('storage-open-outputs-dir');
    fireEvent.click(btn);
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => u.endsWith('/export/reveal'));
      expect(call).toBeTruthy();
      expect(JSON.parse(call[1].body)).toEqual({ path: INFO.outputs_dir });
    });
  });

  it('factory reset clears every registered preference key, not just the zustand blob', async () => {
    mockFetch();
    // Preferences scattered across the app (the pre-registry bug left these behind):
    localStorage.setItem('omnivoice.app', '{"state":{}}');
    localStorage.setItem('omnivoice.navRailSide', 'right');
    localStorage.setItem('omnivoice.settings.category', 'storage');
    localStorage.setItem('omni_capture_live_typing', '1');
    localStorage.setItem('ov_stories_global_speed', '1.4');
    localStorage.setItem('omni_ui', '{"uiScale":1.2}');
    localStorage.setItem('dismissed_lang_suggestion', 'true');
    // User data + connection state that must survive a reset:
    localStorage.setItem('omni_transcriptions', '[{"text":"note"}]');
    localStorage.setItem('ov_backend_url', 'http://192.168.1.4:7842');

    renderTab();
    await waitFor(() => expect(screen.getByTestId('factory-reset-open')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('factory-reset-open'));
    await waitFor(() => expect(screen.getByTestId('factory-reset-confirm')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('factory-reset-confirm'));

    await waitFor(() => expect(localStorage.getItem('omnivoice.app')).toBeNull());
    expect(localStorage.getItem('omnivoice.navRailSide')).toBeNull();
    expect(localStorage.getItem('omnivoice.settings.category')).toBeNull();
    expect(localStorage.getItem('omni_capture_live_typing')).toBeNull();
    expect(localStorage.getItem('ov_stories_global_speed')).toBeNull();
    expect(localStorage.getItem('omni_ui')).toBeNull();
    expect(localStorage.getItem('dismissed_lang_suggestion')).toBeNull();
    // Never touch user data or the remote-backend connection:
    expect(localStorage.getItem('omni_transcriptions')).toBe('[{"text":"note"}]');
    expect(localStorage.getItem('ov_backend_url')).toBe('http://192.168.1.4:7842');
  });
});
