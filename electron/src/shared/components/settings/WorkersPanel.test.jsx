import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('../../api/client', () => ({ apiFetch }));

/**
 * apiFetch resolves to a raw Response — it does not parse JSON and does not
 * throw on 4xx. Mocking it as if it returned parsed data is what let the panel
 * ship calling it wrong: the tests agreed with the mock, not with the client.
 */
const respond = (body, { ok = true, status = 200 } = {}) => ({
  ok,
  status,
  json: async () => body,
});
const respondWith = (fn) => apiFetch.mockImplementation((...args) => Promise.resolve(fn(...args)));

const { askConfirm } = vi.hoisted(() => ({ askConfirm: vi.fn() }));
vi.mock('../../utils/dialog', () => ({ askConfirm }));

import toast from 'react-hot-toast';
import WorkersPanel, { WorkerRow } from './WorkersPanel';

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkersPanel />
    </QueryClientProvider>,
  );
}

const WORKER = {
  id: 'w1',
  name: 'Desktop 4090',
  enabled: true,
  connected: true,
  consent_granted: true,
  active_tasks: 1,
  available_slots: 1,
  breakers: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  askConfirm.mockResolvedValue(true);
});

describe('WorkersPanel', () => {
  it('shows nothing beyond the toggle while the feature is off', async () => {
    apiFetch.mockResolvedValue(respond({ enabled: false, running: false, workers: [] }));
    renderPanel();

    await waitFor(() => expect(apiFetch.mock.calls[0][0]).toBe('/workers'));
    // The endpoint, the token button, and the worker list are all consequences
    // of enabling — an off feature must not advertise its surface.
    expect(screen.queryByText(/Generate token/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Workers connect to/i)).not.toBeInTheDocument();
  });

  it('reveals the endpoint and add flow once enabled', async () => {
    apiFetch.mockResolvedValue(
      respond({ enabled: true, running: true, endpoint: 'my-mac:7443', workers: [] }),
    );
    renderPanel();

    expect(await screen.findByText('my-mac:7443')).toBeInTheDocument();
    expect(screen.getByText(/Generate token/i)).toBeInTheDocument();
    expect(screen.getByText(/No workers yet/i)).toBeInTheDocument();
  });

  it('explains an occupied control-plane port without exposing inactive controls', async () => {
    apiFetch.mockResolvedValue(
      respond({
        enabled: true,
        running: false,
        startup_error: 'CONTROL_PLANE_PORT_IN_USE',
        workers: [],
      }),
    );
    renderPanel();

    expect(await screen.findByRole('alert')).toHaveTextContent(/another VoiceStudio instance/i);
    expect(screen.queryByText(/Generate token/i)).not.toBeInTheDocument();
  });

  it('shows the token once, with its shown-once warning', async () => {
    respondWith((path) =>
      path === '/workers'
        ? respond({ enabled: true, running: true, workers: [] })
        : respond({ token: 'ovw_abc123', expires_at: 1 }),
    );
    renderPanel();

    fireEvent.click(await screen.findByText(/Generate token/i));

    expect(await screen.findByText('ovw_abc123')).toBeInTheDocument();
    expect(screen.getByText(/shown only once/i)).toBeInTheDocument();
  });

  it('confirms before removing, because removal revokes the key', async () => {
    apiFetch.mockResolvedValue(respond({ enabled: true, running: true, workers: [WORKER] }));
    renderPanel();

    fireEvent.click(await screen.findByText(/Remove/i));

    await waitFor(() => expect(askConfirm).toHaveBeenCalled());
    expect(askConfirm.mock.calls[0][0]).toMatch(/revoked/i);
    await waitFor(() =>
      expect(
        apiFetch.mock.calls.some(([p, o]) => p === '/workers/w1' && o?.method === 'DELETE'),
      ).toBe(true),
    );
  });

  it('does not remove when the confirmation is declined', async () => {
    askConfirm.mockResolvedValue(false);
    apiFetch.mockResolvedValue(respond({ enabled: true, running: true, workers: [WORKER] }));
    renderPanel();

    fireEvent.click(await screen.findByText(/Remove/i));

    await waitFor(() => expect(askConfirm).toHaveBeenCalled());
    expect(apiFetch.mock.calls.some(([, o]) => o?.method === 'DELETE')).toBe(false);
  });

  // ── The calls themselves ────────────────────────────────────────────────
  //
  // apiFetch sets no Content-Type and does not parse JSON. These assert the
  // wire shape rather than "a call happened", because the panel shipped a 422
  // by sending a JSON string with no content type — which a was-it-called
  // assertion cannot see.

  const jsonCall = (path) => apiFetch.mock.calls.find(([p]) => p === path)?.[1] || {};

  it('sends the enable toggle as real JSON', async () => {
    respondWith(() => respond({ enabled: false, running: false, workers: [] }));
    renderPanel();

    fireEvent.click(await screen.findByRole('switch'));

    await waitFor(() => expect(jsonCall('/workers/enabled').method).toBe('POST'));
    const opts = jsonCall('/workers/enabled');
    expect(opts.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(opts.body)).toEqual({ enabled: true });
  });

  it('sends the enrollment request as real JSON', async () => {
    respondWith((path) =>
      path === '/workers'
        ? respond({ enabled: true, running: true, workers: [] })
        : respond({ token: 'ovw_x' }),
    );
    renderPanel();

    fireEvent.click(await screen.findByText(/Generate token/i));

    await waitFor(() => expect(jsonCall('/workers/enrollments').method).toBe('POST'));
    const opts = jsonCall('/workers/enrollments');
    expect(opts.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(opts.body)).toEqual({ ttl_seconds: 900 });
  });

  it('sends the per-worker enable toggle as real JSON', async () => {
    respondWith(() => respond({ enabled: true, running: true, workers: [WORKER] }));
    renderPanel();

    fireEvent.click(await screen.findByText('Disable'));

    await waitFor(() => expect(jsonCall('/workers/w1').method).toBe('PATCH'));
    const opts = jsonCall('/workers/w1');
    expect(opts.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(opts.body)).toEqual({ enabled: false });
  });

  it('clears a breaker through the resume endpoint', async () => {
    respondWith(() =>
      respond({
        enabled: true,
        running: true,
        workers: [{ ...WORKER, breakers: [{ summary: 'Paused ...' }] }],
      }),
    );
    renderPanel();

    fireEvent.click(await screen.findByText(/Resume/));

    await waitFor(() => expect(jsonCall('/workers/w1/resume').method).toBe('POST'));
  });

  it('surfaces the server reason instead of a bare status', async () => {
    respondWith((path) =>
      path === '/workers'
        ? respond({ enabled: true, running: true, workers: [] })
        : respond({ detail: 'Remote workers are turned off.' }, { ok: false, status: 409 }),
    );
    renderPanel();

    fireEvent.click(await screen.findByText(/Generate token/i));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Remote workers are turned off.'));
  });

  it('renames a worker through the API', async () => {
    respondWith(() => respond({ enabled: true, running: true, workers: [WORKER] }));
    renderPanel();

    fireEvent.click(await screen.findByLabelText(/rename worker/i));
    const field = await screen.findByRole('textbox', { name: 'Rename worker' });
    fireEvent.change(field, { target: { value: 'Studio 4090' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    await waitFor(() => {
      const call = apiFetch.mock.calls.find(
        ([p, o]) => p === '/workers/w1' && o?.method === 'PATCH',
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(call[1].body)).toEqual({ name: 'Studio 4090' });
    });
  });

  it('does not send an empty rename', async () => {
    // An empty name would leave the row labelled by its key id, which is not
    // something a user can recognise.
    respondWith(() => respond({ enabled: true, running: true, workers: [WORKER] }));
    renderPanel();

    fireEvent.click(await screen.findByLabelText(/rename worker/i));
    const field = await screen.findByRole('textbox', { name: 'Rename worker' });
    fireEvent.change(field, { target: { value: '   ' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: 'Rename worker' })).not.toBeInTheDocument(),
    );
    expect(apiFetch.mock.calls.some(([, o]) => o?.method === 'PATCH')).toBe(false);
  });

  it('escape cancels a rename', async () => {
    respondWith(() => respond({ enabled: true, running: true, workers: [WORKER] }));
    renderPanel();

    fireEvent.click(await screen.findByLabelText(/rename worker/i));
    const field = await screen.findByRole('textbox', { name: 'Rename worker' });
    fireEvent.change(field, { target: { value: 'nope' } });
    fireEvent.keyDown(field, { key: 'Escape' });

    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: 'Rename worker' })).not.toBeInTheDocument(),
    );
    expect(apiFetch.mock.calls.some(([, o]) => o?.method === 'PATCH')).toBe(false);
  });
});

describe('WorkerRow', () => {
  const noop = () => {};

  it('reports an online worker and its load', () => {
    render(<WorkerRow worker={WORKER} onRemove={noop} onResume={noop} onToggle={noop} />);
    expect(screen.getByText('Online')).toBeInTheDocument();
    expect(screen.getByText(/Tasks 1 \/ 2/)).toBeInTheDocument();
  });

  it('surfaces the breaker reason instead of a bare percentage', () => {
    render(
      <WorkerRow
        worker={{
          ...WORKER,
          breakers: [{ summary: 'Paused after 3 failures (boom) — retrying in 45s' }],
        }}
        onRemove={noop}
        onResume={noop}
        onToggle={noop}
      />,
    );
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.getByText(/retrying in 45s/)).toBeInTheDocument();
    expect(screen.getByText(/Resume/)).toBeInTheDocument();
  });

  it('flags a worker that has not been approved', () => {
    render(
      <WorkerRow
        worker={{ ...WORKER, consent_granted: false }}
        onRemove={noop}
        onResume={noop}
        onToggle={noop}
      />,
    );
    expect(screen.getByText('Not approved')).toBeInTheDocument();
  });

  it('shows a disabled worker as disabled rather than offline', () => {
    render(
      <WorkerRow
        worker={{ ...WORKER, enabled: false }}
        onRemove={noop}
        onResume={noop}
        onToggle={noop}
      />,
    );
    expect(screen.getByText('Disabled')).toBeInTheDocument();
  });
});
