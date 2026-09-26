import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import LLMProvidersPanel from './LLMProvidersPanel';

const PROVIDERS = {
  active: 'groq',
  providers: [
    {
      id: 'groq',
      display_name: 'Groq',
      local: false,
      needs_account: false,
      signup_url: 'https://console.groq.com',
      notes: 'fast inference',
      base_url: 'https://api.groq.com/openai/v1',
      model: 'llama-3.3-70b',
      has_key: true,
      key_from_env: false,
      configured: true,
    },
    {
      id: 'ollama',
      display_name: 'Ollama',
      local: true,
      needs_account: false,
      signup_url: null,
      notes: null,
      base_url: 'http://localhost:11434/v1',
      model: 'llama3',
      has_key: false,
      key_from_env: false,
      configured: false,
    },
  ],
};

// A provider whose base_url / model / active selection are all pinned by env
// vars — the UI must disable those fields + the make-active button and explain
// why (the silent-revert / dead-make-active traps).
const ENV_LOCKED = {
  active: 'openai',
  providers: [
    {
      id: 'openai',
      display_name: 'OpenAI',
      local: false,
      needs_account: false,
      signup_url: 'https://platform.openai.com',
      notes: 'env-pinned',
      base_url: 'https://env.example/v1',
      model: 'env-model',
      has_key: true,
      key_from_env: true,
      base_url_from_env: true,
      model_from_env: true,
      active_from_env: true,
      configured: true,
    },
  ],
};

function mockFetchSequence(...responses) {
  const fn = vi.fn();
  for (const r of responses) {
    fn.mockResolvedValueOnce({
      ok: (r.status ?? 200) >= 200 && (r.status ?? 200) < 300,
      status: r.status ?? 200,
      json: async () => r.body,
      text: async () => JSON.stringify(r.body),
    });
  }
  return fn;
}

describe('LLMProvidersPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('loads providers and preselects the active one', async () => {
    global.fetch = mockFetchSequence({ body: PROVIDERS });
    render(<LLMProvidersPanel />);
    const select = await screen.findByTestId('llm-provider-select');
    await waitFor(() => expect(select.value).toBe('groq'));
    expect(screen.getByTestId('llm-provider-base-url').value).toBe(
      'https://api.groq.com/openai/v1',
    );
    // Nothing env-pinned here → the fields stay editable and activate is live.
    expect(screen.getByTestId('llm-provider-base-url')).not.toBeDisabled();
    expect(screen.queryByTestId('llm-active-env-banner')).toBeNull();
  });

  it('env-pinned base_url / model / active are disabled and explained', async () => {
    global.fetch = mockFetchSequence({ body: ENV_LOCKED });
    render(<LLMProvidersPanel />);
    await screen.findByTestId('llm-provider-select');
    expect(screen.getByTestId('llm-provider-base-url')).toBeDisabled();
    expect(screen.getByTestId('llm-provider-model')).toBeDisabled();
    expect(screen.getByTestId('llm-provider-key')).toBeDisabled();
    // Make-active is dead while LLM_DEFAULT_PROVIDER pins the choice.
    expect(screen.getByTestId('llm-provider-activate')).toBeDisabled();
    expect(screen.getByTestId('llm-active-env-banner')).toBeInTheDocument();
  });

  it('successful test shows model + latency badge', async () => {
    global.fetch = mockFetchSequence(
      { body: PROVIDERS }, // mount GET
      { body: {} }, // save PUT
      { body: PROVIDERS }, // refresh GET
      { body: { ok: true, model: 'llama-3.3-70b', reply: 'ok', latency_ms: 412 } }, // test POST
    );
    render(<LLMProvidersPanel />);
    fireEvent.click(await screen.findByTestId('llm-provider-test'));
    await waitFor(() => expect(screen.getByText(/llama-3\.3-70b · 412 ms/)).toBeInTheDocument());
  });

  it('auth failure renders the actionable localized message, not the raw detail', async () => {
    global.fetch = mockFetchSequence(
      { body: PROVIDERS },
      { body: {} },
      { body: PROVIDERS },
      {
        body: {
          ok: false,
          kind: 'auth',
          detail: 'AuthenticationError: Incorrect API key',
          latency_ms: 130,
        },
      },
    );
    render(<LLMProvidersPanel />);
    fireEvent.click(await screen.findByTestId('llm-provider-test'));
    await waitFor(() => expect(screen.getByText(/Key rejected \(401\/403\)/)).toBeInTheDocument());
    expect(screen.queryByText(/AuthenticationError/)).toBeNull();
  });

  it('network failure explains reachability (local server hint)', async () => {
    global.fetch = mockFetchSequence(
      { body: PROVIDERS },
      { body: {} },
      { body: PROVIDERS },
      { body: { ok: false, kind: 'network', detail: 'APIConnectionError: refused' } },
    );
    render(<LLMProvidersPanel />);
    fireEvent.click(await screen.findByTestId('llm-provider-test'));
    await waitFor(() => expect(screen.getByText(/Can't reach the provider/)).toBeInTheDocument());
  });

  it('fetch models fills the datalist for the model input', async () => {
    global.fetch = mockFetchSequence(
      { body: PROVIDERS },
      { body: {} }, // save PUT (models saves non-key fields first)
      { body: PROVIDERS }, // refresh GET
      { body: { ok: true, models: ['llama-3.1-8b', 'llama-3.3-70b'] } }, // models GET
    );
    render(<LLMProvidersPanel />);
    fireEvent.click(await screen.findByTestId('llm-provider-models'));
    await waitFor(() => expect(screen.getByTestId('llm-provider-model')).toHaveAttribute('list'));
    expect(document.querySelectorAll('datalist option')).toHaveLength(2);
  });

  it('a truncated model list still fills the datalist', async () => {
    global.fetch = mockFetchSequence(
      { body: PROVIDERS },
      { body: {} },
      { body: PROVIDERS },
      { body: { ok: true, models: ['a', 'b', 'c'], truncated: true } },
    );
    render(<LLMProvidersPanel />);
    fireEvent.click(await screen.findByTestId('llm-provider-models'));
    await waitFor(() => expect(screen.getByTestId('llm-provider-model')).toHaveAttribute('list'));
    expect(document.querySelectorAll('datalist option')).toHaveLength(3);
  });

  it('local provider hides the API key row', async () => {
    global.fetch = mockFetchSequence({ body: PROVIDERS });
    render(<LLMProvidersPanel />);
    const select = await screen.findByTestId('llm-provider-select');
    fireEvent.change(select, { target: { value: 'ollama' } });
    await waitFor(() => expect(screen.queryByTestId('llm-provider-key')).toBeNull());
  });

  // #963 honesty: a green Test on a provider that is NOT the active one must
  // say the provider isn't used for translation yet — pre-fix the panel read
  // as "done" while translation kept using another provider.
  it('test-only flow on a non-active provider surfaces the not-yet-active notice', async () => {
    global.fetch = mockFetchSequence(
      { body: PROVIDERS }, // mount GET (active: groq)
      { body: {} }, // save PUT (ollama, make_active:false)
      { body: PROVIDERS }, // refresh GET — active is still groq
      { body: { ok: true, model: 'llama3', reply: 'ok', latency_ms: 9 } }, // test POST
    );
    render(<LLMProvidersPanel />);
    const select = await screen.findByTestId('llm-provider-select');
    fireEvent.change(select, { target: { value: 'ollama' } });
    fireEvent.click(screen.getByTestId('llm-provider-test'));
    await waitFor(() => expect(screen.getByTestId('llm-not-active-notice')).toBeInTheDocument());
    expect(screen.getByText(/not yet used for translation/)).toBeInTheDocument();
  });

  it('a failed implicit save aborts Test — no probe against the stale stored config', async () => {
    const fetchMock = mockFetchSequence(
      { body: PROVIDERS }, // mount GET
      { status: 500, body: { detail: 'disk full' } }, // save PUT fails
      // nothing else queued: the /test POST must never fire
    );
    global.fetch = fetchMock;
    render(<LLMProvidersPanel />);
    fireEvent.click(await screen.findByTestId('llm-provider-test'));

    // The save error is the single message…
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/disk full/));
    // …with no contradictory green "Test ok" badge and no /test round-trip.
    expect(screen.queryByText(/ok —/)).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/test'))).toBe(false);
  });

  it('a failed implicit save aborts Fetch models', async () => {
    const fetchMock = mockFetchSequence(
      { body: PROVIDERS }, // mount GET
      { status: 500, body: { detail: 'disk full' } }, // save PUT fails
    );
    global.fetch = fetchMock;
    render(<LLMProvidersPanel />);
    fireEvent.click(await screen.findByTestId('llm-provider-models'));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/disk full/));
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/models'))).toBe(false);
  });

  it('initial-load failure offers a Retry that refetches (no remount needed)', async () => {
    const fetchMock = mockFetchSequence(
      { status: 500, body: { detail: 'backend hiccup' } }, // mount GET fails
      { body: PROVIDERS }, // retry GET succeeds
    );
    global.fetch = fetchMock;
    render(<LLMProvidersPanel />);

    const retry = await screen.findByTestId('llm-provider-retry');
    expect(screen.getByRole('alert')).toHaveTextContent(/backend hiccup/);
    fireEvent.click(retry);

    const select = await screen.findByTestId('llm-provider-select');
    await waitFor(() => expect(select.value).toBe('groq'));
    expect(screen.queryByTestId('llm-provider-retry')).toBeNull();
  });

  it('no notice when the saved provider IS the active one', async () => {
    global.fetch = mockFetchSequence(
      { body: PROVIDERS }, // mount GET (active: groq)
      { body: {} }, // save PUT (groq)
      { body: PROVIDERS }, // refresh GET — groq active
      { body: { ok: true, model: 'llama-3.3-70b', reply: 'ok', latency_ms: 412 } }, // test POST
    );
    render(<LLMProvidersPanel />);
    fireEvent.click(await screen.findByTestId('llm-provider-test'));
    await waitFor(() => expect(screen.getByText(/llama-3\.3-70b · 412 ms/)).toBeInTheDocument());
    expect(screen.queryByTestId('llm-not-active-notice')).toBeNull();
  });
});
