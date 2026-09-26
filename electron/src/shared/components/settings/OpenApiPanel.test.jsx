import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

// Stub Scalar's heavy bundled component — we only need to assert the panel
// mounts the reference container, not exercise the real Vue app in jsdom.
vi.mock('@scalar/api-reference-react', async () => {
  const { jsx } = await import('react/jsx-runtime');
  return { ApiReferenceReact: () => jsx('div', { 'data-testid': 'scalar-mock' }) };
});

// Control the spec fetch + backend base without a live backend.
const backendBase = vi.hoisted(() => ({ url: 'http://127.0.0.1:3900' }));
vi.mock('../../api/client', () => ({
  get API() {
    return backendBase.url;
  },
  apiFetch: vi.fn(),
}));

// Clipboard helper + toast — controlled so the copy affordance's success AND
// failure feedback can both be asserted.
vi.mock('../../utils/copyText', () => ({ copyText: vi.fn() }));
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

import OpenApiPanel from './OpenApiPanel';
import { apiFetch } from '../../api/client';
import { copyText } from '../../utils/copyText';
import toast from 'react-hot-toast';

const MINIMAL_SPEC = {
  openapi: '3.1.0',
  info: { title: 'VoiceStudio', version: '0.0.0' },
  paths: {},
};

describe('OpenApiPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    backendBase.url = 'http://127.0.0.1:3900';
    sessionStorage.clear();
    // The same-origin fallback goes through the global fetch. Default it to
    // "nothing there" so the direct-path tests below stay about apiFetch.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('falls back to the same-origin /openapi.json when the backend base is unreachable', async () => {
    // An embedded webview (the Claude desktop browser pane) blocks every origin
    // but the page's own, so the direct base fails while the dev-server proxy /
    // backend-served copy on the page's origin answers. The reference must
    // render from that copy instead of showing "backend unavailable".
    apiFetch.mockRejectedValue(new TypeError('Failed to fetch'));
    fetch.mockResolvedValue({ ok: true, json: async () => MINIMAL_SPEC });

    render(<OpenApiPanel />);

    expect(await screen.findByTestId('scalar-mock')).toBeInTheDocument();
    expect(screen.queryByTestId('openapi-unreachable')).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      '/openapi.json',
      expect.objectContaining({ cache: 'no-store' }),
    );
    // The copy / open-raw affordances still name the real backend base.
    expect(screen.getByText('http://127.0.0.1:3900/openapi.json')).toBeInTheDocument();
  });

  it('accepts the default localhost development backend', async () => {
    backendBase.url = 'http://localhost:3900';
    apiFetch.mockRejectedValue(new TypeError('Failed to fetch'));
    fetch.mockResolvedValue({ ok: true, json: async () => MINIMAL_SPEC });
    render(<OpenApiPanel />);
    expect(await screen.findByTestId('scalar-mock')).toBeInTheDocument();
  });

  it.each(['https://my-backend.example', 'http://localhost:3901', 'https://localhost:3900'])(
    'never substitutes the page server for %s',
    async (url) => {
      backendBase.url = url;
      apiFetch.mockRejectedValue(new TypeError('Failed to fetch'));
      fetch.mockResolvedValue({ ok: true, json: async () => MINIMAL_SPEC });
      render(<OpenApiPanel />);
      expect(await screen.findByTestId('openapi-unreachable')).toBeInTheDocument();
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it('preserves the configured PIN through the known development proxy', async () => {
    sessionStorage.setItem('ov_pin', 'test-pin');
    apiFetch.mockRejectedValue(new TypeError('Failed to fetch'));
    fetch.mockResolvedValue({ ok: true, json: async () => MINIMAL_SPEC });
    render(<OpenApiPanel />);
    await screen.findByTestId('scalar-mock');
    expect(fetch.mock.calls[0][1].headers.get('X-OmniVoice-Pin')).toBe('test-pin');
  });

  it('still reports the backend unreachable when the same-origin copy is not there either', async () => {
    apiFetch.mockRejectedValue(new Error('backend down'));
    fetch.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });

    render(<OpenApiPanel />);

    expect(await screen.findByTestId('openapi-unreachable')).toBeInTheDocument();
    expect(screen.queryByTestId('scalar-mock')).not.toBeInTheDocument();
  });

  it('fetches the local /openapi.json spec and renders the Scalar reference', async () => {
    apiFetch.mockResolvedValue({ json: async () => MINIMAL_SPEC });

    render(<OpenApiPanel />);

    expect(screen.getByRole('heading', { name: 'VoiceStudio API' })).toBeInTheDocument();
    expect(screen.queryByText('OpenAPI Reference')).not.toBeInTheDocument();

    // The spec is fetched from the backend root route (not under /api).
    expect(apiFetch).toHaveBeenCalledWith('/openapi.json');

    // Reference container + embedded (mocked) Scalar component mount.
    expect(await screen.findByTestId('scalar-mock')).toBeInTheDocument();
    expect(screen.getByTestId('openapi-reference')).toBeInTheDocument();

    // Copy / open-raw affordances point at the resolved backend base.
    expect(screen.getByText('http://127.0.0.1:3900/openapi.json')).toBeInTheDocument();
    expect(screen.getByTestId('openapi-copy-url')).toBeInTheDocument();
    expect(screen.getByTestId('openapi-open-raw')).toBeInTheDocument();
  });

  it('shows the unreachable-backend fallback when the spec fetch fails', async () => {
    apiFetch.mockRejectedValue(new Error('backend down'));

    render(<OpenApiPanel />);

    const fallback = await screen.findByTestId('openapi-unreachable');
    expect(fallback).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByTestId('openapi-retry')).toBeInTheDocument();

    // Scalar must never mount when the spec can't be reached.
    expect(screen.queryByTestId('scalar-mock')).not.toBeInTheDocument();
  });

  it('recovers when Retry succeeds after an initial failure', async () => {
    apiFetch
      .mockRejectedValueOnce(new Error('backend down'))
      .mockResolvedValueOnce({ json: async () => MINIMAL_SPEC });

    render(<OpenApiPanel />);

    fireEvent.click(await screen.findByTestId('openapi-retry'));

    expect(await screen.findByTestId('scalar-mock')).toBeInTheDocument();
    expect(screen.queryByTestId('openapi-unreachable')).not.toBeInTheDocument();
  });

  it('toasts success when the spec URL copies', async () => {
    apiFetch.mockResolvedValue({ json: async () => MINIMAL_SPEC });
    copyText.mockResolvedValue(true);

    render(<OpenApiPanel />);
    fireEvent.click(screen.getByTestId('openapi-copy-url'));

    await vi.waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(copyText).toHaveBeenCalledWith('http://127.0.0.1:3900/openapi.json');
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('toasts an error when the clipboard copy fails (non-secure context)', async () => {
    apiFetch.mockResolvedValue({ json: async () => MINIMAL_SPEC });
    copyText.mockResolvedValue(false);

    render(<OpenApiPanel />);
    fireEvent.click(screen.getByTestId('openapi-copy-url'));

    // A failed copy must never be silent.
    await vi.waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
  });
});
