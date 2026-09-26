import { apiJson } from '@/lib/api/client';
import type { ArchetypePage } from '@shared/api/archetypes-types';
export const FAVORITES_KEY = 'voicestudio.gallery.favorites.v1';
export function readFavorites(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]');
    return Array.isArray(value)
      ? [...new Set(value.filter((id): id is string => typeof id === 'string'))]
      : [];
  } catch {
    return [];
  }
}
/** Favorites must cover the whole filtered catalogue, not just its first page. */
export async function favoriteCatalogue(filters: Record<string, string>, signal: AbortSignal) {
  const items: ArchetypePage['items'] = [];
  let offset = 0;
  while (!signal.aborted) {
    const page = await apiJson<ArchetypePage>(
      '/archetypes?' + new URLSearchParams({ ...filters, limit: '500', offset: String(offset) }),
      { signal },
    );
    items.push(...page.items);
    offset += page.items.length;
    if (!page.items.length || offset >= page.total) return items;
  }
  throw new DOMException('Aborted', 'AbortError');
}
