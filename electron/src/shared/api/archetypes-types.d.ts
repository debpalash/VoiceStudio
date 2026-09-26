export interface ArchetypeFacets {
  gender: string | null;
  age: string | null;
  pitch: string | null;
  accent: string | null;
  whisper: boolean;
  lang: string;
}

export interface Archetype {
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
