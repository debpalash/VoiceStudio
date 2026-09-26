/**
 * Community gallery (marketplace) API — designed presets + recorded voices
 * loaded from the omnivoice-gallery content repo via the backend
 * (CDN-fetched, cached, validated). See backend/api/routers/community.py.
 */
import { apiJson } from './client';

interface CommunityItem {
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

export const listCommunityItems = (filters: CommunityFilters = {}): Promise<CommunityPage> => {
  const qs = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  });
  const q = qs.toString();
  return apiJson(`/community/items${q ? `?${q}` : ''}`);
};

export const communitySubmitUrl = (type: 'preset' | 'voice'): Promise<{ url: string }> =>
  apiJson(`/community/submit-url?type=${type}`);

export const addCommunityItem = (
  id: string,
  name?: string,
): Promise<{ profile_id: string; name: string }> => {
  const q = name ? `?name=${encodeURIComponent(name)}` : '';
  return apiJson(`/community/items/${encodeURIComponent(id)}/use${q}`, { method: 'POST' });
};

/** Same-origin preview path for both designed presets and recorded voices. */
export const communityPreviewUrl = (id: string, local = false): string =>
  `/community/items/${encodeURIComponent(id)}/preview${local ? '?local=true' : ''}`;
