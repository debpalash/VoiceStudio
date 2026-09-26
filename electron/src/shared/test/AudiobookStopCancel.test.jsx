// Audiobook Stop/cancel + live per-chapter progress (#1216).
//
// "The Create audiobook button has no stop option." This proves the fix:
// while a render streams, the primary action becomes a Stop button; clicking
// it aborts the fetch's AbortController AND releases the stream reader
// (reader.cancel), which is what closes the connection so the backend sees the
// disconnect. It also checks the live per-chapter progress list renders from
// the stream events, and that a stop lands in a clean "Stopped" state (not an
// error).
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '../i18n';
import en from '../i18n/locales/en.json';

vi.mock('../api/engines', () => ({
  listEngines: vi.fn().mockResolvedValue({ tts: { active: 'x', backends: [] } }),
}));
vi.mock('../api/generate', () => ({ audioUrl: (f) => `http://test.local/audio/${f}` }));

// audiobookGenerate returns a controllable SSE stream; we capture the abort
// signal it was handed and record reader.cancel() so the test can assert the
// end-to-end cancel wiring. First read emits `started` (3 chapters), the second
// stays pending until the signal aborts — exactly how a real fetch body behaves.
const gen = {
  capturedSignal: null,
  cancelCalled: false,
  reset() {
    this.capturedSignal = null;
    this.cancelCalled = false;
  },
};
vi.mock('../api/audiobook', () => ({
  audiobookListJobs: vi.fn().mockResolvedValue({ jobs: [] }),
  audiobookResume: vi.fn(),
  audiobookPlan: vi.fn(),
  audiobookUploadCover: vi.fn(),
  audiobookPreviewChapter: vi.fn(),
  audiobookImport: vi.fn(),
  audiobookGenerate: vi.fn((_body, opts) => {
    gen.capturedSignal = opts?.signal;
    let n = 0;
    return Promise.resolve({
      body: {
        getReader: () => ({
          read: () => {
            n += 1;
            if (n === 1)
              return Promise.resolve({
                done: false,
                value: new TextEncoder().encode(
                  'data: {"type":"started","chapters":3}\n\n' +
                    'data: {"type":"chapter","index":0,"total":3,"title":"One","cached":false}\n\n',
                ),
              });
            return new Promise((_, reject) => {
              // If the signal already aborted before this read() (a real race),
              // the 'abort' event has passed — reject now instead of hanging.
              if (gen.capturedSignal.aborted) {
                reject(new DOMException('Aborted', 'AbortError'));
                return;
              }
              gen.capturedSignal.addEventListener('abort', () =>
                reject(new DOMException('Aborted', 'AbortError')),
              );
            });
          },
          cancel: () => {
            gen.cancelCalled = true;
            return Promise.resolve();
          },
        }),
      },
    });
  }),
}));

import AudiobookTab from '../pages/AudiobookTab';
import { useAppStore } from '../store';

// AudiobookTab embeds VoiceSelector, which reads /archetypes via react-query
// (#1219) — a QueryClient must be in context (fetch is gated on dropdown open).
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const withI18n = (node) => (
  <QueryClientProvider client={qc}>
    <I18nextProvider i18n={i18n}>{node}</I18nextProvider>
  </QueryClientProvider>
);

describe('AudiobookTab — Stop/cancel + progress (#1216)', () => {
  beforeEach(() => {
    localStorage.clear();
    gen.reset();
    useAppStore.getState().setScript('# One\nHello.\n# Two\nWorld.\n# Three\nDone.');
  });

  it('swaps Create → Stop while generating, renders per-chapter progress, and cancels end-to-end', async () => {
    render(withI18n(<AudiobookTab profiles={[]} />));

    fireEvent.click(screen.getByText(en.audiobook.create));

    // Stop replaces Create; the live per-chapter list renders from the stream.
    await waitFor(() => expect(screen.getByText(en.audiobook.stop)).toBeTruthy());
    expect(screen.queryByText(en.audiobook.create)).toBeNull();
    // started{chapters:3} seeded 3 rows; chapter 0 finished → its title shows.
    await waitFor(() => expect(screen.getByText('One')).toBeTruthy());
    // Chapters not yet reached render from the chapter_n fallback key.
    expect(screen.getByText(en.audiobook.chapter_n.replace('{{n}}', '3'))).toBeTruthy();

    fireEvent.click(screen.getByText(en.audiobook.stop));

    // The fetch signal aborted AND the stream reader was cancelled (the two
    // halves of a real disconnect), then a clean Stopped state — not an error.
    await waitFor(() => {
      expect(gen.capturedSignal.aborted).toBe(true);
      expect(gen.cancelCalled).toBe(true);
    });
    await waitFor(() => expect(screen.getByText(en.audiobook.stopped_note)).toBeTruthy());
    expect(screen.getByText(en.audiobook.create)).toBeTruthy(); // Create is back
  });

  it('shows the empty-state hint when the script is blank', () => {
    useAppStore.getState().setScript('');
    render(withI18n(<AudiobookTab profiles={[]} />));
    expect(screen.getByText(en.audiobook.empty_hint)).toBeTruthy();
  });
});
