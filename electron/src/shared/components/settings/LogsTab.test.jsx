import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import LogsTab from './LogsTab';

const LINES = ['[10:00:00] boot\n', '[10:00:01] ready\n'];

function renderTab(overrides = {}) {
  const props = {
    logSource: 'backend',
    setLogSource: vi.fn(),
    logs: LINES,
    logMeta: { path: '/home/u/.omnivoice/omnivoice.log', exists: true },
    loadingLogs: false,
    refreshLogs: vi.fn(),
    onClearLogs: vi.fn(),
    ...overrides,
  };
  return { ...render(<LogsTab {...props} />), props };
}

describe('LogsTab', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('offers Open folder for on-disk logs and reveals via /export/reveal', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true }),
      text: async () => '{"success":true}',
    });
    global.fetch = fetchMock;

    renderTab();
    fireEvent.click(screen.getByTestId('logs-open-folder'));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => u.endsWith('/export/reveal'));
      expect(call).toBeTruthy();
      expect(JSON.parse(call[1].body)).toEqual({ path: '/home/u/.omnivoice/omnivoice.log' });
    });
  });

  it('hides Open folder for the in-memory frontend buffer and missing files', () => {
    renderTab({ logSource: 'frontend', logMeta: { path: 'in-memory (last 500)', exists: true } });
    expect(screen.queryByTestId('logs-open-folder')).not.toBeInTheDocument();

    renderTab({ logSource: 'tauri', logMeta: { path: '—', exists: false } });
    expect(screen.queryByTestId('logs-open-folder')).not.toBeInTheDocument();
  });

  it('copies the visible tail to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    renderTab();
    fireEvent.click(screen.getByTestId('logs-copy'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(LINES.join('')));

    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
  });

  it('disables Copy when there is nothing to copy', () => {
    renderTab({ logs: [] });
    expect(screen.getByTestId('logs-copy')).toBeDisabled();
  });

  it('log viewport is keyboard-reachable and labelled', () => {
    renderTab();
    const box = screen.getByTestId('logs-scroll');
    expect(box).toHaveAttribute('tabindex', '0');
    expect(box).toHaveAttribute('role', 'log');
    expect(box).toHaveAccessibleName('Logs');
    expect(box).toHaveClass('flex-1');
    expect(box).toHaveClass('overflow-auto');
    expect(box).not.toHaveClass('max-h-[280px]');
  });

  it('scrolls to the newest entries when logs load', () => {
    const { rerender, props } = renderTab({ logs: [] });
    const box = screen.getByTestId('logs-scroll');
    Object.defineProperty(box, 'scrollHeight', { value: 640, configurable: true });
    rerender(<LogsTab {...props} logs={LINES} />);
    expect(box.scrollTop).toBe(640);
  });
});
