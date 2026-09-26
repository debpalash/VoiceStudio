import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import BackendCrashNotice from '../components/BackendCrashNotice';

// #1164 — the point of the run-sentinel work: BackendCrashNotice must light
// up in a plain BROWSER (dev/Docker) with ZERO changes of its own, driven by
// the REAL utils/backendCrash module whose HTTP fallback we stub at the
// fetch level. (The sibling BackendCrashNotice.test.jsx mocks the module —
// this file proves the whole browser wiring end-to-end.)
vi.mock('../utils/bugReport', () => ({
  openBugReport: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../api/external', () => ({
  openExternal: vi.fn().mockResolvedValue(undefined),
}));

const RECORD = {
  detected_at: Math.floor(Date.now() / 1000) - 45,
  started_at: Math.floor(Date.now() / 1000) - 500,
  ended_between: [Math.floor(Date.now() / 1000) - 90, Math.floor(Date.now() / 1000) - 45],
  uptime_hint_s: 455,
  version: '0.3.23',
  last_activity: { ts: null, kind: 'generate', detail: 'omnivoice' },
  log_tail: ['INFO generate started', 'INFO loading model weights'],
};

function stubBackend({ record = RECORD, acknowledged = false } = {}) {
  const calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, init) => {
      calls.push({ url: String(url), init });
      if (String(url).includes('/system/last-run-crash/ack')) {
        return new Response('{"ok":true}', { status: 200 });
      }
      if (String(url).includes('/system/last-run-crash')) {
        return new Response(JSON.stringify({ record, acknowledged }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('{}', { status: 200 });
    }),
  );
  return calls;
}

describe('BackendCrashNotice — browser mode (run-sentinel HTTP fallback)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('surfaces a previous-run death fetched from the backend, with details + ack', async () => {
    const calls = stubBackend();
    render(<BackendCrashNotice />);

    const alert = await screen.findByRole('alert');
    // #1375: the sentinel banner speaks human now — "ended without a clean
    // shutdown", naming the benign causes — instead of surfacing the raw
    // exit_desc, because the sentinel cannot know it was a crash.
    expect(alert.textContent).toContain('without a clean shutdown');
    expect(calls.some((c) => c.url.includes('/system/last-run-crash'))).toBe(true);

    // Details dialog shows the captured log tail; viewing acks over HTTP.
    fireEvent.click(screen.getByRole('button', { name: /view crash details/i }));
    expect(await screen.findByText(/INFO loading model weights/)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.url.includes('/system/last-run-crash/ack') && c.init?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  it('renders nothing when the backend reports no record (or is unreachable)', async () => {
    const calls = stubBackend({ record: null });
    const { container } = render(<BackendCrashNotice />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(container).toBeEmptyDOMElement();
  });
});
