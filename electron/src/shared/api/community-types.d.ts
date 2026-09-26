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
}

export interface CommunityPage {
  total: number;
  limit: number;
  offset: number;
  items: CommunityItem[];
}

export interface CommunityFilters {
  use_case?: string | null;
  gender?: string | null;
  type?: string | null;
  lang?: string | null;
  q?: string | null;
  limit?: number;
  offset?: number;
  refresh?: boolean;
}
