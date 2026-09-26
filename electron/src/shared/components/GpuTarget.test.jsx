import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }));

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('../api/client', () => ({ apiFetch }));

// apiFetch resolves to a raw Response and does not throw on 4xx.
const respond = (body, { ok = true, status = 200 } = {}) => ({
  ok,
  status,
  json: async () => body,
});

import toast from 'react-hot-toast';
import GpuTarget from './GpuTarget';
import { useAppStore } from '../store';

const LOCAL = { id: 'local', label: 'Local', available: true, connected: true, is_local: true };
const DESKTOP = {
  id: 'w1',
  label: 'desktop-4090',
  endpoint: '192.168.0.222:2222',
  available: true,
  connected: true,
  is_local: false,
};

function renderPicker() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GpuTarget />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // The picker resolves against the workspace in front of the user, so every
  // test states which one it is rather than inheriting the last one's.
  useAppStore.setState({ mode: 'studio' });
});

describe('GpuTarget', () => {
  it('renders nothing when no worker is enrolled', async () => {
    // A user who never opted in must see no change to the header.
    apiFetch.mockResolvedValue(
      respond({ target: 'local', active: { remote: false }, targets: [LOCAL] }),
    );
    const { container } = renderPicker();
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(container.querySelector('button')).toBeNull();
  });

  it('shows Local when local is chosen', async () => {
    apiFetch.mockResolvedValue(
      respond({ target: 'local', active: { remote: false }, targets: [LOCAL, DESKTOP] }),
    );
    renderPicker();
    expect(await screen.findByText('Local')).toBeInTheDocument();
  });

  it('shows the worker name when a remote target is active', async () => {
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        active: { remote: true, worker_id: 'w1', label: 'desktop-4090' },
        targets: [LOCAL, DESKTOP],
      }),
    );
    renderPicker();
    expect(await screen.findByText('desktop-4090')).toBeInTheDocument();
  });

  it('shows the RESOLVED answer, not the stored choice', async () => {
    // The case that matters: you picked your desktop, it went to sleep, and
    // the work is running here. Showing "desktop-4090" would be a lie exactly
    // when it matters most.
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        active: {
          remote: false,
          label: 'Local',
          reason: 'desktop-4090 is offline — running locally',
        },
        targets: [LOCAL, { ...DESKTOP, available: false, connected: false, detail: 'offline' }],
      }),
    );
    renderPicker();

    expect(await screen.findByText('Local')).toBeInTheDocument();
    expect(screen.queryByText('desktop-4090')).not.toBeInTheDocument();
  });

  it('lists targets with their endpoint, and marks the chosen one', async () => {
    apiFetch.mockResolvedValue(
      respond({ target: 'local', active: { remote: false }, targets: [LOCAL, DESKTOP] }),
    );
    renderPicker();

    fireEvent.click(await screen.findByRole('button'));

    expect(await screen.findByText('desktop-4090')).toBeInTheDocument();
    expect(screen.getByText('192.168.0.222:2222')).toBeInTheDocument();
  });

  it('lets you choose an offline worker so you can set it up first', async () => {
    // You pick your desktop, then go and switch it on. Routing falls back
    // locally with a reason until it answers.
    apiFetch.mockResolvedValue(
      respond({
        target: 'local',
        active: { remote: false },
        targets: [LOCAL, { ...DESKTOP, available: false, connected: false, detail: 'offline' }],
      }),
    );
    renderPicker();

    fireEvent.click(await screen.findByRole('button'));
    fireEvent.click(await screen.findByText('desktop-4090'));

    await waitFor(() => {
      const call = apiFetch.mock.calls.find(
        ([p, o]) => p === '/workers/target' && o?.method === 'POST',
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(call[1].body)).toEqual({ target: 'w1' });
    });
  });

  it('shows why a target is unavailable', async () => {
    apiFetch.mockResolvedValue(
      respond({
        target: 'local',
        active: { remote: false },
        targets: [
          LOCAL,
          { ...DESKTOP, available: false, detail: 'paused after repeated failures' },
        ],
      }),
    );
    renderPicker();

    fireEvent.click(await screen.findByRole('button'));
    expect(await screen.findByText(/paused after repeated failures/)).toBeInTheDocument();
  });

  it('sends the chosen target as real JSON', async () => {
    apiFetch.mockResolvedValue(
      respond({ target: 'local', active: { remote: false }, targets: [LOCAL, DESKTOP] }),
    );
    renderPicker();

    fireEvent.click(await screen.findByRole('button'));
    fireEvent.click(await screen.findByText('desktop-4090'));

    await waitFor(() => {
      // GET and POST share this path, so match on the method — otherwise the
      // polling GET is found first and carries no headers.
      const call = apiFetch.mock.calls.find(
        ([p, o]) => p === '/workers/target' && o?.method === 'POST',
      );
      expect(call).toBeTruthy();
      expect(call[1].headers['Content-Type']).toBe('application/json');
      expect(JSON.parse(call[1].body)).toEqual({ target: 'w1' });
    });
  });

  it('surfaces a rejection instead of failing silently', async () => {
    apiFetch.mockImplementation((path) =>
      Promise.resolve(
        path === '/workers/target' && apiFetch.mock.calls.length > 1
          ? respond({ detail: 'No such worker.' }, { ok: false, status: 404 })
          : respond({ target: 'local', active: { remote: false }, targets: [LOCAL, DESKTOP] }),
      ),
    );
    renderPicker();

    fireEvent.click(await screen.findByRole('button'));
    fireEvent.click(await screen.findByText('desktop-4090'));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('No such worker.'));
  });

  it('offers no rename for Local — it is this machine, not a machine', async () => {
    apiFetch.mockResolvedValue(
      respond({ target: 'local', active: { remote: false }, targets: [LOCAL, DESKTOP] }),
    );
    renderPicker();

    fireEvent.click(await screen.findByRole('button'));

    expect(screen.queryByLabelText(/rename/i)).not.toBeInTheDocument();
  });

  // ── Status, latency, live tasks ─────────────────────────────────────────

  const READY = { ...DESKTOP, status: 'ready', latency_ms: 12.4, active_tasks: 0, max_tasks: 2 };

  it('shows the worker name and its latency in the chip', async () => {
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        active: { remote: true, worker_id: 'w1', label: 'desktop-4090' },
        targets: [LOCAL, READY],
      }),
    );
    renderPicker();

    expect(await screen.findByText('desktop-4090')).toBeInTheDocument();
    expect(screen.getByText('12 ms')).toBeInTheDocument();
  });

  it('never shows latency for Local — there is no network to measure', async () => {
    apiFetch.mockResolvedValue(
      respond({ target: 'local', active: { remote: false }, targets: [LOCAL, READY] }),
    );
    renderPicker();

    await screen.findByText('Local');
    expect(screen.queryByText(/ms$/)).not.toBeInTheDocument();
  });

  it('says nothing when latency has not been measured yet', async () => {
    // 0 means "no sample", not "instant".
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        active: { remote: true, worker_id: 'w1', label: 'desktop-4090' },
        targets: [LOCAL, { ...READY, latency_ms: 0 }],
      }),
    );
    renderPicker();

    await screen.findByText('desktop-4090');
    expect(screen.queryByText(/ms$/)).not.toBeInTheDocument();
  });

  it('colours the dot green when ready, amber when busy, red when offline', async () => {
    for (const [status, cls] of [
      // Themed tokens, not palette-fixed Tailwind classes: on Midnight or
      // Catppuccin a hardcoded `emerald` dot sits next to that theme's own
      // green and reads as a rendering bug.
      ['ready', 'bg-\\[var\\(--color-success\\)\\]'],
      ['busy', 'bg-\\[var\\(--color-warn\\)\\]'],
      ['offline', 'bg-\\[var\\(--color-danger\\)\\]'],
    ]) {
      apiFetch.mockResolvedValue(
        respond({
          target: 'w1',
          active:
            status === 'offline'
              ? { remote: false, label: 'Local', reason: 'desktop-4090 is offline' }
              : { remote: true, worker_id: 'w1', label: 'desktop-4090' },
          targets: [LOCAL, { ...READY, status, connected: status !== 'offline' }],
        }),
      );
      const { container, unmount } = renderPicker();
      await waitFor(() => expect(container.querySelector(`.${cls}`)).toBeTruthy());
      unmount();
      vi.clearAllMocks();
    }
  });

  it('the dot reports the CHOSEN worker even when work fell back locally', async () => {
    // A green dot beside "Local" would hide that the machine you picked is down.
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        active: { remote: false, label: 'Local', reason: 'desktop-4090 is offline' },
        targets: [LOCAL, { ...READY, status: 'offline', connected: false }],
      }),
    );
    const { container } = renderPicker();

    await screen.findByText('Local');
    expect(container.querySelector('.bg-\\[var\\(--color-danger\\)\\]')).toBeTruthy();
  });

  // ── Op awareness ────────────────────────────────────────────────────────
  //
  // A worker is not remote for everything: work reaches it only where this
  // side has a producer, and those are ported one at a time. Without asking
  // per operation the badge reads "gpu2 ● ready" on the Dub tab while 100% of
  // dubbing runs locally — the exact lie the resolved-answer rule exists to
  // prevent, in a place the user cannot see it happen.

  const paths = () => apiFetch.mock.calls.filter(([, o]) => !o?.method).map(([p]) => p);

  it('asks routing about the operation the current workspace submits', async () => {
    useAppStore.setState({ mode: 'dub' });
    apiFetch.mockResolvedValue(
      respond({ target: 'w1', op: 'dub', active: { remote: false }, targets: [LOCAL, DESKTOP] }),
    );
    renderPicker();

    await waitFor(() => expect(paths()).toContain('/workers/target?op=dub'));
  });

  it('asks about the target itself where the workspace submits no job', async () => {
    // The launchpad renders no GPU work; the menu is being opened to pick a
    // machine, not to ask about one job.
    useAppStore.setState({ mode: 'launchpad' });
    apiFetch.mockResolvedValue(
      respond({ target: 'w1', op: '', active: { remote: false }, targets: [LOCAL, DESKTOP] }),
    );
    renderPicker();

    await waitFor(() => expect(paths()).toContain('/workers/target'));
  });

  it('reads Local, in the user language, on a surface that has no remote path', async () => {
    useAppStore.setState({ mode: 'dub' });
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        op: 'dub',
        // The worker is healthy and chosen — it simply receives no dubbing.
        active: { remote: false, label: 'Local', reason: 'dubbing does not run remotely yet' },
        remote_operations: ['tts'],
        targets: [LOCAL, READY],
      }),
    );
    renderPicker();

    expect(await screen.findByText('Local')).toBeInTheDocument();
    expect(screen.queryByText('desktop-4090')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button'));
    // Localized here, not the control plane's English sentence.
    expect(
      await screen.findByText('Local — dubbing does not run remotely yet'),
    ).toBeInTheDocument();
  });

  it('does not warn in amber when the surface simply has no remote path', async () => {
    // Nothing is wrong: the machine is fine and this work was never going to
    // leave. Amber is reserved for "the worker you picked is down".
    useAppStore.setState({ mode: 'dub' });
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        op: 'dub',
        active: { remote: false, label: 'Local', reason: 'dubbing does not run remotely yet' },
        remote_operations: ['tts'],
        targets: [LOCAL, READY],
      }),
    );
    const { container } = renderPicker();

    await screen.findByText('Local');
    expect(container.querySelector('.text-\\[color\\:var\\(--color-warn\\)\\]')).toBeNull();
  });

  it('labels dictation as intentionally local without an unported notice', async () => {
    useAppStore.setState({ mode: 'dictation' });
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        op: 'dictation',
        active: { remote: false, label: 'Local', reason: 'ignored server wording' },
        remote_operations: ['audiobook', 'tts'],
        targets: [LOCAL, READY],
      }),
    );
    renderPicker();

    await waitFor(() => expect(paths()).toContain('/workers/target?op=dictation'));
    fireEvent.click(await screen.findByRole('button'));
    expect(await screen.findByText('Dictation always runs on this machine')).toBeInTheDocument();
    expect(screen.queryByText(/does not run remotely yet/)).not.toBeInTheDocument();
  });

  it('says what a worker actually takes, in the menu', async () => {
    apiFetch.mockResolvedValue(
      respond({
        target: 'local',
        op: 'tts',
        active: { remote: false },
        remote_operations: ['tts'],
        targets: [LOCAL, READY],
      }),
    );
    renderPicker();

    fireEvent.click(await screen.findByRole('button'));
    expect(await screen.findByText(/192\.168\.0\.222:2222 · TTS only/)).toBeInTheDocument();
  });

  it('claims no coverage when the control plane reports none', async () => {
    // An older control plane answers without `remote_operations`. Saying
    // "TTS only" there would be an invention, and greying the surface out
    // would break a working setup.
    useAppStore.setState({ mode: 'dub' });
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        active: { remote: true, worker_id: 'w1', label: 'desktop-4090' },
        targets: [LOCAL, READY],
      }),
    );
    renderPicker();

    expect(await screen.findByText('desktop-4090')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button'));
    expect(screen.queryByText(/only/i)).not.toBeInTheDocument();
  });

  it('shows the address and live task count for a busy worker', async () => {
    apiFetch.mockResolvedValue(
      respond({
        target: 'w1',
        active: { remote: true, worker_id: 'w1', label: 'desktop-4090' },
        targets: [LOCAL, { ...READY, status: 'busy', active_tasks: 1, max_tasks: 2 }],
      }),
    );
    renderPicker();

    fireEvent.click(await screen.findByRole('button'));
    expect(await screen.findByText(/192\.168\.0\.222:2222 · 1\/2 tasks/)).toBeInTheDocument();
  });
});
