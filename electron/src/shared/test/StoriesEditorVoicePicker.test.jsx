import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '../i18n';

const generationApi = vi.hoisted(() => ({
  generateSpeech: vi.fn(),
}));
vi.mock('../api/generate', () => ({
  generateSpeech: generationApi.generateSpeech,
  audioUrl: (path) => path,
}));
vi.mock('../utils/media', () => ({
  playBlobAudio: vi.fn(() => Promise.resolve()),
}));

// The Stories cast + per-line pickers migrated from native <select>s to the
// shared, gallery-enabled VoiceSelector (#1220). VoiceSelector reads /archetypes
// and materializes gallery picks — mock both so the editor renders standalone.
vi.mock('../api/hooks', () => ({ useArchetypes: vi.fn(() => ({ data: undefined })) }));
vi.mock('../api/archetypes', () => ({ useArchetypeAsProfile: vi.fn() }));

import StoriesEditor from '../components/StoriesEditor';
import { useAppStore } from '../store';

const PROFILES = [{ id: 'p_clone', name: 'Aria' }];

function seedStore() {
  useAppStore.setState({
    cast: [{ id: 'narrator', name: 'Narrator', color: '#b8bb26', profileId: null }],
    storyTracks: [
      {
        id: 1,
        character: 'narrator',
        text: 'Once upon a time',
        profileId: null,
        emotion: null,
        speed: null,
        generating: false,
        audioUrl: null,
      },
    ],
    storyProjects: [],
    currentProjectId: null,
  });
}

function renderEditor() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <StoriesEditor profiles={PROFILES} />
    </QueryClientProvider>,
  );
}

describe('StoriesEditor voice pickers (#1220)', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    seedStore();
    generationApi.generateSpeech.mockReset();
    generationApi.generateSpeech.mockResolvedValue({
      blob: async () => new Blob(['audio'], { type: 'audio/wav' }),
    });
  });

  it('per-line picker renders VoiceSelector and stores the picked profile id', () => {
    renderEditor();
    const list = screen.getByRole('list');
    // The line-card voice picker trigger shows the default label.
    const trigger = within(list).getByRole('button', { name: /Default/ });
    fireEvent.click(trigger);
    fireEvent.mouseDown(screen.getByText('Aria'));
    expect(useAppStore.getState().storyTracks[0].profileId).toBe('p_clone');
  });

  it('cast picker renders VoiceSelector and stores the character voice', () => {
    renderEditor();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Cast' }), { button: 0 });
    const castRegion = screen.getByRole('tabpanel', { name: 'Cast' });
    const trigger = within(castRegion).getByRole('button', { name: /Default/ });
    fireEvent.click(trigger);
    fireEvent.mouseDown(screen.getByText('Aria'));
    expect(useAppStore.getState().cast[0].profileId).toBe('p_clone');

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Script' }), { button: 0 });
    expect(within(screen.getByRole('list')).getByRole('button', { name: /Aria/ })).toBeVisible();
  });

  it('defaults to Script and renders the project stats and line canvas', () => {
    renderEditor();
    expect(screen.getByRole('heading', { level: 1, name: /Untitled story/ })).toBeInTheDocument();
    expect(screen.getAllByText('1 lines').length).toBeGreaterThan(0);
    expect(screen.getByRole('tab', { name: 'Script' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('main')).toHaveClass('stories-manuscript');
    expect(screen.getByRole('list')).toHaveClass('stories-track-list');
    expect(screen.getByRole('listitem')).toHaveClass('stories-line');
  });

  it('preserves script edits while moving through the workspace tabs', () => {
    renderEditor();
    const line = screen.getByDisplayValue('Once upon a time');
    fireEvent.change(line, { target: { value: 'A revised opening line' } });

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Cast' }), { button: 0 });
    expect(screen.getByRole('tabpanel', { name: 'Cast' })).toBeVisible();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Export' }), { button: 0 });
    expect(screen.getByRole('tabpanel', { name: 'Export' })).toBeVisible();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Script' }), { button: 0 });

    expect(screen.getByDisplayValue('A revised opening line')).toBeVisible();
    expect(useAppStore.getState().storyTracks[0].text).toBe('A revised opening line');
  });

  it('supports keyboard navigation across all workspace tabs', async () => {
    renderEditor();
    const script = screen.getByRole('tab', { name: 'Script' });
    const cast = screen.getByRole('tab', { name: 'Cast' });
    const projects = screen.getByRole('tab', { name: 'Projects' });

    script.focus();
    fireEvent.keyDown(script, { key: 'ArrowRight' });
    await waitFor(() => expect(cast).toHaveFocus());
    expect(script).toHaveAttribute('aria-selected', 'true');
    expect(cast).toHaveAttribute('aria-selected', 'false');

    fireEvent.keyDown(cast, { key: 'Enter' });
    expect(cast).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(cast, { key: 'End' });
    await waitFor(() => expect(projects).toHaveFocus());
    expect(cast).toHaveAttribute('aria-selected', 'true');
    expect(projects).toHaveAttribute('aria-selected', 'false');
    fireEvent.keyDown(projects, { key: 'Enter' });
    expect(projects).toHaveAttribute('aria-selected', 'true');
  });

  it('loads a comprehensive working sample by default', async () => {
    useAppStore.setState({ storyTracks: [], storyProjects: [], currentProjectId: null });
    renderEditor();

    await waitFor(() => expect(useAppStore.getState().storyTracks).toHaveLength(11));
    const state = useAppStore.getState();
    expect(state.cast.map((member) => member.name)).toEqual(['Narrator', 'Mara', 'Cole']);
    expect(state.cast.every((member) => member.profileId === 'p_clone')).toBe(true);
    expect(state.storyProjects.at(-1)?.name).toBe("The Lighthouse at Wits' End");
    expect(
      screen.getByRole('heading', { name: "The Lighthouse at Wits' End" }),
    ).toBeInTheDocument();
    expect(state.storyTracks.filter((track) => track.text.startsWith('#'))).toHaveLength(2);
    expect(state.storyTracks.some((track) => track.text.includes('[pause'))).toBe(true);
  });

  it('does not restore sample voices after the user clears them', async () => {
    useAppStore.setState({ storyTracks: [], storyProjects: [], currentProjectId: null });
    renderEditor();
    await waitFor(() => expect(useAppStore.getState().storyTracks).toHaveLength(11));
    const sampleMemberIds = useAppStore.getState().cast.map((member) => member.id);
    expect(sampleMemberIds).toEqual(['narrator', 'mara', 'cole']);

    act(() => {
      useAppStore
        .getState()
        .setCast(useAppStore.getState().cast.map((member) => ({ ...member, profileId: null })));
    });
    await act(async () => {});

    const settledCast = useAppStore.getState().cast;
    expect(settledCast.map((member) => member.id)).toEqual(sampleMemberIds);
    expect(settledCast.every((member) => member.profileId === null)).toBe(true);
  });

  it('makes only the drag handle draggable so text remains selectable', () => {
    const { container } = renderEditor();
    const row = screen.getByRole('listitem');

    expect(row).not.toHaveAttribute('draggable');
    expect(within(row).getByRole('textbox')).not.toHaveAttribute('draggable');
    expect(container.querySelector('.stories-line__drag')).toHaveAttribute('draggable', 'true');
  });

  it('serializes marker preview chunks through the shared generation admission', async () => {
    let releaseFirst;
    generationApi.generateSpeech.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseFirst = () =>
            resolve({ blob: async () => new Blob(['first'], { type: 'audio/wav' }) });
        }),
    );
    useAppStore.setState({
      storyTracks: [
        {
          ...useAppStore.getState().storyTracks[0],
          text: 'First chunk [pause 1ms] second chunk',
        },
      ],
    });
    renderEditor();

    fireEvent.click(screen.getByRole('button', { name: 'Preview this line' }));
    await waitFor(() => expect(generationApi.generateSpeech).toHaveBeenCalledTimes(1));

    await act(async () => {
      releaseFirst();
    });
    await waitFor(() => expect(generationApi.generateSpeech).toHaveBeenCalledTimes(2));
  });
});
