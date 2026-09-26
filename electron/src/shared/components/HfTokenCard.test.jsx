import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import HfTokenCard from './HfTokenCard';

const STATE_NONE_ACTIVE = {
  active: null,
  sources: [
    {
      source: 'app',
      set: false,
      masked: null,
      whoami_user: null,
      whoami_ok: false,
    },
    {
      source: 'env',
      set: false,
      masked: null,
      whoami_user: null,
      whoami_ok: false,
    },
    {
      source: 'hf-cli',
      set: false,
      masked: null,
      whoami_user: null,
      whoami_ok: false,
    },
  ],
};

// Mirrors the live report (#FR-006): `hf-cli` already resolved and validated.
const STATE_HF_CLI_ACTIVE = {
  active: null,
  sources: [
    {
      source: 'app',
      set: false,
      masked: null,
      whoami_user: null,
      whoami_ok: false,
    },
    {
      source: 'env',
      set: false,
      masked: null,
      whoami_user: null,
      whoami_ok: false,
    },
    {
      source: 'hf-cli',
      set: true,
      masked: 'hf_…Sfb',
      whoami_user: null,
      whoami_ok: null,
    },
  ],
};

function mockFetchOnce(payload, status = 200) {
  return vi.fn().mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  });
}

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

describe('HfTokenCard', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the add-token pitch when no token is active anywhere', async () => {
    global.fetch = mockFetchOnce(STATE_NONE_ACTIVE);
    render(<HfTokenCard />);
    await waitFor(() => expect(screen.getByPlaceholderText(/hf_/)).toBeInTheDocument());
    expect(
      screen.getByText(/Speed up downloads with a free Hugging Face token/),
    ).toBeInTheDocument();
  });

  it('does NOT pitch a new token once one is locally configured without validation (#FR-006)', async () => {
    global.fetch = mockFetchOnce(STATE_HF_CLI_ACTIVE);
    render(<HfTokenCard />);

    // The satisfied state names the masked value; the blind "paste a token"
    // input must never appear alongside it.
    await waitFor(() => expect(screen.getByText(/hf_…Sfb/)).toBeInTheDocument());
    expect(screen.queryByPlaceholderText(/hf_/)).toBeNull();
    expect(screen.queryByText(/Speed up downloads with a free Hugging Face token/)).toBeNull();
  });

  it('replacing an active token requires an explicit "Replace…" click, not a blind paste-and-Save', async () => {
    global.fetch = mockFetchOnce(STATE_HF_CLI_ACTIVE);
    render(<HfTokenCard />);
    await waitFor(() => expect(screen.getByText(/hf_…Sfb/)).toBeInTheDocument());

    // Still no paste field until the user opts in.
    expect(screen.queryByPlaceholderText(/hf_/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /replace/i }));

    // Now the field appears, alongside a warning that this overwrites the
    // token above — the deliberate-action gate the fix adds.
    await waitFor(() => expect(screen.getByPlaceholderText(/hf_/)).toBeInTheDocument());
    expect(screen.getByText(/replaces the token saved for this app/i)).toBeInTheDocument();
  });

  it('keeps the overwrite warning visible on narrow screens (unlike the dismissable pitch)', async () => {
    global.fetch = mockFetchOnce(STATE_HF_CLI_ACTIVE);
    render(<HfTokenCard />);
    await waitFor(() => expect(screen.getByText(/hf_…Sfb/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /replace/i }));

    const warning = await screen.findByText(/replaces the token saved for this app/i);
    // The default pitch is allowed to hide at <=560px; the overwrite safety
    // warning must not carry that same responsive-hide class, or a user on a
    // narrow viewport can replace a working token without ever seeing it.
    expect(warning.className).not.toMatch(/max-\[560px\]:hidden/);
  });

  it('shows a neutral checking placeholder while state is loading, never the pitch', async () => {
    let resolveFetch;
    global.fetch = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        }),
    );
    render(<HfTokenCard />);

    expect(screen.queryByPlaceholderText(/hf_/)).toBeNull();
    expect(screen.queryByText(/Speed up downloads with a free Hugging Face token/)).toBeNull();
    expect(screen.getByText(/checking/i)).toBeInTheDocument();

    await waitFor(() => expect(global.fetch).toHaveBeenCalledOnce());
    resolveFetch({
      ok: true,
      status: 200,
      json: async () => STATE_NONE_ACTIVE,
      text: async () => JSON.stringify(STATE_NONE_ACTIVE),
    });
    await waitFor(() => expect(screen.getByPlaceholderText(/hf_/)).toBeInTheDocument());
  });

  it('keeps Save hidden after a failed state read until Retry discovers the existing token', async () => {
    const failed = mockFetchOnce({ detail: 'private server failure' }, 503);
    failed.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => STATE_HF_CLI_ACTIVE,
    });
    global.fetch = failed;
    render(<HfTokenCard />);
    const retry = await screen.findByRole('button', { name: 'Retry' });
    expect(screen.queryByPlaceholderText(/hf_/)).toBeNull();
    expect(screen.queryByRole('button', { name: /^save$/i })).toBeNull();
    expect(screen.queryByText(/private server failure/)).toBeNull();
    fireEvent.click(retry);
    await screen.findByText(/hf_…Sfb/);
    expect(screen.queryByPlaceholderText(/hf_/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /replace/i }));
    expect(screen.getByText(/replaces the token saved for this app/i)).toBeInTheDocument();
  });

  it('saves an app token through the canonical Settings endpoint without claiming validation', async () => {
    const fetchMock = mockFetchSequence(
      { status: 200, body: STATE_NONE_ACTIVE }, // GET state
      { status: 200, body: STATE_HF_CLI_ACTIVE }, // POST app token
    );
    global.fetch = fetchMock;

    render(<HfTokenCard />);
    const input = await screen.findByPlaceholderText(/hf_/);
    fireEvent.change(input, { target: { value: 'hf_newtoken123' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(([, opts]) => opts?.method === 'POST');
      expect(postCall).toBeTruthy();
      expect(postCall[0]).toMatch(/\/api\/settings\/hf-token$/);
      expect(JSON.parse(postCall[1].body)).toEqual({ token: 'hf_newtoken123' });
    });
    await screen.findByText('Hugging Face token saved');
    expect(screen.queryByText(/downloads are now faster/)).toBeNull();
  });
  it.each(['app', 'env', 'hf-cli'])(
    'replaces the used token from %s through the app source',
    async (source) => {
      const state = {
        active: null,
        sources: STATE_NONE_ACTIVE.sources.map((row) => ({
          ...row,
          set: row.source === source,
          masked: row.source === source ? 'hf_…old' : null,
        })),
      };
      global.fetch = mockFetchSequence(
        { status: 200, body: state },
        { status: 200, body: STATE_NONE_ACTIVE },
      );
      render(<HfTokenCard />);
      await screen.findByText(/hf_…old/);
      expect(screen.queryByPlaceholderText(/hf_/)).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: /replace/i }));
      fireEvent.change(screen.getByPlaceholderText(/hf_/), {
        target: { value: ' hf_replacement ' },
      });
      fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
      await screen.findByText('Hugging Face token saved');
      const [url, options] = global.fetch.mock.calls.find(([, opts]) => opts?.method === 'POST');
      expect(url).toMatch(/\/api\/settings\/hf-token$/);
      expect(JSON.parse(options.body)).toEqual({ token: 'hf_replacement' });
    },
  );

  it('retains the replacement warning and token after a failed save so it can be retried', async () => {
    global.fetch = mockFetchSequence(
      { status: 200, body: STATE_HF_CLI_ACTIVE },
      { status: 500, body: { detail: 'private server failure' } },
      { status: 200, body: STATE_NONE_ACTIVE },
    );
    render(<HfTokenCard />);
    await screen.findByText(/hf_…Sfb/);
    fireEvent.click(screen.getByRole('button', { name: /replace/i }));
    fireEvent.change(screen.getByPlaceholderText(/hf_/), {
      target: { value: 'hf_replacement' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await screen.findByText(/Could not save the token/);
    expect(screen.getByPlaceholderText(/hf_/)).toHaveValue('hf_replacement');
    expect(screen.getByText(/replaces the token saved for this app/i)).toBeInTheDocument();
    expect(screen.queryByText(/private server failure/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await screen.findByText('Hugging Face token saved');
  });
  it.each([
    null,
    {},
    { sources: [] },
    { sources: [null] },
    { sources: [...STATE_NONE_ACTIVE.sources, null] },
  ])('keeps malformed token state gated: %j', async (state) => {
    global.fetch = mockFetchOnce(state);
    render(<HfTokenCard />);
    await screen.findByRole('button', { name: 'Retry' });
    expect(screen.queryByPlaceholderText(/hf_/)).toBeNull();
    expect(screen.queryByRole('button', { name: /^save$/i })).toBeNull();
  });

  it('waits for Retry to finish before showing the empty-state form', async () => {
    let resolveRetry;
    global.fetch = mockFetchOnce({ detail: 'failed' }, 503);
    global.fetch.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRetry = resolve;
        }),
    );
    render(<HfTokenCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(screen.getByText(/checking/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/hf_/)).toBeNull();
    await waitFor(() => expect(resolveRetry).toBeTypeOf('function'));
    resolveRetry({
      ok: true,
      status: 200,
      json: async () => STATE_NONE_ACTIVE,
    });
    await screen.findByPlaceholderText(/hf_/);
  });

  it('does not issue duplicate saves or cancel an in-flight replacement', async () => {
    let resolveSave;
    global.fetch = mockFetchOnce(STATE_HF_CLI_ACTIVE);
    global.fetch.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }),
    );
    render(<HfTokenCard />);
    fireEvent.click(await screen.findByRole('button', { name: /replace/i }));
    fireEvent.change(screen.getByPlaceholderText(/hf_/), {
      target: { value: 'hf_replacement' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(screen.getByRole('button', { name: /saving/i })).toBeDisabled();
    expect(screen.getByPlaceholderText(/hf_/)).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText(/hf_/), { target: { value: 'hf_racing_edit' } });
    expect(screen.getByPlaceholderText(/hf_/)).toHaveValue('hf_replacement');
    expect(screen.getByRole('button', { name: /cancel/i })).toBeDisabled();
    fireEvent.keyDown(screen.getByPlaceholderText(/hf_/), { key: 'Enter' });
    await waitFor(() => expect(resolveSave).toBeTypeOf('function'));
    expect(global.fetch.mock.calls.filter(([, opts]) => opts?.method === 'POST')).toHaveLength(1);
    resolveSave({ ok: true, status: 200, json: async () => STATE_NONE_ACTIVE });
    await screen.findByText('Hugging Face token saved');
  });
});
