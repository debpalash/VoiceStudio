/**
 * Voice-gallery slice — favorites, filters, and view state for the archetype
 * gallery (mode === 'gallery').
 *
 * Archetypes are not database rows (they're generated/curated server-side), so
 * the user's *favorites* and *filter* selections live here on the client.
 * Favorites + view mode are persisted (see store/index.ts partialize); the
 * active zone/filters are session preferences that also persist so the gallery
 * reopens where you left it.
 */
import type { StateCreator } from 'zustand';

type GalleryZone = 'archetypes' | 'imports' | 'community';

interface ArchetypeFilterState {
  use_case: string | null;
  gender: string | null;
  age: string | null;
  pitch: string | null;
  accent: string | null;
  whisper: boolean | null;
  lang: string | null;
}

const EMPTY_ARCHETYPE_FILTERS: ArchetypeFilterState = {
  use_case: null,
  gender: null,
  age: null,
  pitch: null,
  accent: null,
  whisper: null,
  lang: null,
};

export interface GallerySlice {
  galleryZone: GalleryZone;
  archetypeFilters: ArchetypeFilterState;
  favoriteArchetypeIds: string[];
  galleryViewMode: 'grid' | 'list';

  setGalleryZone: (zone: GalleryZone) => void;
  setArchetypeFilter: <K extends keyof ArchetypeFilterState>(
    key: K,
    value: ArchetypeFilterState[K],
  ) => void;
  resetArchetypeFilters: () => void;
  toggleFavoriteArchetype: (id: string) => void;
  isFavoriteArchetype: (id: string) => boolean;
  setGalleryViewMode: (mode: 'grid' | 'list') => void;
}

export const createGallerySlice: StateCreator<GallerySlice, [], [], GallerySlice> = (
  set,
  get,
  _store,
) => ({
  galleryZone: 'archetypes',
  archetypeFilters: { ...EMPTY_ARCHETYPE_FILTERS },
  favoriteArchetypeIds: [],
  galleryViewMode: 'grid',

  setGalleryZone: (zone) => set({ galleryZone: zone }),
  setArchetypeFilter: (key, value) =>
    set((s) => ({ archetypeFilters: { ...s.archetypeFilters, [key]: value } })),
  resetArchetypeFilters: () => set({ archetypeFilters: { ...EMPTY_ARCHETYPE_FILTERS } }),
  toggleFavoriteArchetype: (id) =>
    set((s) => ({
      favoriteArchetypeIds: s.favoriteArchetypeIds.includes(id)
        ? s.favoriteArchetypeIds.filter((x) => x !== id)
        : [...s.favoriteArchetypeIds, id],
    })),
  isFavoriteArchetype: (id) => get().favoriteArchetypeIds.includes(id),
  setGalleryViewMode: (mode) => set({ galleryViewMode: mode }),
});
