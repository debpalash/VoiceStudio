import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
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

function rejectPrimaryRequest(message = 'configured route unavailable') {
  apiFetch.mockRejectedValue(new Error(message));
}

function returnSpecFromPageOrigin() {
  fetch.mockResolvedValue({ ok: true, json: async () => MINIMAL_SPEC });
}

function mountOpenApi() {
  return render(<OpenApiPanel />);
}

describe('OpenApiPanel', () => {
  beforeEach(() => {
    backendBase.url = 'http://127.0.0.1:3900';
    sessionStorage.clear();
    vi.clearAllMocks();
    const unavailablePageOrigin = vi.fn(async () => {
      throw new Error('page-origin route unavailable');
    });
    vi.stubGlobal('fetch', unavailablePageOrigin);
  });

  afterEach(() => {
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it('renders a page-origin spec without changing the shown backend address', async () => {
    rejectPrimaryRequest();
    returnSpecFromPageOrigin();

    mountOpenApi();

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(screen.getByText('http://127.0.0.1:3900/openapi.json')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      '/openapi.json',
      expect.objectContaining({ cache: 'no-store', credentials: 'include' }),
    );
    expect(await screen.findByTestId('scalar-mock')).toBeInTheDocument();
    expect(screen.queryByTestId('openapi-unreachable')).toBeNull();
  });

  it('recognizes localhost on the fixed development backend port', async () => {
    backendBase.url = 'http://localhost:3900';
    rejectPrimaryRequest();
    returnSpecFromPageOrigin();

    mountOpenApi();

    expect(screen.getByText('http://localhost:3900/openapi.json')).toBeInTheDocument();
    expect(await screen.findByTestId('openapi-reference')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it.each(['https://api.example.test', 'http://localhost:4900', 'https://localhost:3900'])(
    'refuses a page-origin substitution for %s',
    async (backendUrl) => {
      backendBase.url = backendUrl;
      rejectPrimaryRequest();

      mountOpenApi();

      expect(await screen.findByRole('alert')).toBeInTheDocument();
      expect(screen.queryByTestId('scalar-mock')).toBeNull();
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it('copies the tab PIN into the page-origin request headers', async () => {
    sessionStorage.setItem('ov_pin', '2468');
    rejectPrimaryRequest();
    returnSpecFromPageOrigin();

    mountOpenApi();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());

    const [, requestInit] = fetch.mock.calls[0];
    expect(requestInit.headers.get('X-OmniVoice-Pin')).toEqual('2468');
    expect(await screen.findByTestId('scalar-mock')).toBeInTheDocument();
  });

  it('keeps Scalar unmounted after a page-origin 404', async () => {
    rejectPrimaryRequest();
    fetch.mockResolvedValue({ ok: false, status: 404 });

    mountOpenApi();

    expect(await screen.findByText('Backend spec unavailable')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledOnce();
    expect(screen.queryByTestId('scalar-mock')).toBeNull();
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
