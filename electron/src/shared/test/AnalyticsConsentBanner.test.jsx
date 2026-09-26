import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

const apiJson = vi.fn();
const apiFetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
vi.mock('../api/client', () => ({
  apiJson: (...a) => apiJson(...a),
  apiFetch: (...a) => apiFetch(...a),
  API: '',
}));
vi.mock('../api/external', () => ({
  openExternal: vi.fn(() => Promise.resolve()),
}));

const enableAnalytics = vi.fn(() => Promise.resolve());
const disableAnalytics = vi.fn();
vi.mock('../utils/analytics', () => ({
  enableAnalytics: (...a) => enableAnalytics(...a),
  disableAnalytics: (...a) => disableAnalytics(...a),
}));

import AnalyticsConsentBanner from '../components/AnalyticsConsentBanner';

const withI18n = (node) => <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;

beforeEach(() => {
  apiJson.mockReset();
  apiFetch.mockClear();
  apiFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
  enableAnalytics.mockClear();
  disableAnalytics.mockClear();
});

describe('AnalyticsConsentBanner — the one-time ask for existing installs', () => {
  it('shows when the build can send, the user was never asked, and analytics is off', async () => {
    apiJson.mockResolvedValue({
      available: true,
      prompted: false,
      opted_in: false,
    });
    render(withI18n(<AnalyticsConsentBanner />));
    expect(await screen.findByTestId('analytics-consent-banner')).toBeInTheDocument();
  });

  it.each([
    ['already prompted', { available: true, prompted: true, opted_in: false }],
    ['already opted in', { available: true, prompted: false, opted_in: true }],
    ['no destination (source build)', { available: false, prompted: false, opted_in: false }],
  ])('never shows when %s', async (_label, state) => {
    apiJson.mockResolvedValue(state);
    const { container } = render(withI18n(<AnalyticsConsentBanner />));
    // Give the fetch a tick to resolve, then assert nothing rendered.
    await waitFor(() => expect(apiJson).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it('never shows when the backend is unreachable (fails closed)', async () => {
    apiJson.mockRejectedValue(new Error('down'));
    const { container } = render(withI18n(<AnalyticsConsentBanner />));
    await waitFor(() => expect(apiJson).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it('YES persists the choice once and hides the banner', async () => {
    apiJson.mockResolvedValue({
      available: true,
      prompted: false,
      opted_in: false,
    });
    render(withI18n(<AnalyticsConsentBanner />));
    fireEvent.click(await screen.findByTestId('analytics-consent-yes'));
    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledTimes(1);
      expect(apiFetch).toHaveBeenCalledWith(
        '/api/settings/analytics',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ enabled: true }),
        }),
      );
    });
    expect(enableAnalytics).toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.queryByTestId('analytics-consent-banner')).not.toBeInTheDocument(),
    );
  });

  it('NO persists the choice and hides the banner', async () => {
    apiJson.mockResolvedValue({
      available: true,
      prompted: false,
      opted_in: false,
    });
    render(withI18n(<AnalyticsConsentBanner />));
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
    await waitFor(() =>
      expect(screen.queryByTestId('analytics-consent-banner')).not.toBeInTheDocument(),
    );
  });

  it('dismiss (X) counts as NO: persists prompted+off, so it is one-shot', async () => {
    apiJson.mockResolvedValue({
      available: true,
      prompted: false,
      opted_in: false,
    });
    render(withI18n(<AnalyticsConsentBanner />));
    await screen.findByTestId('analytics-consent-banner');
    fireEvent.click(screen.getByTitle(/Dismiss/i));
    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith(
        '/api/settings/analytics',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ enabled: false }),
        }),
      );
    });
    expect(screen.queryByTestId('analytics-consent-banner')).not.toBeInTheDocument();
    expect(enableAnalytics).not.toHaveBeenCalled();
  });
});
