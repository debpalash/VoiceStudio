import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import i18n from '../../i18n';

const STATE_THREE_UNSET = {
  active: null,
  sources: [
    { source: 'app', set: false, masked: null, whoami_user: null, whoami_ok: false },
    { source: 'env', set: false, masked: null, whoami_user: null, whoami_ok: false },
    { source: 'hf-cli', set: false, masked: null, whoami_user: null, whoami_ok: false },
  ],
};

const STATE_APP_ACTIVE = {
  active: 'app',
  sources: [
    { source: 'app', set: true, masked: 'hf_…abc', whoami_user: 'alice', whoami_ok: true },
    { source: 'env', set: false, masked: null, whoami_user: null, whoami_ok: false },
    { source: 'hf-cli', set: false, masked: null, whoami_user: null, whoami_ok: false },
  ],
};

const STATE_ENV_ACTIVE = {
  active: 'env',
  sources: [
    { source: 'app', set: false, masked: null, whoami_user: null, whoami_ok: false },
    { source: 'env', set: true, masked: 'hf_…xyz', whoami_user: 'bob', whoami_ok: true },
    { source: 'hf-cli', set: false, masked: null, whoami_user: null, whoami_ok: false },
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

import ApiKeysPanel from './ApiKeysPanel';

describe('ApiKeysPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows local token presence as untested until Test now is selected', async () => {
    const local = {
      active: null,
      sources: [
        { source: 'app', set: true, masked: 'hf_…abc', whoami_ok: null, whoami_user: null },
      ],
    };
    const fetchMock = mockFetchSequence(
      { status: 200, body: local },
      { status: 200, body: STATE_APP_ACTIVE },
    );
    global.fetch = fetchMock;
    render(<ApiKeysPanel />);
    expect(await screen.findByText('Not tested')).toBeInTheDocument();
    expect(screen.queryByText('whoami failed')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Test now' }));
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)[0]).toContain('?fresh=1'));
    expect(await screen.findByText('alice')).toBeInTheDocument();
  });
  it('renders 3 source rows after mount', async () => {
    global.fetch = mockFetchOnce(STATE_THREE_UNSET);
    const { container } = render(<ApiKeysPanel />);
    await waitFor(() => {
      const rows = container.querySelectorAll('.apikeys-row');
      expect(rows.length).toBe(3);
      expect(container.querySelector('[data-source="app"]')).not.toBeNull();
      expect(container.querySelector('[data-source="env"]')).not.toBeNull();
      expect(container.querySelector('[data-source="hf-cli"]')).not.toBeNull();
    });
  });

  it('shows the Active badge on the row matching state.active', async () => {
    global.fetch = mockFetchOnce(STATE_APP_ACTIVE);
    const { container } = render(<ApiKeysPanel />);
    await waitFor(() => {
      const appRow = container.querySelector('[data-source="app"]');
      expect(appRow).not.toBeNull();
      expect(appRow.classList.contains('apikeys-row--active')).toBe(true);
      const badge = appRow.querySelector('.apikeys-badge--active');
      expect(badge?.textContent).toMatch(/active/i);
    });
  });

  it('moves the Active badge when the env source is active', async () => {
    global.fetch = mockFetchOnce(STATE_ENV_ACTIVE);
    const { container } = render(<ApiKeysPanel />);
    await waitFor(() => {
      const envRow = container.querySelector('[data-source="env"]');
      expect(envRow?.classList.contains('apikeys-row--active')).toBe(true);
      const appRow = container.querySelector('[data-source="app"]');
      expect(appRow?.classList.contains('apikeys-row--active')).toBe(false);
    });
  });

  it('Save button POSTs the entered token and refetches state', async () => {
    const fetchMock = mockFetchSequence(
      { status: 200, body: STATE_THREE_UNSET }, // initial GET
      { status: 200, body: STATE_APP_ACTIVE }, // POST returns updated state
      { status: 200, body: STATE_APP_ACTIVE }, // GET after save
    );
    global.fetch = fetchMock;

    render(<ApiKeysPanel />);
    await waitFor(() => screen.getByPlaceholderText(/hf_/));

    const input = screen.getByPlaceholderText(/hf_/);
    fireEvent.change(input, { target: { value: 'hf_newtoken123' } });
    const saveBtn = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      const calls = fetchMock.mock.calls;
      // Find the POST call
      const postCall = calls.find(([_url, opts]) => opts && opts.method === 'POST');
      expect(postCall).toBeTruthy();
      const [url, init] = postCall;
      expect(url).toMatch(/\/api\/settings\/hf-token$/);
      const body = JSON.parse(init.body);
      expect(body).toEqual({ token: 'hf_newtoken123' });
    });
  });

  it('Clear button shows confirmation dialog and DELETEs on confirm', async () => {
    const fetchMock = mockFetchSequence(
      { status: 200, body: STATE_APP_ACTIVE }, // initial GET
      { status: 200, body: STATE_THREE_UNSET }, // DELETE response
      { status: 200, body: STATE_THREE_UNSET }, // refetch GET
    );
    global.fetch = fetchMock;

    render(<ApiKeysPanel />);
    await waitFor(() => screen.getByPlaceholderText(/hf_/));

    const clearBtn = screen.getByRole('button', { name: /^clear$/i });
    fireEvent.click(clearBtn);

    // Dialog appears
    expect(screen.getByText(/Clear the App-source HuggingFace token/)).toBeInTheDocument();
    const confirmBtn = screen.getByRole('button', { name: /clear token/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      const calls = fetchMock.mock.calls;
      const del = calls.find(([_u, opts]) => opts && opts.method === 'DELETE');
      expect(del).toBeTruthy();
      expect(del[0]).toMatch(/\/api\/settings\/hf-token/);
      // also_clear_hf_cli default is false → no query string
      expect(del[0]).not.toMatch(/also_clear_hf_cli=true/);
    });
  });

  it('keeps failed clear open for retry and shows the localized error', async () => {
    const previousLanguage = i18n.language;
    await i18n.changeLanguage('ko');
    await waitFor(() => expect(i18n.hasResourceBundle('ko', 'translation')).toBe(true));
    expect(i18n.t('common.error')).not.toBe('Something went wrong');
    const fetchMock = mockFetchSequence(
      { status: 200, body: STATE_APP_ACTIVE },
      { status: 500, body: { detail: 'Failed to clear local Hugging Face token files' } },
      { status: 200, body: STATE_THREE_UNSET },
      { status: 200, body: STATE_THREE_UNSET },
    );
    global.fetch = fetchMock;
    const { unmount } = render(<ApiKeysPanel />);
    try {
      fireEvent.click(
        await screen.findByRole('button', { name: i18n.t('settings.hf_token_clear_short') }),
      );
      const checkbox = screen.getByRole('checkbox');
      fireEvent.click(checkbox);
      const confirm = screen.getByRole('button', { name: i18n.t('settings.hf_token_clear_btn') });
      fireEvent.click(confirm);
      expect(await screen.findByText(i18n.t('common.error'))).toBeInTheDocument();
      expect(
        screen.queryByText('Failed to clear local Hugging Face token files'),
      ).not.toBeInTheDocument();
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(checkbox).toBeChecked();
      expect(confirm).toBeEnabled();
      expect(fetchMock).toHaveBeenCalledTimes(2);
      fireEvent.click(confirm);
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(fetchMock.mock.calls[2][0]).toContain('also_clear_hf_cli=true');
      expect(screen.queryByText(i18n.t('common.error'))).not.toBeInTheDocument();
    } finally {
      unmount();
      await i18n.changeLanguage(previousLanguage);
    }
  });

  it('"Test now" busts the whoami cache (?fresh=1); plain mounts stay cached', async () => {
    const fetchMock = mockFetchSequence(
      { status: 200, body: STATE_THREE_UNSET },
      { status: 200, body: STATE_THREE_UNSET },
    );
    global.fetch = fetchMock;

    render(<ApiKeysPanel />);
    await waitFor(() => screen.getByPlaceholderText(/hf_/));
    // Mount GET keeps the backend cache — no fresh param.
    expect(fetchMock.mock.calls[0][0]).not.toMatch(/fresh=1/);

    const testBtn = screen.getByRole('button', { name: /test now/i });
    fireEvent.click(testBtn);

    // The button claims to re-run whoami, so it must actually bypass the
    // backend's 300s validation cache.
    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
      expect(fetchMock.mock.calls[1][0]).toMatch(/\/api\/settings\/hf-token\/state\?fresh=1$/);
    });
  });

  it('initial load shows a checking placeholder, never a false "not set" verdict', async () => {
    let resolveFetch;
    global.fetch = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const { container } = render(<ApiKeysPanel />);

    // While the GET is in flight: placeholder, no source rows, no verdicts.
    expect(screen.getByTestId('hf-token-loading')).toBeInTheDocument();
    expect(screen.queryByText(/not set/i)).toBeNull();
    expect(container.querySelectorAll('.apikeys-row').length).toBe(0);

    // API requests wait for the credential-scrubbing bootstrap before they
    // reach fetch, so do not assume the effect invokes fetch synchronously.
    await waitFor(() => expect(global.fetch).toHaveBeenCalledOnce());
    resolveFetch({
      ok: true,
      status: 200,
      json: async () => STATE_APP_ACTIVE,
      text: async () => JSON.stringify(STATE_APP_ACTIVE),
    });
    await waitFor(() => {
      expect(container.querySelectorAll('.apikeys-row').length).toBe(3);
      expect(screen.queryByTestId('hf-token-loading')).toBeNull();
    });
  });

  it('renders the sources as a valid ARIA list (no cell-less table)', async () => {
    global.fetch = mockFetchOnce(STATE_THREE_UNSET);
    render(<ApiKeysPanel />);
    const list = await screen.findByRole('list', { name: /HF token sources/i });
    expect(list.querySelectorAll('[role="listitem"]').length).toBe(3);
  });

  it('Enter while a save is in flight does not fire a duplicate POST', async () => {
    let resolvePost;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => STATE_THREE_UNSET,
        text: async () => JSON.stringify(STATE_THREE_UNSET),
      })
      .mockImplementation(
        () =>
          new Promise((resolve) => {
            resolvePost = resolve;
          }),
      );
    global.fetch = fetchMock;

    render(<ApiKeysPanel />);
    const input = await screen.findByPlaceholderText(/hf_/);
    fireEvent.change(input, { target: { value: 'hf_newtoken123' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.keyDown(input, { key: 'Enter' }); // Save button is disabled; Enter must be too.
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter(([, opts]) => opts?.method === 'POST');
      expect(posts.length).toBe(1);
    });
    resolvePost({
      ok: true,
      status: 200,
      json: async () => STATE_APP_ACTIVE,
      text: async () => JSON.stringify(STATE_APP_ACTIVE),
    });
  });
});
