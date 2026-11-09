// HfTokenCard — state-aware Hugging Face token card for the setup wizard.
// The resolver read (apiJson) and the save (apiPost) are mocked at the client
// boundary; everything else renders for real.
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({ apiJson: vi.fn(), apiPost: vi.fn() }));
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal()),
  apiJson: api.apiJson,
  apiPost: api.apiPost,
}));
vi.mock('../api/external', () => ({ openExternal: vi.fn() }));

import HfTokenCard from '../components/HfTokenCard';

const FIELD = 'Add a free Hugging Face token for faster downloads';
const PITCH = 'Speed up downloads with a free Hugging Face token';
const WARNING = 'This replaces the token saved for this app. It does not revoke the old token.';

const row = (source, set = false, masked = null) => ({
  source,
  set,
  masked,
  whoami_user: null,
  whoami_ok: null,
});
const stateWith = (active) => ({
  active: active ?? null,
  sources: ['app', 'env', 'hf-cli'].map((id) =>
    id === active ? row(id, true, 'hf_****1234') : row(id),
  ),
});
const NONE = stateWith(null);

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const field = () => screen.getByLabelText(FIELD);
const queryField = () => screen.queryByLabelText(FIELD);
const saveBtn = () => screen.getByRole('button', { name: 'Save' });

beforeEach(() => {
  api.apiJson.mockReset();
  api.apiPost.mockReset();
  api.apiPost.mockResolvedValue({});
});

async function renderWith(state) {
  api.apiJson.mockResolvedValue(state);
  render(<HfTokenCard />);
  await waitFor(() => expect(screen.queryByText('checking…')).toBeNull());
}

describe('HfTokenCard', () => {
  it('pitches and offers the input when no source has a token', async () => {
    await renderWith(NONE);
    expect(screen.getByText(PITCH)).toBeTruthy();
    expect(field()).toBeTruthy();
    expect(api.apiJson).toHaveBeenCalledWith('/system/hf-token/state');
  });

  it('shows the masked value for an active token instead of the form', async () => {
    await renderWith(stateWith('hf-cli'));
    expect(screen.getByText(/found in HuggingFace CLI — hf_\*\*\*\*1234/)).toBeTruthy();
    expect(queryField()).toBeNull();
    expect(screen.queryByText(PITCH)).toBeNull();
  });

  it('needs an explicit Replace click, then warns about the overwrite', async () => {
    await renderWith(stateWith('app'));
    expect(queryField()).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Replace…' }));
    expect(field()).toBeTruthy();
    expect(screen.getByText(WARNING)).toBeTruthy();
    expect(screen.queryByText(PITCH)).toBeNull();
  });

  it('keeps the narrow-screen hide class on the pitch but not on the warning', async () => {
    await renderWith(NONE);
    expect(screen.getByText(PITCH).className).toContain('max-[560px]:hidden');
    document.body.innerHTML = '';
    await renderWith(stateWith('env'));
    fireEvent.click(screen.getByRole('button', { name: 'Replace…' }));
    expect(screen.getByText(WARNING).className).not.toContain('max-[560px]:hidden');
  });

  it('shows only a checking placeholder while the state loads, and asks once', async () => {
    const pending = deferred();
    api.apiJson.mockReturnValue(pending.promise);
    render(<HfTokenCard />);
    expect(screen.getByText('checking…')).toBeTruthy();
    expect(queryField()).toBeNull();
    expect(screen.queryByText(PITCH)).toBeNull();
    expect(api.apiJson).toHaveBeenCalledTimes(1);
    pending.resolve(NONE);
    expect(await screen.findByText(PITCH)).toBeTruthy();
    expect(api.apiJson).toHaveBeenCalledTimes(1);
  });

  it('offers only Retry after a failed read, and Retry can find an active token', async () => {
    api.apiJson.mockRejectedValueOnce(new Error('upstream detail: 500 traceback'));
    render(<HfTokenCard />);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(queryField()).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(screen.queryByText(/traceback/)).toBeNull();

    api.apiJson.mockResolvedValueOnce(stateWith('env'));
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(/found in Environment variable/)).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows checking, never the form, between Retry and the new answer', async () => {
    api.apiJson.mockRejectedValueOnce(new Error('down'));
    render(<HfTokenCard />);
    const retry = await screen.findByRole('button', { name: 'Retry' });
    const next = deferred();
    api.apiJson.mockReturnValue(next.promise);
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByText('checking…')).toBeTruthy());
    expect(queryField()).toBeNull();
    expect(screen.queryByText(PITCH)).toBeNull();
    next.resolve(NONE);
    expect(await screen.findByText(PITCH)).toBeTruthy();
  });

  it.each([
    ['null', null],
    ['empty object', {}],
    ['empty sources', { sources: [] }],
    ['null row', { sources: [row('app'), null, row('hf-cli')] }],
    ['extra row', { sources: [...NONE.sources, row('app')] }],
    ['missing source', { sources: [row('app'), row('env'), row('env')] }],
    ['non-boolean set', { sources: [row('app'), row('env'), { source: 'hf-cli', set: 'yes' }] }],
  ])('treats a malformed state (%s) as a failure', async (_name, body) => {
    api.apiJson.mockResolvedValue(body);
    render(<HfTokenCard />);
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(queryField()).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('saves a new token with the exact body and does not promise speed', async () => {
    await renderWith(NONE);
    fireEvent.change(field(), { target: { value: '  hf_newtoken  ' } });
    fireEvent.click(saveBtn());
    await screen.findByText('Hugging Face token saved');
    expect(api.apiPost).toHaveBeenCalledTimes(1);
    expect(api.apiPost).toHaveBeenCalledWith('/api/settings/hf-token', { token: 'hf_newtoken' });
    expect(screen.queryByText(/faster/i)).toBeNull();
  });

  it.each(['app', 'env', 'hf-cli'])('replaces a token active in %s', async (source) => {
    await renderWith(stateWith(source));
    fireEvent.click(screen.getByRole('button', { name: 'Replace…' }));
    fireEvent.change(field(), { target: { value: ' hf_swap ' } });
    fireEvent.click(saveBtn());
    await screen.findByText('Hugging Face token saved');
    expect(api.apiPost).toHaveBeenCalledWith('/api/settings/hf-token', { token: 'hf_swap' });
  });

  it('keeps the value and warning after a failed save, hides detail, then succeeds', async () => {
    await renderWith(stateWith('app'));
    fireEvent.click(screen.getByRole('button', { name: 'Replace…' }));
    fireEvent.change(field(), { target: { value: 'hf_retry' } });
    api.apiPost.mockRejectedValueOnce(new Error('secret server detail'));
    fireEvent.click(saveBtn());
    expect(await screen.findByText(/Could not save the token/)).toBeTruthy();
    expect(screen.queryByText(/secret server detail/)).toBeNull();
    expect(field().value).toBe('hf_retry');
    expect(screen.getByText(WARNING)).toBeTruthy();

    fireEvent.click(saveBtn());
    await screen.findByText('Hugging Face token saved');
    expect(api.apiPost).toHaveBeenCalledTimes(2);
  });

  it('Cancel leaves replace mode and clears the typed value', async () => {
    await renderWith(stateWith('app'));
    fireEvent.click(screen.getByRole('button', { name: 'Replace…' }));
    fireEvent.change(field(), { target: { value: 'hf_half' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(queryField()).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Replace…' }));
    expect(field().value).toBe('');
  });

  it('has no Cancel when there is no active token to keep', async () => {
    await renderWith(NONE);
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
  });

  it('blocks duplicate saves, edits and cancellation while a save is in flight', async () => {
    await renderWith(stateWith('app'));
    fireEvent.click(screen.getByRole('button', { name: 'Replace…' }));
    fireEvent.change(field(), { target: { value: 'hf_once' } });
    const inflight = deferred();
    api.apiPost.mockReturnValue(inflight.promise);
    fireEvent.click(saveBtn());

    await waitFor(() => expect(field().disabled).toBe(true));
    expect(screen.getByRole('button', { name: 'Cancel' }).disabled).toBe(true);
    fireEvent.change(field(), { target: { value: 'hf_other' } });
    expect(field().value).toBe('hf_once');
    fireEvent.keyDown(field(), { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: 'saving…' }));
    expect(api.apiPost).toHaveBeenCalledTimes(1);

    inflight.resolve({});
    await screen.findByText('Hugging Face token saved');
    expect(api.apiPost).toHaveBeenCalledTimes(1);
  });
});
