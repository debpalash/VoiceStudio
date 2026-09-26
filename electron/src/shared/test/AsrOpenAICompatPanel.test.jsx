/**
 * Model Catalogue (ASR tab) → OpenAI-compatible remote ASR panel.
 *
 * The single Save button persists base URL + model + API key. Regression
 * coverage for the dirty/saved lifecycle: Save is disabled while the fields
 * match the server (so "did I save that?" has an answer), enables on any edit
 * (including URL/model-only edits far above the button), and a successful save
 * shows an explicit "Saved" confirmation even when the key didn't change.
 *
 * Test connection: saves first when dirty (stale-config contract), then
 * POSTs /test and renders success + latency or the classified failure.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

const apiJson = vi.fn();
const apiFetch = vi.fn();
const apiPost = vi.fn();
vi.mock('../api/client', () => ({
  apiJson: (...a) => apiJson(...a),
  apiFetch: (...a) => apiFetch(...a),
  apiPost: (...a) => apiPost(...a),
}));

import AsrOpenAICompatPanel from '../components/settings/AsrOpenAICompatPanel';

const SERVER = { base_url: 'http://localhost:8000/v1', model: 'whisper-1', has_key: false };

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

describe('AsrOpenAICompatPanel', () => {
  beforeEach(() => {
    apiJson.mockReset();
    apiFetch.mockReset();
    apiPost.mockReset();
    apiJson.mockResolvedValue(SERVER);
  });

  const waitForLoad = async () => {
    await waitFor(() =>
      expect(screen.getByTestId('asr-openai-compat-model')).toHaveValue(SERVER.model),
    );
  };

  it('disables Save until a field differs from the server values', async () => {
    render(withI18n(<AsrOpenAICompatPanel />));
    const saveBtn = await screen.findByTestId('asr-openai-compat-save');
    await waitForLoad();
    expect(saveBtn).toBeDisabled();

    // Editing the model (a field far above the button) makes it dirty…
    fireEvent.change(screen.getByTestId('asr-openai-compat-model'), {
      target: { value: 'qwen3-asr' },
    });
    expect(saveBtn).not.toBeDisabled();

    // …and reverting the edit makes it clean again.
    fireEvent.change(screen.getByTestId('asr-openai-compat-model'), {
      target: { value: SERVER.model },
    });
    expect(saveBtn).toBeDisabled();
  });

  it('typing an API key alone marks the form dirty', async () => {
    render(withI18n(<AsrOpenAICompatPanel />));
    const saveBtn = await screen.findByTestId('asr-openai-compat-save');
    await waitForLoad();
    expect(saveBtn).toBeDisabled();
    fireEvent.change(screen.getByTestId('asr-openai-compat-api-key'), {
      target: { value: 'k' },
    });
    expect(saveBtn).not.toBeDisabled();
  });

  it('shows a Saved confirmation after a URL/model-only save (no key change)', async () => {
    apiFetch.mockResolvedValue({
      json: async () => ({ base_url: SERVER.base_url, model: 'qwen3-asr', has_key: false }),
    });
    render(withI18n(<AsrOpenAICompatPanel />));
    await screen.findByTestId('asr-openai-compat-save');
    await waitForLoad();
    fireEvent.change(screen.getByTestId('asr-openai-compat-model'), {
      target: { value: 'qwen3-asr' },
    });
    fireEvent.click(screen.getByTestId('asr-openai-compat-save'));

    expect(await screen.findByTestId('asr-openai-compat-saved')).toHaveTextContent(
      i18n.t('models.asrOpenAICompatSaved'),
    );
    // The PUT carried the edited fields and omitted the untouched key.
    const [path, opts] = apiFetch.mock.calls[0];
    expect(path).toBe('/api/settings/asr-openai-compat');
    expect(JSON.parse(opts.body)).toEqual({ base_url: SERVER.base_url, model: 'qwen3-asr' });
    // Clean again after the save round-trip.
    expect(screen.getByTestId('asr-openai-compat-save')).toBeDisabled();

    // The confirmation clears as soon as the user edits again.
    fireEvent.change(screen.getByTestId('asr-openai-compat-model'), {
      target: { value: 'other' },
    });
    expect(screen.queryByTestId('asr-openai-compat-saved')).not.toBeInTheDocument();
  });

  it('notifies onSaved after a successful save (the Engines matrix refetches)', async () => {
    const onSaved = vi.fn();
    apiFetch.mockResolvedValue({
      json: async () => ({ base_url: SERVER.base_url, model: 'qwen3-asr', has_key: false }),
    });
    render(withI18n(<AsrOpenAICompatPanel onSaved={onSaved} />));
    await screen.findByTestId('asr-openai-compat-save');
    await waitForLoad();
    fireEvent.change(screen.getByTestId('asr-openai-compat-model'), {
      target: { value: 'qwen3-asr' },
    });
    fireEvent.click(screen.getByTestId('asr-openai-compat-save'));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  });

  it('Test connection on a clean form POSTs /test without re-saving and shows latency', async () => {
    apiPost.mockResolvedValue({
      ok: true,
      status: 'ok',
      latency_ms: 42.3,
      http_status: 200,
      models_count: 1,
      model_found: true,
    });
    render(withI18n(<AsrOpenAICompatPanel />));
    await screen.findByTestId('asr-openai-compat-test');
    await waitForLoad();

    fireEvent.click(screen.getByTestId('asr-openai-compat-test'));
    const result = await screen.findByTestId('asr-openai-compat-test-result');
    expect(result).toHaveTextContent(
      i18n.t('models.asrOpenAICompatTestOkModelListed', { ms: 42, model: SERVER.model }),
    );
    expect(apiPost).toHaveBeenCalledWith('/api/settings/asr-openai-compat/test');
    // Clean form → no PUT before the probe.
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('Test connection on a dirty form saves first, then probes', async () => {
    apiFetch.mockResolvedValue({
      json: async () => ({ base_url: SERVER.base_url, model: 'qwen3-asr', has_key: false }),
    });
    apiPost.mockResolvedValue({ ok: true, status: 'ok', latency_ms: 10 });
    render(withI18n(<AsrOpenAICompatPanel />));
    await screen.findByTestId('asr-openai-compat-test');
    await waitForLoad();

    fireEvent.change(screen.getByTestId('asr-openai-compat-model'), {
      target: { value: 'qwen3-asr' },
    });
    fireEvent.click(screen.getByTestId('asr-openai-compat-test'));
    await screen.findByTestId('asr-openai-compat-test-result');
    // The PUT persisted the edit BEFORE the probe ran (stale-config contract).
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(apiFetch.mock.calls[0][1].body).model).toBe('qwen3-asr');
    expect(apiPost).toHaveBeenCalledTimes(1);
  });

  it('renders the classified failure and clears it on the next edit', async () => {
    apiPost.mockResolvedValue({
      ok: false,
      status: 'unreachable',
      latency_ms: 3.1,
      detail: 'ConnectError: connection refused',
    });
    render(withI18n(<AsrOpenAICompatPanel />));
    await screen.findByTestId('asr-openai-compat-test');
    await waitForLoad();

    fireEvent.click(screen.getByTestId('asr-openai-compat-test'));
    const result = await screen.findByTestId('asr-openai-compat-test-result');
    expect(result).toHaveTextContent(i18n.t('models.asrOpenAICompatTestUnreachable'));

    // Editing any field invalidates the verdict — a stale result must not
    // vouch for values it never tested.
    fireEvent.change(screen.getByTestId('asr-openai-compat-base-url'), {
      target: { value: 'http://other:9000/v1' },
    });
    expect(screen.queryByTestId('asr-openai-compat-test-result')).not.toBeInTheDocument();
  });

  it('aborts the probe when the pre-test save fails (no misleading green)', async () => {
    apiFetch.mockRejectedValue(new Error('400 Bad Request: Base URL must start with http(s)://'));
    render(withI18n(<AsrOpenAICompatPanel />));
    await screen.findByTestId('asr-openai-compat-test');
    await waitForLoad();

    fireEvent.change(screen.getByTestId('asr-openai-compat-base-url'), {
      target: { value: 'localhost:8080/v1' },
    });
    fireEvent.click(screen.getByTestId('asr-openai-compat-test'));
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId('asr-openai-compat-test')).not.toBeDisabled());
    expect(apiPost).not.toHaveBeenCalled();
    expect(screen.queryByTestId('asr-openai-compat-test-result')).not.toBeInTheDocument();
  });
});
