/**
 * The join box is the half of enrollment that had no UI: before it, becoming a
 * worker meant OMNIVOICE_WORKER_MODE + OMNIVOICE_WORKER_TOKEN in the
 * environment and a relaunch. These pin what makes it usable — the code
 * reaching the API as a real JSON body, the two states (never joined vs joined
 * and stopped) offering different next actions, and the failure the user
 * actually hits (an expired code) staying on screen.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }));

import JoinWorkerPanel from './JoinWorkerPanel';

const STATUS = {
  worker_mode: false,
  running: false,
  enrolled: false,
  endpoint: '',
  last_error: '',
  env_pinned: false,
};

function renderPanel(status = STATUS, request = vi.fn().mockResolvedValue(status)) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <JoinWorkerPanel request={request} />
    </QueryClientProvider>,
  );
  return { ...result, request };
}

beforeEach(() => vi.clearAllMocks());

describe('JoinWorkerPanel', () => {
  it('sends the pasted code to the join endpoint', async () => {
    const request = vi.fn().mockResolvedValue(STATUS);
    renderPanel(STATUS, request);

    fireEvent.change(await screen.findByLabelText('Join code'), {
      target: { value: '  ovw_abc123  ' },
    });
    fireEvent.click(screen.getByText('Join'));

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith('/workers/agent/join', {
        method: 'POST',
        // Trimmed: a code copied out of a terminal carries whitespace, and the
        // server rejects it verbatim.
        body: { token: 'ovw_abc123' },
      }),
    );
  });

  it('will not post an empty code', async () => {
    const request = vi.fn().mockResolvedValue(STATUS);
    renderPanel(STATUS, request);

    await screen.findByLabelText('Join code');
    expect(screen.getByText('Join').closest('button')).toBeDisabled();
  });

  it('offers a switch, not another code, once the machine has joined', async () => {
    const request = vi.fn().mockResolvedValue({
      ...STATUS,
      enrolled: true,
      running: true,
      endpoint: 'studio-mac:7443',
    });
    renderPanel(undefined, request);

    // The pinned certificate survives a stop, so resuming asks for nothing.
    expect(await screen.findByText('Take work from')).toBeInTheDocument();
    expect(screen.getByText('studio-mac:7443')).toBeInTheDocument();
    expect(screen.getByText('Working')).toBeInTheDocument();
  });

  it('starts and stops taking work through the agent endpoint', async () => {
    const request = vi
      .fn()
      .mockResolvedValue({ ...STATUS, enrolled: true, running: true, endpoint: 'mac:7443' });
    renderPanel(undefined, request);

    fireEvent.click(await screen.findByRole('switch'));

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith('/workers/agent/enabled', {
        method: 'POST',
        body: { enabled: false },
      }),
    );
  });

  it('keeps the last join failure on screen instead of only in a toast', async () => {
    const request = vi.fn().mockResolvedValue({
      ...STATUS,
      last_error: 'This enrollment token has expired. Generate a new one.',
    });
    renderPanel(undefined, request);

    expect(await screen.findByRole('alert')).toHaveTextContent(/expired/i);
  });

  it('does not offer a toggle it cannot honour when the environment pins worker mode', async () => {
    const request = vi
      .fn()
      .mockResolvedValue({ ...STATUS, enrolled: true, running: true, env_pinned: true });
    renderPanel(undefined, request);

    expect(await screen.findByRole('switch')).toBeDisabled();
    expect(screen.getByText(/OMNIVOICE_WORKER_MODE/)).toBeInTheDocument();
  });
});
