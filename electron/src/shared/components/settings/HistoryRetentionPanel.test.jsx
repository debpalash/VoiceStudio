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

import HistoryRetentionPanel from './HistoryRetentionPanel';

describe('HistoryRetentionPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the current cap from GET state', async () => {
    global.fetch = mockFetchSequence({ status: 200, body: { cap: 200, default: 200 } });
    render(<HistoryRetentionPanel />);
    await waitFor(() => {
      expect(screen.getByTestId('history-retention-input')).toHaveValue(200);
    });
  });

  it('save PUTs the edited cap', async () => {
    const fetchMock = mockFetchSequence(
      { status: 200, body: { cap: 200, default: 200 } }, // initial GET
      { status: 200, body: { cap: 50, default: 200 } }, // PUT
    );
    global.fetch = fetchMock;

    render(<HistoryRetentionPanel />);
    await waitFor(() => screen.getByTestId('history-retention-input'));
    fireEvent.change(screen.getByTestId('history-retention-input'), { target: { value: '50' } });
    fireEvent.click(screen.getByTestId('history-retention-save'));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([_u, opts]) => opts && opts.method === 'PUT');
      expect(put).toBeTruthy();
      expect(put[0]).toMatch(/\/api\/settings\/history-retention$/);
      expect(JSON.parse(put[1].body)).toEqual({ cap: 50 });
    });
  });

  it('saves on Enter in the input', async () => {
    const fetchMock = mockFetchSequence(
      { status: 200, body: { cap: 200, default: 200 } }, // initial GET
      { status: 200, body: { cap: 75, default: 200 } }, // PUT
    );
    global.fetch = fetchMock;

    render(<HistoryRetentionPanel />);
    await waitFor(() => screen.getByTestId('history-retention-input'));
    const input = screen.getByTestId('history-retention-input');
    fireEvent.change(input, { target: { value: '75' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([_u, opts]) => opts && opts.method === 'PUT');
      expect(put).toBeTruthy();
      expect(JSON.parse(put[1].body)).toEqual({ cap: 75 });
    });
  });

  it('surfaces a load failure (500) and holds Save until a load succeeds', async () => {
    global.fetch = mockFetchSequence({ status: 500, body: { detail: 'db locked' } });
    render(<HistoryRetentionPanel />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent(/db locked/);
    expect(screen.getByTestId('history-retention-save')).toBeDisabled();
  });

  it('stays silent and usable on a 404 (backend older than the panel)', async () => {
    global.fetch = mockFetchSequence({ status: 404, body: { detail: 'Not Found' } });
    render(<HistoryRetentionPanel />);
    await waitFor(() => expect(screen.getByTestId('history-retention-save')).not.toBeDisabled());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    // The hardcoded default hint stays in place.
    expect(screen.getByTestId('history-retention-input')).toHaveAttribute('placeholder', '200');
  });

  it('rejects a negative cap client-side without a PUT', async () => {
    const fetchMock = mockFetchSequence({ status: 200, body: { cap: 200, default: 200 } });
    global.fetch = fetchMock;

    render(<HistoryRetentionPanel />);
    await waitFor(() => screen.getByTestId('history-retention-input'));
    fireEvent.change(screen.getByTestId('history-retention-input'), { target: { value: '-5' } });
    fireEvent.click(screen.getByTestId('history-retention-save'));

    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([_u, o]) => o && o.method === 'PUT')).toHaveLength(0);
    });
  });
});
