import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('../../api/client', () => ({
  LS_BACKEND_URL: 'ov_backend_url',
  LS_API_KEY: 'ov_api_key',
  API: 'http://127.0.0.1:3900',
}));

const authMocks = vi.hoisted(() => ({
  exchangeApiKey: vi.fn(),
  clearAdminSession: vi.fn(),
}));
vi.mock('../../api/authSession', async (importOriginal) => ({
  ...(await importOriginal()),
  exchangeApiKey: authMocks.exchangeApiKey,
  clearAdminSession: authMocks.clearAdminSession,
}));

// Shared confirmation dialog (Tauri-aware) — controlled per test.
const { askConfirm } = vi.hoisted(() => ({ askConfirm: vi.fn() }));
vi.mock('../../utils/dialog', () => ({ askConfirm }));

import toast from 'react-hot-toast';
import RemoteBackendPanel, { isValidBackendUrl } from './RemoteBackendPanel';

const healthResponse = (version) =>
  new Response(JSON.stringify({ status: 'ok', version, device: 'cuda' }), { status: 200 });

describe('isValidBackendUrl', () => {
  it('accepts absolute http(s) URLs only', () => {
    expect(isValidBackendUrl('http://gpu-box:3900')).toBe(true);
    expect(isValidBackendUrl('https://gpu-box.tailnet.ts.net:3900')).toBe(true);
    // The classic typo: schemeless host:port parses as a URL with a bogus
    // protocol — it must NOT be accepted (it bricks every call post-reload).
    expect(isValidBackendUrl('gpu-box:3900')).toBe(false);
    expect(isValidBackendUrl('not a url')).toBe(false);
    expect(isValidBackendUrl('ftp://gpu-box')).toBe(false);
    expect(isValidBackendUrl('https://user:secret@gpu-box:3900')).toBe(false);
    expect(isValidBackendUrl('https://gpu-box:3900?key=secret')).toBe(false);
    expect(isValidBackendUrl('')).toBe(false);
  });
});

describe('RemoteBackendPanel', () => {
  let reload;

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    authMocks.exchangeApiKey.mockResolvedValue({
      transport: 'bearer',
      expiresAt: Date.now() / 1000 + 60,
    });
    authMocks.clearAdminSession.mockImplementation(() =>
      sessionStorage.removeItem('ov_admin_session'),
    );
    reload = vi.fn();
  });

  const setUrl = (value) =>
    fireEvent.change(screen.getByTestId('remote-backend-url'), { target: { value } });
  const setKey = (value) =>
    fireEvent.change(screen.getByTestId('remote-backend-key'), { target: { value } });
  const clickSave = () => fireEvent.click(screen.getByTestId('remote-backend-save'));

  it('rejects an invalid URL instead of saving and reloading into a broken app', async () => {
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('gpu-box:3900');
    clickSave();

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(reload).not.toHaveBeenCalled();
    expect(localStorage.getItem('ov_backend_url')).toBeNull();
    expect(askConfirm).not.toHaveBeenCalled();
  });

  it('asks for confirmation before saving an unverified URL, and aborts on decline', async () => {
    askConfirm.mockResolvedValue(false);
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('http://gpu-box:3900');
    clickSave();

    await waitFor(() => expect(askConfirm).toHaveBeenCalled());
    expect(reload).not.toHaveBeenCalled();
    expect(localStorage.getItem('ov_backend_url')).toBeNull();
  });

  it('saves and reloads an unverified URL when the user confirms', async () => {
    askConfirm.mockResolvedValue(true);
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('http://gpu-box:3900/');
    clickSave();

    await waitFor(() => expect(reload).toHaveBeenCalled());
    // Trailing slashes are normalized before persisting.
    expect(localStorage.getItem('ov_backend_url')).toBe('http://gpu-box:3900');
  });

  it('reports a localized save failure when the requested reload rejects', async () => {
    askConfirm.mockResolvedValue(true);
    reload.mockRejectedValue(new Error('reload unavailable'));
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('http://gpu-box:3900');

    clickSave();

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/Save failed/i)),
    );
    expect(reload).toHaveBeenCalledOnce();
  });

  it('skips the confirmation when the exact URL passed a connection test', async () => {
    global.fetch = vi.fn().mockResolvedValue(healthResponse('0.3.15'));
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('http://gpu-box:3900');

    fireEvent.click(screen.getByTestId('remote-backend-test'));
    await screen.findByText('OK — 0.3.15 on cuda');

    clickSave();
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(askConfirm).not.toHaveBeenCalled();
    expect(localStorage.getItem('ov_backend_url')).toBe('http://gpu-box:3900');
  });

  it('exchanges a test credential only after the public health probe succeeds', async () => {
    global.fetch = vi.fn().mockResolvedValue(healthResponse('0.4.2'));
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('http://gpu-box:3900');
    setKey('master-secret');

    fireEvent.click(screen.getByTestId('remote-backend-test'));

    await screen.findByText('OK — 0.4.2 on cuda');
    expect(authMocks.exchangeApiKey).toHaveBeenCalledWith('master-secret', {
      apiBase: 'http://gpu-box:3900',
    });
    expect(screen.getByTestId('remote-backend-key')).toHaveValue('');
    expect(localStorage.getItem('ov_api_key')).toBeNull();
    expect(global.fetch.mock.invocationCallOrder[0]).toBeLessThan(
      authMocks.exchangeApiKey.mock.invocationCallOrder[0],
    );
  });

  it('does not exchange or retain a credential when the health probe fails', async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('http://gpu-box:3900');
    setKey('master-secret');

    fireEvent.click(screen.getByTestId('remote-backend-test'));

    await screen.findByText(/Failed/);
    expect(authMocks.exchangeApiKey).not.toHaveBeenCalled();
    expect(screen.getByTestId('remote-backend-key')).toHaveValue('');
    expect(localStorage.getItem('ov_api_key')).toBeNull();
  });

  it('blocks save and reload when credential exchange fails', async () => {
    askConfirm.mockResolvedValue(true);
    authMocks.exchangeApiKey.mockRejectedValueOnce(
      Object.assign(new Error('generic'), { status: 401 }),
    );
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('http://gpu-box:3900');
    setKey('master-secret');

    clickSave();

    await screen.findByText(/HTTP 401/);
    expect(localStorage.getItem('ov_backend_url')).toBeNull();
    expect(localStorage.getItem('ov_api_key')).toBeNull();
    expect(screen.getByTestId('remote-backend-key')).toHaveValue('');
    expect(reload).not.toHaveBeenCalled();
  });

  it('does not exchange the same credential twice after Test succeeds', async () => {
    global.fetch = vi.fn().mockResolvedValue(healthResponse('0.4.2'));
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('http://gpu-box:3900');
    setKey('master-secret');
    fireEvent.click(screen.getByTestId('remote-backend-test'));
    await screen.findByText('OK — 0.4.2 on cuda');

    clickSave();
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    expect(authMocks.exchangeApiKey).toHaveBeenCalledOnce();
    expect(askConfirm).not.toHaveBeenCalled();
  });

  it('classifies a wrong 7443 service and succeeds when retried with the HTTP API', async () => {
    global.fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(healthResponse('0.4.2'));
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('https://gpu-box:7443');
    fireEvent.click(screen.getByTestId('remote-backend-test'));
    await screen.findByText(/7443/);

    setUrl('http://gpu-box:3900');
    fireEvent.click(screen.getByTestId('remote-backend-test'));
    await screen.findByText('OK — 0.4.2 on cuda');
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it('offers an explicit disable action that clears the remote URL and key', async () => {
    localStorage.setItem('ov_backend_url', 'http://old-box:3900');
    localStorage.setItem('ov_api_key', 'secret');
    render(<RemoteBackendPanel reload={reload} />);
    fireEvent.click(screen.getByTestId('remote-backend-disable'));
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    expect(localStorage.getItem('ov_backend_url')).toBeNull();
    expect(localStorage.getItem('ov_api_key')).toBeNull();
    expect(authMocks.clearAdminSession).toHaveBeenCalled();
  });

  it('clears a restored session before switching targets without a new key', async () => {
    localStorage.setItem('ov_backend_url', 'http://old-box:3900');
    sessionStorage.setItem(
      'ov_admin_session',
      JSON.stringify({
        token: `ovs_admin_session_${'S'.repeat(43)}`,
        expiresAt: Date.now() / 1000 + 3600,
        apiBase: 'http://old-box:3900',
      }),
    );
    askConfirm.mockResolvedValue(true);
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('http://new-box:3900');

    clickSave();

    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    expect(authMocks.exchangeApiKey).not.toHaveBeenCalled();
    expect(authMocks.clearAdminSession).toHaveBeenCalledOnce();
    expect(localStorage.getItem('ov_backend_url')).toBe('http://new-box:3900');
  });

  it('clears both settings and reloads without confirmation when the URL is emptied', async () => {
    localStorage.setItem('ov_backend_url', 'http://old-box:3900');
    localStorage.setItem('ov_api_key', 'k');
    render(<RemoteBackendPanel reload={reload} />);
    setUrl('');
    setKey('');
    clickSave();

    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(askConfirm).not.toHaveBeenCalled();
    expect(localStorage.getItem('ov_backend_url')).toBeNull();
    expect(localStorage.getItem('ov_api_key')).toBeNull();
    expect(authMocks.clearAdminSession).toHaveBeenCalled();
  });

  it('never pre-fills the master credential from legacy localStorage', () => {
    localStorage.setItem('ov_api_key', 'legacy-master');
    render(<RemoteBackendPanel reload={reload} />);
    expect(screen.getByTestId('remote-backend-key')).toHaveValue('');
  });

  it('renders localized strings and labelled inputs (no hardcoded-English bypass)', () => {
    render(<RemoteBackendPanel reload={reload} />);
    // Strings resolve through i18n (en locale in tests) …
    expect(screen.getByText('Remote backend')).toBeInTheDocument();
    expect(screen.getByText('Test connection')).toBeInTheDocument();
    expect(screen.getByText('Save & reload')).toBeInTheDocument();
    // … and both inputs carry accessible names.
    expect(screen.getByLabelText('Backend URL')).toBeInTheDocument();
    expect(screen.getByLabelText('API key')).toBeInTheDocument();
  });
});
