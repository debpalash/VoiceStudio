import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

// Deterministic confirm: tests flip `confirmAnswer` per case (the real
// askConfirm routes through the Tauri dialog plugin / window.confirm).
let confirmAnswer = true;
const askConfirmMock = vi.fn(async () => confirmAnswer);
vi.mock('./native', () => ({
  isTauri: () => false,
  askConfirm: (...args) => askConfirmMock(...args),
}));

const PROFILES = [
  { id: 'morgan', name: 'Morgan' },
  { id: 'scarlett', name: 'Scarlett' },
];
vi.mock('../../api/profiles', () => ({
  listProfiles: vi.fn(async () => PROFILES),
}));

import MCPBindingsPanel from './MCPBindingsPanel';

const BINDINGS = [
  { client_id: 'claude-code', label: 'Claude Code', profile_id: 'morgan' },
  { client_id: 'cursor', label: null, profile_id: null },
];

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

describe('MCPBindingsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    confirmAnswer = true;
  });

  it('renders bindings with label (falling back to client id) and profile badge', async () => {
    global.fetch = mockFetchSequence({ body: BINDINGS });
    render(<MCPBindingsPanel />);
    expect(await screen.findByText('Claude Code')).toBeInTheDocument();
    // Unlabelled binding falls back to its client id as the row title.
    expect(screen.getByText('cursor')).toBeInTheDocument();
    // Profile badge on the bound row ("Morgan" also exists as a select option).
    expect(screen.getAllByText('Morgan').some((el) => el.tagName !== 'OPTION')).toBe(true);
  });

  it('empty list shows the first-run guidance instead of a bare add row', async () => {
    global.fetch = mockFetchSequence({ body: [] });
    render(<MCPBindingsPanel />);
    expect(await screen.findByTestId('mcp-empty')).toHaveTextContent(/No bindings yet/);
  });

  it('load failure surfaces the error (and no stale empty-state hint)', async () => {
    // HTTP 500 (not a transport error) — apiFetch never retries HTTP errors,
    // so the test stays fast and deterministic.
    global.fetch = mockFetchSequence({ status: 500, body: { detail: 'boom' } });
    render(<MCPBindingsPanel />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.queryByTestId('mcp-empty')).toBeNull();
  });

  it('Add binding PUTs client id + optional label + profile, then refreshes', async () => {
    const fetchMock = mockFetchSequence(
      { body: [] }, // mount GET
      { body: { client_id: 'cline', label: 'Cline', profile_id: 'scarlett' } }, // PUT
      { body: [{ client_id: 'cline', label: 'Cline', profile_id: 'scarlett' }] }, // refresh GET
    );
    global.fetch = fetchMock;
    render(<MCPBindingsPanel />);
    await screen.findByTestId('mcp-empty');

    fireEvent.change(screen.getByTestId('mcp-client-id'), { target: { value: '  cline  ' } });
    fireEvent.change(screen.getByTestId('mcp-label'), { target: { value: 'Cline' } });
    fireEvent.change(screen.getByTestId('mcp-profile'), { target: { value: 'scarlett' } });
    fireEvent.click(screen.getByTestId('mcp-add'));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === 'PUT');
      expect(put).toBeTruthy();
      expect(put[0]).toMatch(/\/api\/mcp\/bindings$/);
      expect(JSON.parse(put[1].body)).toEqual({
        client_id: 'cline',
        label: 'Cline',
        profile_id: 'scarlett',
      });
    });
    // Inputs reset after a successful add; the new row renders.
    await screen.findByText('Cline');
    expect(screen.getByTestId('mcp-client-id').value).toBe('');
  });

  it('Add button is disabled with an empty client id', async () => {
    global.fetch = mockFetchSequence({ body: [] });
    render(<MCPBindingsPanel />);
    await screen.findByTestId('mcp-empty');
    expect(screen.getByTestId('mcp-add')).toBeDisabled();
  });

  it('delete asks for confirmation and DELETEs on confirm', async () => {
    const fetchMock = mockFetchSequence(
      { body: BINDINGS }, // mount GET
      { body: { deleted: 'cursor' } }, // DELETE
      { body: [BINDINGS[0]] }, // refresh GET
    );
    global.fetch = fetchMock;
    render(<MCPBindingsPanel />);
    fireEvent.click(await screen.findByTestId('mcp-del-cursor'));

    await waitFor(() => {
      expect(askConfirmMock).toHaveBeenCalledWith(
        expect.stringContaining('cursor'),
        expect.any(String),
      );
      const del = fetchMock.mock.calls.find(([, opts]) => opts?.method === 'DELETE');
      expect(del).toBeTruthy();
      expect(del[0]).toMatch(/\/api\/mcp\/bindings\/cursor$/);
    });
    await waitFor(() => expect(screen.queryByTestId('mcp-del-cursor')).toBeNull());
  });

  it('declining the confirmation sends no DELETE', async () => {
    confirmAnswer = false;
    const fetchMock = mockFetchSequence({ body: BINDINGS });
    global.fetch = fetchMock;
    render(<MCPBindingsPanel />);
    fireEvent.click(await screen.findByTestId('mcp-del-cursor'));
    await waitFor(() => expect(askConfirmMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls.find(([, opts]) => opts?.method === 'DELETE')).toBeUndefined();
  });

  it('a failed delete (already gone: 404) still re-syncs the list', async () => {
    const fetchMock = mockFetchSequence(
      { body: BINDINGS }, // mount GET
      { status: 404, body: { detail: 'No binding for that client id' } }, // DELETE fails
      { body: [BINDINGS[0]] }, // refresh GET — row is gone server-side
    );
    global.fetch = fetchMock;
    render(<MCPBindingsPanel />);
    fireEvent.click(await screen.findByTestId('mcp-del-cursor'));
    // The stale row disappears even though the DELETE errored.
    await waitFor(() => expect(screen.queryByTestId('mcp-del-cursor')).toBeNull());
  });

  it('controls carry accessible names', async () => {
    global.fetch = mockFetchSequence({ body: BINDINGS });
    render(<MCPBindingsPanel />);
    await screen.findByText('Claude Code');
    expect(screen.getByLabelText('Client ID')).toBeInTheDocument();
    expect(screen.getByLabelText('Label')).toBeInTheDocument();
    expect(screen.getByLabelText('Voice profile')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove cursor' })).toBeInTheDocument();
  });
});
