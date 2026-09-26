// Audiobook tab layout — the Write → Cast → Produce workspace redesign.
//
// The inspector rail is gone: Script / Voices / Book tabs (shadcn, manual
// activation, clone-workspace pill styling) own the column, and the
// warnings/progress/result/plan status rail only takes space once there is
// something to show.
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '../i18n';
import en from '../i18n/locales/en.json';

// listEngines runs once on mount to gate the emotion controls — stub it so the
// tab renders offline and reports "no emotion support".
vi.mock('../api/engines', () => ({
  listEngines: vi.fn().mockResolvedValue({ tts: { active: 'x', backends: [] } }),
}));
vi.mock('../api/generate', () => ({ audioUrl: (f) => `http://test.local/audio/${f}` }));
vi.mock('../api/audiobook', () => ({
  audiobookListJobs: vi.fn().mockResolvedValue({ jobs: [] }),
  audiobookResume: vi.fn(),
  audiobookPlan: vi.fn(),
  audiobookGenerate: vi.fn(),
  audiobookUploadCover: vi.fn(),
  audiobookPreviewChapter: vi.fn(),
  audiobookImport: vi.fn(),
}));

import AudiobookTab from '../pages/AudiobookTab';
import { useAppStore } from '../store';

// AudiobookTab embeds VoiceSelector, which reads /archetypes via react-query
// (#1219) — so a QueryClient must be in context even though the fetch is gated
// on the dropdown being open.
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const withI18n = (node) => (
  <QueryClientProvider client={qc}>
    <I18nextProvider i18n={i18n}>{node}</I18nextProvider>
  </QueryClientProvider>
);

function tabTrigger(name) {
  return screen.getByRole('tab', { name });
}

// Real browsers focus on mousedown and activate on click; fireEvent.click
// alone skips the focus half, which manual-activation Radix tabs need.
function switchTab(name) {
  const tab = tabTrigger(name);
  fireEvent.mouseDown(tab);
  fireEvent.click(tab);
  return tab;
}

describe('AudiobookTab — Write/Cast/Produce tabs', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.getState().setLastOutput('');
    useAppStore.getState().setScript('');
  });

  it('opens on the Script tab with the manuscript editor', () => {
    render(withI18n(<AudiobookTab profiles={[]} />));
    for (const label of [en.audiobook.tab_script, en.audiobook.tab_voices, en.audiobook.tab_book]) {
      expect(screen.getByRole('tab', { name: new RegExp(label) })).toBeTruthy();
    }
    expect(tabTrigger(en.audiobook.tab_script)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText(en.audiobook.script, { selector: 'textarea' })).toBeTruthy();
    // Hero actions stay discoverable through accessible labels.
    expect(screen.getByLabelText(en.audiobook.load_sample)).toBeTruthy();
    expect(screen.getByLabelText(en.audiobook.import)).toBeTruthy();
    expect(screen.getByLabelText(en.audiobook.preview_plan)).toBeTruthy();
    expect(screen.getByText(en.audiobook.create)).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: en.audiobook.title })).toBeTruthy();
  });

  it('keeps the status rail out of the way until there is something to show', () => {
    render(withI18n(<AudiobookTab profiles={[]} />));
    expect(screen.queryByTestId('audiobook-status-rail')).toBeNull();
  });

  it('shows voices controls only on the Voices tab', () => {
    render(withI18n(<AudiobookTab profiles={[]} />));
    expect(screen.queryByText(en.audiobook.default_voice)).toBeNull();
    switchTab(en.audiobook.tab_voices);
    expect(tabTrigger(en.audiobook.tab_voices)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(en.audiobook.default_voice)).toBeTruthy();
    expect(screen.getByText(en.audiobook.language)).toBeTruthy();
    expect(screen.getByText(en.audiobook.cast)).toBeTruthy();
    expect(screen.getByText(en.audiobook.expressive)).toBeTruthy();
  });

  it('shows production controls only on the Book tab', () => {
    render(withI18n(<AudiobookTab profiles={[]} />));
    expect(screen.queryByLabelText(en.audiobook.loudness)).toBeNull();
    switchTab(en.audiobook.tab_book);
    expect(screen.getByLabelText(en.audiobook.format)).toBeTruthy();
    expect(screen.getByLabelText(en.audiobook.loudness)).toBeTruthy();
    // Details / lexicon / markup fold into collapsible sections.
    for (const title of [en.audiobook.details, en.audiobook.lexicon, en.audiobook.markup_help]) {
      expect(screen.getByText(new RegExp(title))).toBeTruthy();
    }
  });

  it('persists the workspace tab across visits', () => {
    const { unmount } = render(withI18n(<AudiobookTab profiles={[]} />));
    switchTab(en.audiobook.tab_voices);
    expect(localStorage.getItem('omnivoice.audiobook.tab')).toBe('voices');
    unmount();
    render(withI18n(<AudiobookTab profiles={[]} />));
    expect(tabTrigger(en.audiobook.tab_voices)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(en.audiobook.default_voice)).toBeTruthy();
  });

  it('opens Cast content when the script contains cast tags', () => {
    useAppStore.getState().setScript('# Chapter\n[voice:Mara] Hello');
    render(withI18n(<AudiobookTab profiles={[]} />));
    expect(tabTrigger(en.audiobook.tab_voices)).toHaveTextContent(String(1));
    switchTab(en.audiobook.tab_voices);
    expect(screen.getByLabelText(`${en.audiobook.cast}: Mara`)).toBeTruthy();
  });

  it('pairs a persisted output with its render-time script after later edits', () => {
    useAppStore
      .getState()
      .setLastOutputSnapshot('rendered.m4b', '# Rendered\nOld line', [
        { title: 'Rendered', status: 'done', duration_s: 2 },
      ]);
    useAppStore.getState().setScript('# Edited\nNew line');

    render(withI18n(<AudiobookTab profiles={[]} />));

    expect(screen.getByRole('button', { name: 'Old' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'New' })).toBeNull();
  });
});
