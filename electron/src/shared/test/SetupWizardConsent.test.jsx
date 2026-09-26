import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

// The wizard's heavy children are irrelevant here — the subject is the
// consent step's presence and Yes/No/skip semantics.
vi.mock('../components/WizardLibrary', () => ({ default: () => null }));
vi.mock('../components/MediaEngineCard', () => ({ default: () => null }));
vi.mock('../components/MirrorRescue', () => ({ default: () => null }));
vi.mock('../components/HfTokenCard', () => ({ default: () => null }));
vi.mock('../components/DictationDemo', () => ({ default: () => null }));
vi.mock('../api/external', () => ({
  openExternal: vi.fn(() => Promise.resolve()),
}));

// Preflight passes and models are ready, so the wizard is freely navigable.
vi.mock('../api/hooks', () => ({
  useSetupStatus: () => ({
    data: { models_ready: true, missing: [], hf_cache_dir: '/tmp/hf' },
    refetch: vi.fn(),
  }),
  usePreflight: () => ({
    data: { ok: true, has_warnings: false, checks: [] },
    isLoading: false,
    refetch: vi.fn(),
  }),
}));

const apiJson = vi.fn();
const apiFetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
vi.mock('../api/client', () => ({
  apiJson: (...a) => apiJson(...a),
  apiFetch: (...a) => apiFetch(...a),
  API: '',
}));

const enableAnalytics = vi.fn(() => Promise.resolve());
const disableAnalytics = vi.fn();
vi.mock('../utils/analytics', () => ({
  enableAnalytics: (...a) => enableAnalytics(...a),
  disableAnalytics: (...a) => disableAnalytics(...a),
}));

import SetupWizard from '../pages/SetupWizard';

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

/** Walk the wizard from the system step to the step after models. */
async function advancePastModels() {
  fireEvent.click(await screen.findByText(/All good — continue/i));
  fireEvent.click(await screen.findByText(/Required models ready/i));
}

beforeEach(() => {
  apiJson.mockReset();
  apiFetch.mockClear();
  apiFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
  enableAnalytics.mockClear();
  disableAnalytics.mockClear();
});

describe('SetupWizard analytics consent step', () => {
  it('inserts the consent step when the build can send and the user was never asked', async () => {
    apiJson.mockResolvedValue({
      available: true,
      prompted: false,
      opted_in: false,
    });
    render(withI18n(<SetupWizard onReady={() => {}} />));
    // The stepper rail gains the consent stage.
    expect(await screen.findByText(/Improve VoiceStudio/i)).toBeInTheDocument();

    await advancePastModels();
    // The title renders exactly once (the card's own heading) — the masthead
    // subtitle and the section head no longer repeat it (#1855).
    expect(await screen.findAllByText(/Help improve VoiceStudio\?/i)).toHaveLength(1);
    // Two equal-weight choices, no preselected default.
    expect(screen.getByTestId('analytics-consent-yes')).toBeInTheDocument();
    expect(screen.getByTestId('analytics-consent-no')).toBeInTheDocument();
  });

  it('YES persists enabled:true, starts the frontend SDK, and advances', async () => {
    apiJson.mockResolvedValue({
      available: true,
      prompted: false,
      opted_in: false,
    });
    render(withI18n(<SetupWizard onReady={() => {}} />));
    await screen.findByText(/Improve VoiceStudio/i);
    await advancePastModels();

    fireEvent.click(await screen.findByTestId('analytics-consent-yes'));
    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith(
        '/api/settings/analytics',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ enabled: true }),
        }),
      );
    });
    expect(enableAnalytics).toHaveBeenCalled();
    // Advanced to the dictation act.
    expect(await screen.findByText(/Enter studio/i)).toBeInTheDocument();
  });

  it('NO persists enabled:false, keeps the SDK off, and advances all the same', async () => {
    apiJson.mockResolvedValue({
      available: true,
      prompted: false,
      opted_in: false,
    });
    render(withI18n(<SetupWizard onReady={() => {}} />));
    await screen.findByText(/Improve VoiceStudio/i);
    await advancePastModels();

    fireEvent.click(await screen.findByTestId('analytics-consent-no'));
    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith(
        '/api/settings/analytics',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ enabled: false }),
        }),
      );
    });
    expect(disableAnalytics).toHaveBeenCalled();
    expect(enableAnalytics).not.toHaveBeenCalled();
    expect(await screen.findByText(/Enter studio/i)).toBeInTheDocument();
  });

  it('shows NO consent step when the build has no analytics destination', async () => {
    apiJson.mockResolvedValue({
      available: false,
      prompted: false,
      opted_in: false,
    });
    render(withI18n(<SetupWizard onReady={() => {}} />));
    await advancePastModels();
    // Straight from models to dictation — an unanswerable ask would be a lie.
    expect(await screen.findByText(/Enter studio/i)).toBeInTheDocument();
    expect(screen.queryByText(/Help improve VoiceStudio\?/i)).not.toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalled();
    // The dictation step's rail label renders exactly once, and it's a
    // distinct string from the demo card's own title — the masthead
    // subtitle no longer repeats it, the redundant section head above the
    // demo was removed (#1855), and the rail label itself was made distinct
    // from the demo's title (#1930). DictationDemo is mocked to null here,
    // so the rail/card-title distinctness itself is covered by the real
    // (unmocked) render in SetupWizardDictation.test.jsx.
    expect(screen.getAllByText('Dictation')).toHaveLength(1);
  });

  it('shows NO consent step when the user was already asked', async () => {
    apiJson.mockResolvedValue({
      available: true,
      prompted: true,
      opted_in: false,
    });
    render(withI18n(<SetupWizard onReady={() => {}} />));
    await advancePastModels();
    expect(await screen.findByText(/Enter studio/i)).toBeInTheDocument();
    expect(screen.queryByTestId('analytics-consent-yes')).not.toBeInTheDocument();
  });

  it('a backend error means no consent step — and nothing is ever sent (fails closed)', async () => {
    apiJson.mockRejectedValue(new Error('backend down'));
    render(withI18n(<SetupWizard onReady={() => {}} />));
    await advancePastModels();
    expect(await screen.findByText(/Enter studio/i)).toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalled();
    expect(enableAnalytics).not.toHaveBeenCalled();
  });
});
