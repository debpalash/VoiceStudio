import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

// Only the wizard's non-dictation heavy children are mocked away —
// DictationDemo is left REAL so this test exercises the actual rendered
// title text, unlike SetupWizardConsent.test.jsx (which mocks it to null
// and therefore cannot catch a rail-label/card-title collision, #1930).
vi.mock('../components/WizardLibrary', () => ({ default: () => null }));
vi.mock('../components/MediaEngineCard', () => ({ default: () => null }));
vi.mock('../components/MirrorRescue', () => ({ default: () => null }));
vi.mock('../components/HfTokenCard', () => ({ default: () => null }));
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

// No analytics destination in this build, so the wizard skips straight from
// models to dictation — matches SetupWizardConsent.test.jsx's equivalent case.
const apiJson = vi.fn(() =>
  Promise.resolve({ available: false, prompted: false, opted_in: false }),
);
const apiFetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
vi.mock('../api/client', () => ({
  apiJson: (...a) => apiJson(...a),
  apiFetch: (...a) => apiFetch(...a),
  API: '',
}));

import SetupWizard from '../pages/SetupWizard';

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

beforeEach(() => {
  apiJson.mockClear();
  apiFetch.mockClear();
});

describe('SetupWizard dictation step (real DictationDemo)', () => {
  it('shows the rail label and the demo title as two distinct strings, each once (#1930)', async () => {
    render(withI18n(<SetupWizard onReady={() => {}} />));

    fireEvent.click(await screen.findByText(/All good — continue/i));
    fireEvent.click(await screen.findByText(/Required models ready/i));

    // Straight to dictation — no consent step in this build.
    expect(await screen.findByText(/Enter studio/i)).toBeInTheDocument();

    // The step rail's short label and the DictationDemo card's own title are
    // two DIFFERENT strings — no more reusing "Try dictation" for both.
    expect(screen.getAllByText('Dictation')).toHaveLength(1);
    expect(screen.getAllByText(/Try Dictation/i)).toHaveLength(1);
  });
});
