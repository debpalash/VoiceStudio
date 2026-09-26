import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '../i18n';
import { ENCODED, SAMPLE } from './encodedText';

// Stories → Import reads a .txt/.srt the user picked. Windows tools save those
// as UTF-16 (Notepad's "Unicode") or in the Windows-1252 code page, and the
// imported script must be the text the file holds, not replacement characters.

vi.mock('../api/generate', () => ({ generateSpeech: vi.fn(), audioUrl: (path) => path }));
vi.mock('../utils/media', () => ({ playBlobAudio: vi.fn(() => Promise.resolve()) }));
vi.mock('../api/hooks', () => ({ useArchetypes: vi.fn(() => ({ data: undefined })) }));
vi.mock('../api/archetypes', () => ({ useArchetypeAsProfile: vi.fn() }));

import StoriesEditor from '../components/StoriesEditor';
import { useAppStore } from '../store';

describe('StoriesEditor import', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    useAppStore.setState({
      cast: [{ id: 'narrator', name: 'Narrator', color: '#b8bb26', profileId: null }],
      storyTracks: [],
      storyProjects: [],
      currentProjectId: null,
    });
  });

  afterEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    window.localStorage.clear();
  });

  it.each(Object.keys(ENCODED))('imports a %s file with its text intact', async (encoding) => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <StoriesEditor profiles={[]} />
      </QueryClientProvider>,
    );
    const input = document.querySelector('input[name="story-import-file"]');
    fireEvent.change(input, {
      target: { files: [new File([ENCODED[encoding](SAMPLE)], 'story.txt')] },
    });

    await waitFor(() =>
      expect(document.querySelector('.stories-split-panel textarea')).toHaveValue(SAMPLE),
    );
  });
});
