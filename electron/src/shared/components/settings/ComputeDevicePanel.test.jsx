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

import ComputeDevicePanel from './ComputeDevicePanel';

const CUDA_HOST = {
  value: 'auto',
  applied: 'auto',
  restart_required: false,
  effective_family: 'cuda',
  auto_family: 'cuda',
  available_families: ['cuda', 'cpu'],
  env_pinned: false,
  choices: ['auto', 'cuda', 'rocm', 'xpu', 'mps', 'cpu'],
};

describe('ComputeDevicePanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('offers only the detected families plus Auto', async () => {
    global.fetch = mockFetchSequence({ status: 200, body: CUDA_HOST });
    render(<ComputeDevicePanel />);
    // Wait for the LOADED state (3 options), not just the select — it
    // renders disabled with only Auto before the GET resolves.
    await waitFor(() => expect(screen.getByTestId('compute-device-select').options.length).toBe(3));
    const options = [...screen.getByTestId('compute-device-select').options].map((o) => o.value);
    // No mps/rocm/xpu on a CUDA host — an override can steer, not invent.
    expect(options).toEqual(['auto', 'cuda', 'cpu']);
  });

  it('changing the pick PUTs the value and shows the restart note', async () => {
    const fetchMock = mockFetchSequence(
      { status: 200, body: CUDA_HOST }, // initial GET
      {
        status: 200,
        body: { ...CUDA_HOST, value: 'cpu', restart_required: true },
      }, // PUT echo
    );
    global.fetch = fetchMock;
    render(<ComputeDevicePanel />);
    await waitFor(() => expect(screen.getByTestId('compute-device-select').options.length).toBe(3));

    fireEvent.change(screen.getByTestId('compute-device-select'), { target: { value: 'cpu' } });

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([_u, opts]) => opts && opts.method === 'PUT');
      expect(put).toBeTruthy();
      expect(put[0]).toMatch(/\/api\/settings\/compute-device$/);
      expect(JSON.parse(put[1].body)).toEqual({ value: 'cpu' });
    });
    expect(screen.getByText(/after the app restarts/i)).toBeInTheDocument();
  });

  it('an OMNIVOICE_DEVICE pin disables the control and says so', async () => {
    global.fetch = mockFetchSequence({
      status: 200,
      body: { ...CUDA_HOST, value: 'cpu', applied: 'cpu', env_pinned: true },
    });
    render(<ComputeDevicePanel />);
    await waitFor(() => {
      expect(screen.getByTestId('compute-device-select')).toBeDisabled();
    });
    expect(screen.getByText(/OMNIVOICE_DEVICE/)).toBeInTheDocument();
  });

  it('re-syncs from the server when the PUT fails, keeping the error visible', async () => {
    const fetchMock = mockFetchSequence(
      { status: 200, body: CUDA_HOST }, // initial GET
      { status: 500, body: { detail: 'nope' } }, // PUT fails
      { status: 200, body: CUDA_HOST }, // re-sync GET
    );
    global.fetch = fetchMock;
    render(<ComputeDevicePanel />);
    await waitFor(() => expect(screen.getByTestId('compute-device-select').options.length).toBe(3));

    fireEvent.change(screen.getByTestId('compute-device-select'), { target: { value: 'cpu' } });

    await waitFor(() => {
      // Three calls: GET, failed PUT, re-sync GET — the select ends on the
      // server's truth (auto), never a pick that didn't persist.
      expect(fetchMock.mock.calls.length).toBe(3);
    });
    expect(screen.getByTestId('compute-device-select')).toHaveValue('auto');
    // The save error must survive the re-sync — a silent snap-back reads
    // as "the app ignored me".
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});
