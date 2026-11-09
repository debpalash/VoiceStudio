export interface CommunityItem {
  id: string;
  type: 'preset' | 'voice';
  name: string;
  icon: string;
  use_case: string;
  facets: Record<string, any>;
  instruct?: string;
  language: string;
  sample_script?: string;
  audio?: { url: string; ref_text?: string; duration?: number; sha256?: string };
  author?: string;
  license?: string;
  source?: string;
  _source_repo?: string;
  is_community?: boolean;
  attrs?: Record<string, string>;
  /** ISO date (YYYY-MM-DD) the item joined the gallery; drives the "new" badge. */
  added_at?: string;
}

export interface CommunityPack {
  id: string;
  name: string;
  description?: string;
  count: number;
}

export interface CommunityPage {
  total: number;
  limit: number;
  offset: number;
  items: CommunityItem[];
  /** Filter options for the whole catalogue, independent of the page's filters. */
  packs?: CommunityPack[];
  languages?: string[];
}

export interface CommunityFilters {
  use_case?: string | null;
  gender?: string | null;
  type?: string | null;
  lang?: string | null;
  q?: string | null;
  pack?: string | null;
  limit?: number;
  offset?: number;
  refresh?: boolean;
}
