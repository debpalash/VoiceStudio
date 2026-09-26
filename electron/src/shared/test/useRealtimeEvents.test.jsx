import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

const { authenticatedWsUrl } = vi.hoisted(() => ({ authenticatedWsUrl: vi.fn() }));
vi.mock('../api/authSession', async (importOriginal) => ({
  ...(await importOriginal()),
  authenticatedWsUrl,
}));

// The cold-start probe in useRealtimeEvents uses a RAW fetch() that does not
// carry the LAN PIN / remote API-key headers apiFetch would attach. It must
// therefore poll the auth-exempt /health endpoint — never a gated path like
// /model/status, which 401s in LAN-share/remote mode and would wedge the
// reconnect loop so the WebSocket never opens. This test pins that contract.
import useRealtimeEvents from '../hooks/useRealtimeEvents';

// Minimal WebSocket stub: records construction and lets us drive onopen.
class FakeWebSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 0; // CONNECTING
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    FakeWebSocket.instances.push(this);
  }
  close() {
    this.readyState = 3; // CLOSED
  }
}

function Harness({ handlers = {} }) {
  useRealtimeEvents(handlers);
  return null;
}

describe('useRealtimeEvents cold-start health probe', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    authenticatedWsUrl.mockReset();
    authenticatedWsUrl.mockResolvedValue('ws://localhost/ws/events?ws_ticket=fresh-ticket');
    vi.stubGlobal('WebSocket', FakeWebSocket);
    if (!AbortSignal.timeout) {
      AbortSignal.timeout = () => new AbortController().signal;
    }
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('probes the auth-exempt /health endpoint, not a gated path', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);

    render(<Harness />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const probedUrl = String(fetchMock.mock.calls[0][0]);
    expect(probedUrl).toMatch(/\/health$/);
    // Guard against the #439 regression: the gated path drops auth → 401.
    expect(probedUrl).not.toContain('/model/status');
  });

  it('opens the WebSocket once the health probe succeeds', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);

    render(<Harness />);

    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    expect(authenticatedWsUrl).toHaveBeenCalledWith(
      '/ws/events',
      expect.objectContaining({ apiBase: expect.any(String) }),
    );
    expect(FakeWebSocket.instances[0].url).toBe('ws://localhost/ws/events?ws_ticket=fresh-ticket');
  });

  it('obtains a fresh one-use ticket for every reconnect', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    authenticatedWsUrl
      .mockResolvedValueOnce('ws://localhost/ws/events?ws_ticket=first')
      .mockResolvedValueOnce('ws://localhost/ws/events?ws_ticket=second');
    render(<Harness />);
    await vi.waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));

    FakeWebSocket.instances[0].onclose({ code: 1006 });
    await vi.advanceTimersByTimeAsync(2000);
    await vi.waitFor(() => expect(FakeWebSocket.instances.length).toBe(2));

    expect(authenticatedWsUrl).toHaveBeenCalledTimes(2);
    expect(FakeWebSocket.instances.map((instance) => instance.url)).toEqual([
      'ws://localhost/ws/events?ws_ticket=first',
      'ws://localhost/ws/events?ws_ticket=second',
    ]);
  });

  it('does not log authentication error details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    authenticatedWsUrl.mockRejectedValueOnce(new Error('private-session-value'));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    render(<Harness />);

    await waitFor(() => expect(warn).toHaveBeenCalled());
    expect(warn).toHaveBeenCalledWith('[ws/events] connection failed');
    expect(JSON.stringify(warn.mock.calls)).not.toContain('private-session-value');
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('does not log remote-controlled malformed frames or parser details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(<Harness />);
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));

    const privateFrame = 'token=private-value\nFORGED';
    FakeWebSocket.instances[0].onmessage({ data: privateFrame });

    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith('[ws/events] malformed message ignored');
    const warningArguments = warn.mock.calls.flat();
    expect(warningArguments).not.toContain(privateFrame);
    expect(warningArguments.every((argument) => !String(argument).includes('SyntaxError'))).toBe(
      true,
    );
  });

  it('does not misclassify or swallow event-handler failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const failure = new Error('handler failed');
    render(
      <Harness
        handlers={{
          failed: () => {
            throw failure;
          },
        }}
      />,
    );
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));

    expect(() => FakeWebSocket.instances[0].onmessage({ data: '{"kind":"failed"}' })).toThrow(
      failure,
    );
    expect(warn).not.toHaveBeenCalled();
  });

  it('dispatches only explicitly registered own handlers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    const inherited = vi.fn();
    const inheritedToString = vi.fn();
    const handlers = Object.create({ inherited, toString: inheritedToString });
    render(<Harness handlers={handlers} />);
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));

    FakeWebSocket.instances[0].onmessage({ data: '{"kind":"inherited"}' });
    FakeWebSocket.instances[0].onmessage({ data: '{"kind":"toString"}' });
    expect(inherited).not.toHaveBeenCalled();
    expect(inheritedToString).not.toHaveBeenCalled();
  });

  it('does NOT open the WebSocket while the backend is unreachable', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('ECONNREFUSED'));
    vi.stubGlobal('fetch', fetchMock);

    render(<Harness />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(FakeWebSocket.instances.length).toBe(0);
  });
});
