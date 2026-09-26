import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '../i18n';

// Stories → Clear script removes every line and chapter in one confirmed
// step. Before it existed an imported story could only be undone one trash
// icon at a time, and the cast must survive the clear.

const generateSpeech = vi.fn();
const playBlobAudio = vi.fn(() => Promise.resolve());
const stopActivePlayback = vi.fn();
vi.mock('../api/generate', () => ({
  generateSpeech: (...args) => generateSpeech(...args),
  audioUrl: (path) => path,
}));
vi.mock('../utils/media', () => ({
  playBlobAudio: (...args) => playBlobAudio(...args),
  isTauri: false,
}));
vi.mock('../utils/playback', () => ({ stopActivePlayback: () => stopActivePlayback() }));
vi.mock('../api/hooks', () => ({ useArchetypes: vi.fn(() => ({ data: undefined })) }));
vi.mock('../api/archetypes', () => ({ useArchetypeAsProfile: vi.fn() }));
const askConfirm = vi.fn();
vi.mock('../utils/dialog', () => ({ askConfirm: (...args) => askConfirm(...args) }));

import StoriesEditor from '../components/StoriesEditor';
import { useAppStore } from '../store';

const CAST = [{ id: 'narrator', name: 'Narrator', color: '#b8bb26', profileId: null }];
const TRACKS = [
  { id: 't1', castId: 'narrator', text: '# Chapter one', voice: null },
  { id: 't2', castId: 'narrator', text: 'Zoe stepped closer.', voice: null },
  { id: 't3', castId: 'narrator', text: 'It is a sloth!', voice: null },
];

function renderEditor() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <StoriesEditor profiles={[]} />
    </QueryClientProvider>,
  );
}

describe('StoriesEditor clear script', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:new-preview');
    askConfirm.mockReset();
    generateSpeech.mockReset();
    playBlobAudio.mockClear();
    stopActivePlayback.mockClear();
    useAppStore.setState({
      cast: CAST,
      storyTracks: TRACKS,
      storyProjects: [],
      currentProjectId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    window.localStorage.clear();
  });

  it('removes every line after the user confirms, keeps the cast, and does not reseed the sample', async () => {
    // First-run flag deliberately unset: an import-then-clear on a fresh
    // install must end with an empty script, not the demo story.
    askConfirm.mockResolvedValue(true);
    renderEditor();
    fireEvent.click(screen.getByRole('button', { name: /clear script/i }));

    await waitFor(() => expect(useAppStore.getState().storyTracks).toEqual([]));
    expect(askConfirm).toHaveBeenCalledTimes(1);
    expect(askConfirm.mock.calls[0][0]).toMatch(/3 script entries/);
    expect(useAppStore.getState().cast).toEqual(CAST);
    await new Promise((r) => setTimeout(r, 50));
    expect(useAppStore.getState().storyTracks).toEqual([]);
    expect(window.localStorage.getItem('ov_stories_default_sample_v2')).toBe('1');
  });

  it('keeps the script when the confirmation is declined', async () => {
    askConfirm.mockResolvedValue(false);
    renderEditor();
    fireEvent.click(screen.getByRole('button', { name: /clear script/i }));

    await waitFor(() => expect(askConfirm).toHaveBeenCalledTimes(1));
    expect(useAppStore.getState().storyTracks).toEqual(TRACKS);
  });

  it('is disabled when the script is already empty', () => {
    // Returning user (sample already shown once), empty project.
    window.localStorage.setItem('ov_stories_default_sample_v2', '1');
    useAppStore.setState({ storyTracks: [] });
    renderEditor();
    expect(screen.getByRole('button', { name: /clear script/i })).toBeDisabled();
    expect(askConfirm).not.toHaveBeenCalled();
  });

  it('drops a preview that was still generating when the script was cleared', async () => {
    // Greptile on #2203: the line is gone by the time its audio arrives, so
    // nothing may play or be written back, and current playback stops.
    let resolveSpeech;
    generateSpeech.mockReturnValue(
      new Promise((resolve) => {
        resolveSpeech = resolve;
      }),
    );
    askConfirm.mockResolvedValue(true);
    renderEditor();
    fireEvent.click(screen.getAllByRole('button', { name: /preview this line/i })[0]);
    await waitFor(() => expect(generateSpeech).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /clear script/i }));
    await waitFor(() => expect(useAppStore.getState().storyTracks).toEqual([]));
    expect(stopActivePlayback).toHaveBeenCalledTimes(1);

    resolveSpeech({ blob: async () => new Blob([new Uint8Array(4)], { type: 'audio/wav' }) });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(playBlobAudio).not.toHaveBeenCalled();
    expect(useAppStore.getState().storyTracks).toEqual([]);
  });
  it.each(['A spoken line.', 'First [pause 0.1s] second.'])(
    'drops a removed line preview: %s',
    async (text) => {
      let resolveSpeech;
      generateSpeech.mockReturnValue(
        new Promise((resolve) => {
          resolveSpeech = resolve;
        }),
      );
      useAppStore.setState({ storyTracks: [{ ...TRACKS[1], text }] });
      renderEditor();
      fireEvent.click(screen.getByRole('button', { name: /preview this line/i }));
      await waitFor(() => expect(generateSpeech).toHaveBeenCalledTimes(1));
      fireEvent.click(screen.getByRole('button', { name: /remove line/i }));
      resolveSpeech({ blob: async () => new Blob(['audio']) });
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      expect(playBlobAudio).not.toHaveBeenCalled();
      expect(URL.createObjectURL).not.toHaveBeenCalled();
    },
  );

  it('stops playback when its line is removed', async () => {
    generateSpeech.mockResolvedValue({ blob: async () => new Blob(['audio']) });
    useAppStore.setState({ storyTracks: [TRACKS[1]] });
    renderEditor();
    fireEvent.click(screen.getByRole('button', { name: /preview this line/i }));
    await waitFor(() => expect(playBlobAudio).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: /remove line/i }));
    expect(stopActivePlayback).toHaveBeenCalledTimes(1);
  });

  it('releases every current preview URL after confirmation', async () => {
    let confirm;
    askConfirm.mockReturnValue(
      new Promise((resolve) => {
        confirm = resolve;
      }),
    );
    useAppStore.setState({
      storyTracks: TRACKS.map((track) => ({
        ...track,
        audioUrl: `blob:${track.id}`,
      })),
    });
    renderEditor();
    fireEvent.click(screen.getByRole('button', { name: /clear script/i }));
    // A preview can finish while the confirmation is open; clear the live state.
    act(() =>
      useAppStore.setState((state) => ({
        storyTracks: state.storyTracks.map((track) =>
          track.id === 't2' ? { ...track, audioUrl: 'blob:latest' } : track,
        ),
      })),
    );
    confirm(true);
    await waitFor(() => expect(useAppStore.getState().storyTracks).toEqual([]));
    expect(URL.revokeObjectURL.mock.calls.map(([url]) => url).sort()).toEqual(
      ['blob:t1', 'blob:latest', 'blob:t3'].sort(),
    );
  });

  it('releases the previous preview when replacing it', async () => {
    useAppStore.setState({ storyTracks: [{ ...TRACKS[1], audioUrl: 'blob:previous' }] });
    generateSpeech.mockResolvedValue({ blob: async () => new Blob(['audio']) });
    renderEditor();
    fireEvent.click(screen.getByRole('button', { name: /preview this line/i }));
    await waitFor(() => expect(playBlobAudio).toHaveBeenCalledTimes(1));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:previous');
    expect(useAppStore.getState().storyTracks[0].audioUrl).toBe('blob:new-preview');
  });

  it('clears pending paste text even before any lines have been added', async () => {
    window.localStorage.setItem('ov_stories_default_sample_v2', '1');
    useAppStore.setState({ storyTracks: [] });
    askConfirm.mockResolvedValue(true);
    renderEditor();
    fireEvent.click(screen.getByRole('button', { name: /paste.*split/i }));
    const input = screen.getByRole('textbox', { name: /paste/i });
    fireEvent.change(input, { target: { value: 'Unsplit manuscript' } });
    expect(screen.getByRole('button', { name: /clear script/i })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /clear script/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /clear script/i })).toBeDisabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: /paste.*split/i }));
    expect(screen.getByRole('textbox', { name: /paste/i })).toHaveValue('');
    expect(askConfirm).toHaveBeenCalledWith(
      'Clear all 1 script entries and any pending imported text? This cannot be undone.',
      'Clear script',
    );
    expect(useAppStore.getState().cast).toEqual(CAST);
  });
});
