import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// Regression guard for #532: a finished audiobook/story render must preview
// IN-APP. The card used to call window.open(url, '_blank'), which under Tauri's
// WebView2 on Windows spawned a separate, un-closeable black media window. The
// fix routes the render through the shared in-app playback path (playBlobAudio).

vi.mock('../utils/media', () => ({ playBlobAudio: vi.fn() }));
vi.mock('../api/generate', () => ({ audioUrl: (f) => `http://test.local/audio/${f}` }));
// The render now plays in-app via apiFetch (carries the LAN-share PIN /
// remote API-key headers a raw fetch would skip), so the mock serves both the
// job list and the audio blob.
const apiFetchMock = vi.fn(async (path) => {
  if (path === '/longform/jobs') {
    return {
      json: async () => ({
        jobs: [
          {
            job_id: 'jb1',
            output: 'book.wav',
            title: 'My Audiobook',
            type: 'audiobook',
            created_at: 1,
          },
        ],
      }),
    };
  }
  return { blob: async () => new Blob(['x']), json: async () => ({}) };
});
vi.mock('../api/client', () => ({ apiFetch: (...a) => apiFetchMock(...a) }));

import Projects from '../pages/Projects';
import { playBlobAudio } from '../utils/media';

describe('Projects — audiobook playback (#532)', () => {
  let fetchMock;
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    fetchMock = vi.fn(async () => ({ ok: true, blob: async () => new Blob(['x']) }));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('plays the render in-app and never opens a separate window', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    render(<Projects />);

    // The longform job loads via apiFetch('/longform/jobs') in an effect.
    const card = (await screen.findByText('My Audiobook')).closest('button');
    expect(card).toBeTruthy();

    fireEvent.click(card);

    await waitFor(() => expect(playBlobAudio).toHaveBeenCalledTimes(1));
    // In-app fetch of the render file (via apiFetch), never a new window/OS
    // media surface.
    expect(apiFetchMock).toHaveBeenCalledWith('http://test.local/audio/book.wav', {
      cache: 'no-store',
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(openSpy).not.toHaveBeenCalled();
  });
});
