import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

import DubbingDemo from '../components/DubbingDemo';

const MOCK_MANIFEST = {
  source: {
    code: 'en',
    label: 'English',
    video: 'source.mp4',
    srt: 'source.srt',
    script: 'VoiceStudio runs entirely on your machine.',
  },
  dubbed: [
    {
      code: 'es',
      label: 'Español',
      video: 'dubbed_es.mp4',
      dir: 'ltr',
      script: 'Funciona en tu máquina.',
    },
    {
      code: 'fr',
      label: 'Français',
      video: 'dubbed_fr.mp4',
      dir: 'ltr',
      script: 'Fonctionne sur votre machine.',
    },
    {
      code: 'ja',
      label: '日本語',
      video: 'dubbed_ja.mp4',
      dir: 'ltr',
      script: 'マシン上で動作します。',
    },
  ],
};

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

describe('DubbingDemo', () => {
  let originalFetch;

  beforeEach(() => {
    originalFetch = global.fetch;
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(MOCK_MANIFEST),
      }),
    );
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('renders source + initial dubbed pane after manifest loads', async () => {
    render(withI18n(<DubbingDemo onDismiss={vi.fn()} />));
    await waitFor(() => {
      expect(screen.getByText('English')).toBeInTheDocument();
    });
    // Default pick is 'es'. Spanish appears in both the pane label and the
    // picker chip — assert at least one match plus the unique script.
    expect(screen.getAllByText('Español').length).toBeGreaterThan(0);
    expect(screen.getByText(/Funciona en tu máquina/)).toBeInTheDocument();
  });

  it('shows all language chips in the picker', async () => {
    render(withI18n(<DubbingDemo onDismiss={vi.fn()} />));
    await waitFor(() => screen.getByText('English'));
    expect(screen.getAllByText('Español').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('button', { name: 'Français' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '日本語' })).toBeInTheDocument();
  });

  it('swaps the dubbed pane script when a chip is clicked', async () => {
    render(withI18n(<DubbingDemo onDismiss={vi.fn()} />));
    await waitFor(() => screen.getByText('English'));
    fireEvent.click(screen.getByRole('button', { name: 'Français' }));
    await waitFor(() => {
      expect(screen.getByText(/Fonctionne sur votre machine/)).toBeInTheDocument();
    });
  });

  it('calls onDismiss when the CTA is clicked', async () => {
    const onDismiss = vi.fn();
    render(withI18n(<DubbingDemo onDismiss={onDismiss} />));
    await waitFor(() => screen.getByText('English'));
    fireEvent.click(screen.getByRole('button', { name: /Run this on your own video/i }));
    expect(onDismiss).toHaveBeenCalled();
  });
});
