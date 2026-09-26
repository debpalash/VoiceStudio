/**
 * Archetype gallery API — the catalog of *designed* voices (no real people).
 *
 * Backed by core.archetypes via backend/api/routers/archetypes.py. The catalog
 * is large (hundreds of voices) and static, so callers should cache
 * aggressively (see api/hooks.ts staleTime).
 */
import { apiJson, apiUrl } from './client';

interface ArchetypeFacets {
  gender: string | null;
  age: string | null;
  pitch: string | null;
  accent: string | null;
  whisper: boolean;
  lang: string;
}

interface Archetype {
  id: string;
  name: string;
  icon: string;
  use_case: string;
  instruct: string;
  attrs: Record<string, string>;
  facets: ArchetypeFacets;
  sample_script: string;
  preview_url: string | null;
  is_featured: boolean;
  language: string;
}

export interface ArchetypeCategory {
  id: string;
  name: string;
  icon: string;
}

export interface ArchetypePage {
  total: number;
  limit: number;
  offset: number;
  items: Archetype[];
}

export interface ArchetypeFilters {
  /** Free-text substring match over name/instruct — the picker search box. */
  q?: string | null;
  use_case?: string | null;
  gender?: string | null;
  age?: string | null;
  pitch?: string | null;
  accent?: string | null;
  whisper?: boolean | null;
  lang?: string | null;
  featured?: boolean | null;
  limit?: number;
  offset?: number;
}

export const listArchetypeCategories = (): Promise<ArchetypeCategory[]> =>
  apiJson('/archetypes/categories');

export const listArchetypes = (filters: ArchetypeFilters = {}): Promise<ArchetypePage> => {
  const qs = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  });
  const q = qs.toString();
  return apiJson(`/archetypes${q ? `?${q}` : ''}`);
};

/** Full URL for an archetype preview clip (use as an <audio> src). */
export const archetypePreviewUrl = (id: string, local = false): string =>
  apiUrl(`/archetypes/${encodeURIComponent(id)}/preview${local ? '?local=true' : ''}`);

/** Materialize an archetype into a reusable voice profile. */
export const useArchetypeAsProfile = (
  id: string,
  name?: string,
): Promise<{ profile_id: string; name: string }> => {
  const q = name ? `?name=${encodeURIComponent(name)}` : '';
  return apiJson(`/archetypes/${encodeURIComponent(id)}/use${q}`, { method: 'POST' });
};
