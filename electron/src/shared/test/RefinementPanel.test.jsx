/**
 * Settings → Capture → Dictation refinement panel.
 *
 * Regression coverage for two Settings paper cuts:
 *  1. A failed initial GET used to return null from the component — the whole
 *     "Dictation refinement" section silently vanished from Settings. It must
 *     render the section shell with the error and a working Retry button.
 *  2. With no LLM configured, the panel described the dead-end but offered no
 *     way to fix it — the "Open LLM Providers" deep-link must be present
 *     BEFORE the first refinement failure, not only after one.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';
import { useAppStore } from '../store';

const apiJson = vi.fn();
const apiFetch = vi.fn();
vi.mock('../api/client', () => ({
  apiJson: (...a) => apiJson(...a),
  apiFetch: (...a) => apiFetch(...a),
}));

import RefinementPanel from '../components/settings/RefinementPanel';

const CFG = {
  auto: false,
  smart_cleanup: true,
  self_correction: true,
  preserve_technical: true,
  llm_ready: true,
  last_refine_status: null,
};

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

describe('RefinementPanel', () => {
  beforeEach(() => {
    apiJson.mockReset();
    apiFetch.mockReset();
    useAppStore.getState().setMode('launchpad');
    useAppStore.getState().setPendingSettingsTab(null);
  });

  it('renders the section with the error and Retry when the initial GET fails (never vanishes)', async () => {
    apiJson.mockRejectedValueOnce(new Error('backend restarting'));
    render(withI18n(<RefinementPanel />));

    // Section title + error stay visible instead of the panel disappearing.
    expect(await screen.findByText(i18n.t('dictation.title'))).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('backend restarting');

    // Retry refetches and renders the toggles.
    apiJson.mockResolvedValueOnce(CFG);
    fireEvent.click(screen.getByTestId('refine-retry'));
    expect(
      await screen.findByRole('switch', { name: i18n.t('dictation.flag_auto') }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('offers the Open LLM Providers deep-link when no LLM is configured', async () => {
    apiJson.mockResolvedValueOnce({ ...CFG, llm_ready: false });
    render(withI18n(<RefinementPanel />));

    const btn = await screen.findByTestId('refine-open-llm');
    fireEvent.click(btn);
    expect(useAppStore.getState().mode).toBe('settings');
    expect(useAppStore.getState().pendingSettingsTab).toBe('llm-providers');
  });

  it('hides the deep-link button when an LLM is configured, and PUTs a toggle', async () => {
    apiJson.mockResolvedValueOnce(CFG);
    apiFetch.mockResolvedValue({ json: async () => ({ ...CFG, auto: true }) });
    render(withI18n(<RefinementPanel />));

    const master = await screen.findByRole('switch', { name: i18n.t('dictation.flag_auto') });
    expect(screen.queryByTestId('refine-open-llm')).not.toBeInTheDocument();

    fireEvent.click(master);
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const [path, opts] = apiFetch.mock.calls[0];
    expect(path).toBe('/api/settings/dictation-refinement');
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({ auto: true });
  });
});
