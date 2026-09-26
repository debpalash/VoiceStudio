import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

function mockFetchSequence(...responses) {
  const fn = vi.fn();
  for (const r of responses) {
    fn.mockResolvedValueOnce({
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => r.body,
      text: async () => JSON.stringify(r.body),
    });
  }
  return fn;
}

import PerformancePanel from './PerformancePanel';

describe('PerformancePanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the toggle unchecked from GET state (Windows)', async () => {
    global.fetch = mockFetchSequence({
      status: 200,
      body: { enabled: false, platform: 'win32' },
    });
    render(<PerformancePanel />);
    await waitFor(() => {
      const toggle = screen.getByTestId('torch-compile-toggle');
      expect(toggle).not.toBeChecked();
      expect(toggle).not.toBeDisabled();
    });
  });

  it('toggle click PUTs the new state', async () => {
    const fetchMock = mockFetchSequence(
      { status: 200, body: { enabled: false, platform: 'win32' } }, // initial GET
      { status: 200, body: { enabled: true, platform: 'win32' } }, // PUT
    );
    global.fetch = fetchMock;

    render(<PerformancePanel />);
    await waitFor(() => screen.getByTestId('torch-compile-toggle'));
    const toggle = screen.getByTestId('torch-compile-toggle');
    fireEvent.click(toggle);

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([_u, opts]) => opts && opts.method === 'PUT');
      expect(put).toBeTruthy();
      expect(put[0]).toMatch(/\/api\/settings\/perf\/torch-compile-disabled$/);
      expect(JSON.parse(put[1].body)).toEqual({ enabled: true });
    });
  });

  it('allows eager fallback on macOS', async () => {
    global.fetch = mockFetchSequence({
      status: 200,
      body: { enabled: false, platform: 'darwin' },
    });
    render(<PerformancePanel />);
    await waitFor(() => {
      const toggle = screen.getByTestId('torch-compile-toggle');
      expect(toggle).not.toBeDisabled();
    });
    expect(screen.queryByText(/not needed on this platform/i)).not.toBeInTheDocument();
  });

  it('renders every user-facing string through i18n (en fallback)', async () => {
    global.fetch = mockFetchSequence({
      status: 200,
      body: { enabled: false, platform: 'win32' },
    });
    render(<PerformancePanel />);
    await waitFor(() => screen.getByTestId('torch-compile-toggle'));
    // Section title + row label resolve from settings.perf_* keys.
    expect(screen.getByText('Performance')).toBeInTheDocument();
    expect(screen.getByText(/Disable torch\.compile/)).toBeInTheDocument();
    expect(screen.getByText(/Uses eager execution/)).toBeInTheDocument();
    expect(screen.getByTestId('torch-compile-toggle')).toHaveAccessibleName(
      /Disable torch\.compile/,
    );
  });

  it('surfaces a translated load error when the GET fails', async () => {
    global.fetch = mockFetchSequence({ status: 500, body: { detail: 'boom' } });
    render(<PerformancePanel />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent(/boom|Failed to load/i);
  });

  it('allows eager fallback on Linux', async () => {
    global.fetch = mockFetchSequence({
      status: 200,
      body: { enabled: false, platform: 'linux' },
    });
    render(<PerformancePanel />);
    await waitFor(() => {
      const toggle = screen.getByTestId('torch-compile-toggle');
      expect(toggle).not.toBeDisabled();
    });
  });

  it('renders pre-enabled when backend reports enabled=true', async () => {
    global.fetch = mockFetchSequence({
      status: 200,
      body: { enabled: true, platform: 'win32' },
    });
    render(<PerformancePanel />);
    await waitFor(() => {
      const toggle = screen.getByTestId('torch-compile-toggle');
      expect(toggle).toBeChecked();
    });
  });
});
