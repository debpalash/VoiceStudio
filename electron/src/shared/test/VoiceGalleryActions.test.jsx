import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const state = {
    galleryZone: 'archetypes',
    setGalleryZone: vi.fn(),
    archetypeFilters: {},
    setArchetypeFilter: vi.fn(),
    resetArchetypeFilters: vi.fn(),
    favoriteArchetypeIds: [],
    toggleFavoriteArchetype: vi.fn(),
    galleryViewMode: 'grid',
    setGalleryViewMode: vi.fn(),
    setMode: vi.fn(),
    setDefineMethod: vi.fn(),
    setPendingProfileId: vi.fn(),
    setInstruct: vi.fn(),
    setVdStates: vi.fn(),
    setLanguage: vi.fn(),
    upsertCastMember: vi.fn(),
    setOutputPrefs: vi.fn(),
    convertMode: vi.fn(),
    cast: [{ id: 'narrator', name: 'Narrator', color: '#fabd2f', profileId: null }],
  };
  return {
    state,
    archetype: {
      id: 'a1',
      name: 'Fresh Voice',
      instruct: 'female, high pitch, american accent',
      language: 'Spanish',
    },
    archetypeB: {
      id: 'a2',
      name: 'Second Voice',
      instruct: 'male, low pitch, british accent',
      language: 'English',
    },
    community: {
      id: 'c1',
      name: 'Community Voice',
      type: 'preset',
      source: 'community',
      instruct: 'female, high pitch, american accent',
      language: 'Spanish',
      audio: { url: 'https://github.com/should-not-be-fetched-directly.wav' },
    },
    addCommunityItem: vi.fn(),
    apiFetch: vi.fn(),
    playBlobAudio: vi.fn(),
    stopActivePlayback: vi.fn(),
    useArchetypeAsProfile: vi.fn(),
  };
});

vi.mock('../store', () => {
  const useAppStore = (selector) => selector(mocks.state);
  useAppStore.getState = () => mocks.state;
  return { useAppStore };
});
vi.mock('../api/archetypes', () => ({
  archetypePreviewUrl: (id, local = false) =>
    `/archetypes/${id}/preview${local ? '?local=true' : ''}`,
  useArchetypeAsProfile: mocks.useArchetypeAsProfile,
}));
vi.mock('../api/community', () => ({
  addCommunityItem: mocks.addCommunityItem,
  communityPreviewUrl: (id, local = false) =>
    `/community/items/${id}/preview${local ? '?local=true' : ''}`,
}));
vi.mock('../api/gallery', () => ({ previewVoiceUrl: (id) => `/gallery/${id}/preview` }));
vi.mock('../api/client', () => ({ apiFetch: mocks.apiFetch }));
vi.mock('../utils/media', () => ({ playBlobAudio: mocks.playBlobAudio }));
vi.mock('../utils/playback', () => ({ stopActivePlayback: mocks.stopActivePlayback }));
vi.mock('../ui', () => ({ Segmented: () => null }));

vi.mock('../components/gallery/ArchetypesZone', () => ({
  default: (props) => (
    <div>
      <button onClick={() => props.onPreview(mocks.archetype)}>Preview archetype</button>
      <button onClick={() => props.onPreview(mocks.archetypeB)}>Preview archetype B</button>
      <button onClick={() => props.onUse(mocks.archetype)}>Use archetype</button>
      <button onClick={() => props.onDesign(mocks.archetype)}>Design archetype</button>
      <button onClick={() => props.onUseInStories(mocks.archetype)}>Stories archetype</button>
      <button onClick={() => props.onUseAsAudiobookDefault(mocks.archetype)}>
        Audiobook archetype
      </button>
    </div>
  ),
}));
vi.mock('../components/gallery/CommunityZone', () => ({
  default: (props) => (
    <div>
      <button onClick={() => props.onPreview(mocks.community)}>Preview community</button>
      <button onClick={() => props.onUse(mocks.community)}>Use community</button>
      <button onClick={() => props.onDesign(mocks.community)}>Design community</button>
    </div>
  ),
}));
vi.mock('../components/gallery/ImportsZone', () => ({ default: () => null }));

// App module is imported at test runtime (in beforeEach, after mocks reset) —
// never at module scope — so mocks are in place before the component loads.
let VoiceGallery;

const completeRecipe = {
  Gender: 'female',
  Age: 'Auto',
  Pitch: 'high pitch',
  Style: 'Auto',
  EnglishAccent: 'american accent',
  ChineseDialect: 'Auto',
};

describe('VoiceGallery persona actions', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    mocks.state.galleryZone = 'archetypes';
    mocks.state.cast = [{ id: 'narrator', name: 'Narrator', color: '#fabd2f', profileId: null }];
    mocks.apiFetch.mockResolvedValue({ blob: () => Promise.resolve(new Blob()) });
    mocks.playBlobAudio.mockResolvedValue(undefined);
    ({ default: VoiceGallery } = await import('../pages/VoiceGallery'));
  });

  it('opens the wand with a fresh complete recipe, language, and no stale profile', () => {
    const clearSelectedProfile = vi.fn();
    render(<VoiceGallery clearSelectedProfile={clearSelectedProfile} />);

    fireEvent.click(screen.getByText('Design archetype'));

    expect(clearSelectedProfile).toHaveBeenCalledOnce();
    expect(mocks.state.setPendingProfileId).toHaveBeenCalledWith(null);
    expect(mocks.state.setVdStates).toHaveBeenCalledWith(completeRecipe);
    expect(mocks.state.setInstruct).toHaveBeenCalledWith('');
    expect(mocks.state.setLanguage).toHaveBeenCalledWith('Spanish');
    expect(mocks.state.setDefineMethod).toHaveBeenCalledWith('design');
    expect(mocks.state.setMode).toHaveBeenCalledWith('studio');
  });

  it('materializes once on a double click, then selects the profile in Studio', async () => {
    let finish;
    mocks.useArchetypeAsProfile.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<VoiceGallery />);

    const button = screen.getByText('Use archetype');
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mocks.useArchetypeAsProfile).toHaveBeenCalledOnce();

    finish({ profile_id: 'profile-1', name: 'Server Voice' });
    await waitFor(() => expect(mocks.state.setPendingProfileId).toHaveBeenCalledWith('profile-1'));
    expect(mocks.state.setMode).toHaveBeenCalledWith('studio');
  });

  it('hands a materialized voice directly to Stories and Audiobook', async () => {
    mocks.useArchetypeAsProfile
      .mockResolvedValueOnce({ profile_id: 'story-1', name: 'Story Voice' })
      .mockResolvedValueOnce({ profile_id: 'book-1', name: 'Book Voice' });
    render(<VoiceGallery />);

    fireEvent.click(screen.getByText('Stories archetype'));
    await waitFor(() => expect(mocks.state.convertMode).toHaveBeenCalledWith('stories'));
    expect(mocks.state.upsertCastMember).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'voice_story-1',
        name: 'Story Voice',
        profileId: 'story-1',
      }),
    );
    expect(mocks.state.setMode).toHaveBeenCalledWith('stories');

    fireEvent.click(screen.getByText('Audiobook archetype'));
    await waitFor(() => expect(mocks.state.convertMode).toHaveBeenCalledWith('audiobook'));
    expect(mocks.state.setOutputPrefs).toHaveBeenCalledWith({ defaultVoice: 'book-1' });
    expect(mocks.state.setMode).toHaveBeenCalledWith('audiobook');
  });

  it('previews and uses Community personas only through the local backend', async () => {
    mocks.state.galleryZone = 'community';
    mocks.addCommunityItem.mockResolvedValue({ profile_id: 'community-1', name: 'Community' });
    render(<VoiceGallery />);

    fireEvent.click(screen.getByText('Preview community'));
    await waitFor(() =>
      expect(mocks.apiFetch).toHaveBeenCalledWith('/community/items/c1/preview', {
        cache: 'no-store',
      }),
    );
    expect(mocks.apiFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('github.com'),
      expect.anything(),
    );

    fireEvent.click(screen.getByText('Use community'));
    await waitFor(() =>
      expect(mocks.addCommunityItem).toHaveBeenCalledWith('c1', 'Community Voice'),
    );
    expect(mocks.state.setPendingProfileId).toHaveBeenCalledWith('community-1');
  });

  it('drops a preview that resolves after unmount (no playback, no state)', async () => {
    let resolveFetch;
    mocks.apiFetch.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );
    const { unmount } = render(<VoiceGallery />);

    fireEvent.click(screen.getByText('Preview archetype'));
    unmount();
    resolveFetch({ blob: () => Promise.resolve(new Blob()) });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mocks.playBlobAudio).not.toHaveBeenCalled();
  });

  it('drops a materialization that resolves after unmount (no late navigation)', async () => {
    let finish;
    mocks.useArchetypeAsProfile.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { unmount } = render(<VoiceGallery />);

    fireEvent.click(screen.getByText('Use archetype'));
    unmount();
    finish({ profile_id: 'late-1', name: 'Late Voice' });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mocks.state.setPendingProfileId).not.toHaveBeenCalled();
    expect(mocks.state.setMode).not.toHaveBeenCalled();
  });

  it('ignores a decode-error retry once a newer preview superseded it', async () => {
    // First preview: capture onDone so the decode error can fire late.
    let firstDone;
    mocks.playBlobAudio.mockImplementationOnce(async (_blob, meta) => {
      firstDone = meta.onDone;
    });
    render(<VoiceGallery />);

    fireEvent.click(screen.getByText('Preview archetype'));
    await waitFor(() => expect(firstDone).toBeDefined());

    fireEvent.click(screen.getByText('Preview archetype B'));
    await waitFor(() =>
      expect(mocks.apiFetch).toHaveBeenCalledWith('/archetypes/a2/preview', { cache: 'no-store' }),
    );

    firstDone('error');
    await new Promise((resolve) => setTimeout(resolve, 0));

    // The superseded preview must not restart old playback via its fallback.
    expect(mocks.apiFetch).not.toHaveBeenCalledWith(
      '/archetypes/a1/preview?local=true',
      expect.anything(),
    );
  });

  it('does not navigate when materialization fails', async () => {
    mocks.useArchetypeAsProfile.mockRejectedValue(new Error('engine unavailable'));
    render(<VoiceGallery />);

    fireEvent.click(screen.getByText('Use archetype'));
    await screen.findByText(/engine unavailable/);

    expect(mocks.state.setPendingProfileId).not.toHaveBeenCalled();
    expect(mocks.state.setMode).not.toHaveBeenCalled();
  });
});
