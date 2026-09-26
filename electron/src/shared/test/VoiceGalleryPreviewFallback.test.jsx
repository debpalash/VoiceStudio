import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiFetch, playBlobAudio } = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  playBlobAudio: vi.fn(),
}));

vi.mock('../utils/media', () => ({ playBlobAudio }));
vi.mock('../utils/playback', () => ({ stopActivePlayback: vi.fn() }));
vi.mock('../api/archetypes', () => ({
  archetypePreviewUrl: (id, local = false) =>
    `http://backend/archetypes/${id}/preview${local ? '?local=true' : ''}`,
  useArchetypeAsProfile: vi.fn(),
}));
vi.mock('../api/gallery', () => ({ previewVoiceUrl: vi.fn() }));
vi.mock('../api/community', () => ({
  addCommunityItem: vi.fn(),
  communityPreviewUrl: (id, local = false) =>
    `/community/items/${id}/preview${local ? '?local=true' : ''}`,
}));
vi.mock('../api/client', () => ({ apiFetch }));
vi.mock('../store', () => ({
  useAppStore: (selector) =>
    selector({
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
      vdStates: {},
    }),
}));
vi.mock('../components/gallery/ArchetypesZone', () => ({
  default: ({ onPreview }) => (
    <button onClick={() => onPreview({ id: 'voice-1', name: 'Voice One' })}>Preview</button>
  ),
}));
vi.mock('../components/gallery/CommunityZone', () => ({ default: () => null }));
vi.mock('../components/gallery/ImportsZone', () => ({ default: () => null }));
vi.mock('../ui', () => ({ Segmented: () => null }));

import VoiceGallery from '../pages/VoiceGallery';

describe('VoiceGallery archetype preview fallback', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiFetch.mockResolvedValue({ blob: () => Promise.resolve(new Blob()) });
  });

  it('retries a gallery decode error through the local-render endpoint', async () => {
    playBlobAudio
      .mockImplementationOnce(async (_blob, meta) => meta.onDone('error'))
      .mockImplementationOnce(async () => {});

    fireEvent.click(render(<VoiceGallery />).getByText('Preview'));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2));
    expect(apiFetch.mock.calls.map(([url]) => url)).toEqual([
      'http://backend/archetypes/voice-1/preview',
      'http://backend/archetypes/voice-1/preview?local=true',
    ]);
    expect(screen.queryByText(/Preview unavailable/)).not.toBeInTheDocument();
  });
});
