import React from 'react';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '../i18n';

const doubles = vi.hoisted(() => ({
  synthesize: vi.fn(),
  play: vi.fn(),
  stop: vi.fn(),
  confirm: vi.fn(),
}));

vi.mock('../api/generate', () => ({
  generateSpeech: doubles.synthesize,
  audioUrl: (value) => value,
}));
vi.mock('../utils/media', () => ({ playBlobAudio: doubles.play }));
vi.mock('../utils/playback', () => ({ stopActivePlayback: doubles.stop }));
vi.mock('../utils/dialog', () => ({ askConfirm: doubles.confirm }));
vi.mock('../api/hooks', () => ({ useArchetypes: vi.fn(() => ({ data: undefined })) }));
vi.mock('../api/archetypes', () => ({ useArchetypeAsProfile: vi.fn() }));

import StoriesEditor from '../components/StoriesEditor';
import { useAppStore } from '../store';

const castFixture = [
  { id: 'reader', name: 'Night Reader', color: '#5b8def', profileId: 'profile-reader' },
];

function storyFixture() {
  return [
    { id: 701, character: 'reader', text: '# Observatory', profileId: null, audioUrl: null },
    { id: 702, character: 'reader', text: 'The dome opened.', profileId: null, audioUrl: null },
    {
      id: 703,
      character: 'reader',
      text: 'Stars appeared [pause 0.4s] overhead.',
      profileId: null,
      audioUrl: null,
    },
  ];
}

function putStoryInStore(tracks = storyFixture()) {
  useAppStore.setState({
    cast: castFixture,
    storyTracks: tracks,
    storyProjects: [],
    currentProjectId: null,
  });
}

function mountSubject() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <StoriesEditor profiles={[{ id: 'profile-reader', name: 'Studio Reader' }]} />
    </QueryClientProvider>,
  );
}

function clearControl() {
  return screen.getByRole('button', { name: 'Erase script' });
}

function controlledPromise() {
  let settle;
  const value = new Promise((resolve) => {
    settle = resolve;
  });
  return { value, settle };
}

describe('removing content from the Stories editor', () => {
  beforeEach(() => {
    localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:replacement-preview'),
      revokeObjectURL: vi.fn(),
    });
    for (const double of Object.values(doubles)) double.mockReset();
    doubles.play.mockResolvedValue(undefined);
    doubles.synthesize.mockResolvedValue({ blob: async () => new Blob(['voice sample']) });
    putStoryInStore();
  });

  test('an empty returning project cannot request a reset', () => {
    localStorage.setItem('ov_stories_default_sample_v2', '1');
    putStoryInStore([]);
    mountSubject();

    expect(clearControl()).toBeDisabled();
    expect(doubles.confirm).not.toHaveBeenCalled();
  });

  test('rejecting the prompt retains the complete manuscript', async () => {
    doubles.confirm.mockResolvedValue(false);
    mountSubject();

    fireEvent.click(clearControl());
    await waitFor(() => expect(doubles.confirm).toHaveBeenCalledTimes(1));

    expect(useAppStore.getState().storyTracks.map((track) => track.id)).toEqual([701, 702, 703]);
    expect(doubles.confirm.mock.calls[0][1]).toBe('Erase script');
  });

  test('pending split text alone can be discarded', async () => {
    localStorage.setItem('ov_stories_default_sample_v2', '1');
    putStoryInStore([]);
    doubles.confirm.mockResolvedValue(true);
    mountSubject();

    fireEvent.click(screen.getByRole('button', { name: 'Paste & Split' }));
    const splitBox = document.querySelector('.stories-split-panel textarea');
    fireEvent.change(splitBox, { target: { value: 'Uncommitted paragraph' } });
    expect(clearControl()).toBeEnabled();

    fireEvent.click(clearControl());
    await waitFor(() => expect(splitBox).not.toBeInTheDocument());

    const [question, heading] = doubles.confirm.mock.calls.at(-1);
    expect(question).toContain('1');
    expect(heading).toBe('Erase script');
    expect(clearControl()).toBeDisabled();
    expect(useAppStore.getState().cast).toStrictEqual(castFixture);
  });

  test('accepting the prompt empties tracks without replacing the cast or restoring samples', async () => {
    doubles.confirm.mockResolvedValue(true);
    mountSubject();

    fireEvent.click(clearControl());
    await waitFor(() => expect(useAppStore.getState().storyTracks).toHaveLength(0));

    expect(doubles.confirm).toHaveBeenCalledTimes(1);
    expect(doubles.confirm.mock.calls[0][0]).toContain('3');
    expect(useAppStore.getState().cast).toStrictEqual(castFixture);
    expect(localStorage.getItem('ov_stories_default_sample_v2')).toBe('1');

    await new Promise((done) => setTimeout(done, 20));
    expect(useAppStore.getState().storyTracks).toHaveLength(0);
  });

  test('confirmation cleanup observes preview URLs added while the prompt is open', async () => {
    const decision = controlledPromise();
    const tracks = storyFixture();
    tracks[1].audioUrl = 'blob:already-present';
    putStoryInStore(tracks);
    doubles.confirm.mockReturnValue(decision.value);
    mountSubject();

    fireEvent.click(clearControl());
    act(() => {
      const latest = useAppStore
        .getState()
        .storyTracks.map((track) =>
          track.id === 703 ? { ...track, audioUrl: 'blob:arrived-late' } : track,
        );
      useAppStore.getState().setStoryTracks(latest);
    });
    decision.settle(true);
    await waitFor(() => expect(useAppStore.getState().storyTracks).toEqual([]));

    expect(URL.revokeObjectURL.mock.calls.map(([url]) => url).sort()).toEqual([
      'blob:already-present',
      'blob:arrived-late',
    ]);
  });

  test('creating a replacement preview revokes the track’s earlier URL', async () => {
    const tracks = storyFixture();
    tracks[1].audioUrl = 'blob:stale-preview';
    putStoryInStore(tracks);
    mountSubject();

    fireEvent.click(screen.getAllByRole('button', { name: 'Preview this line' })[0]);
    await waitFor(() =>
      expect(useAppStore.getState().storyTracks[1].audioUrl).toBe('blob:replacement-preview'),
    );

    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenLastCalledWith('blob:stale-preview');
  });

  test('removing the line currently playing requests playback shutdown', async () => {
    doubles.play.mockReturnValue(new Promise(() => {}));
    const tracks = storyFixture();
    tracks[1].audioUrl = 'blob:active-preview';
    putStoryInStore(tracks);
    mountSubject();

    fireEvent.click(screen.getAllByRole('button', { name: 'Preview this line' })[0]);
    await waitFor(() => expect(doubles.play).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove line' })[1]);

    expect(doubles.stop).toHaveBeenCalledTimes(1);
  });

  test.each([
    { spokenButton: 0, removeButton: 1, kind: 'plain speech' },
    { spokenButton: 1, removeButton: 2, kind: 'speech containing a pause' },
  ])(
    'a pending preview for removed $kind never reaches playback',
    async ({ spokenButton, removeButton }) => {
      const synthesis = controlledPromise();
      doubles.synthesize.mockReturnValue(synthesis.value);
      mountSubject();

      fireEvent.click(screen.getAllByRole('button', { name: 'Preview this line' })[spokenButton]);
      fireEvent.click(screen.getAllByRole('button', { name: 'Remove line' })[removeButton]);
      synthesis.settle({ blob: async () => new Blob(['late audio']) });
      await act(async () => synthesis.value);

      expect(URL.createObjectURL).not.toHaveBeenCalled();
      expect(doubles.play).not.toHaveBeenCalled();
    },
  );

  test('resetting during synthesis invalidates the result and stops existing playback', async () => {
    const synthesis = controlledPromise();
    doubles.synthesize.mockReturnValue(synthesis.value);
    doubles.confirm.mockResolvedValue(true);
    mountSubject();

    fireEvent.click(screen.getAllByRole('button', { name: 'Preview this line' })[0]);
    fireEvent.click(clearControl());
    await waitFor(() => expect(useAppStore.getState().storyTracks).toEqual([]));
    synthesis.settle({ blob: async () => new Blob(['obsolete audio']) });
    await act(async () => synthesis.value);

    expect(doubles.stop).toHaveBeenCalledTimes(1);
    expect(doubles.play).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
