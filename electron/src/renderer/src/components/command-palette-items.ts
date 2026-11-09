import type { LucideIcon } from 'lucide-react';
import type { HistoryItem } from '@/lib/api/types';
import { displayTitle } from '@/features/clone/format';

export interface CommandItem {
  id: string;
  label: string;
  group: string;
  icon: LucideIcon;
  shortcut?: string;
  /** Secondary text on the right, such as a take's age and length. */
  detail?: string;
  /**
   * Extra words the item answers to: short page names, the route, and the
   * English names people type from muscle memory ("clone", "models", "dark").
   */
  keywords?: readonly string[];
  run: () => void | Promise<void>;
}

const fold = (value: string) => value.toLocaleLowerCase().normalize('NFKD').replace(/\p{M}/gu, '');

/**
 * Every word of the query must appear somewhere in the item's label, keywords
 * or id, in any order: "new project" and "project new" both find New project.
 */
export function commandMatches(item: CommandItem, query: string): boolean {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const haystack = fold([item.label, item.id, ...(item.keywords ?? [])].join(' '));
  return words.every((word) => haystack.includes(word));
}

/** How many recent takes the palette lists; the full list lives in History. */
export const RECENT_TAKE_LIMIT = 5;

/**
 * Recent Clone and Design takes, newest first, one row per distinct script:
 * regenerating the same line six times is one entry, not six.
 */
export function recentTakes(
  history: readonly HistoryItem[],
  limit = RECENT_TAKE_LIMIT,
): HistoryItem[] {
  const seen = new Set<string>();
  const stamp = (item: HistoryItem) =>
    typeof item.created_at === 'number' ? item.created_at : Date.parse(item.created_at) / 1000 || 0;
  const picked: HistoryItem[] = [];
  for (const item of [...history].sort((a, b) => stamp(b) - stamp(a))) {
    if (item.mode !== 'clone' && item.mode !== 'design') continue;
    const key = `${item.mode}:${fold(displayTitle(item.text)).replace(/\s+/g, ' ').trim()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    picked.push(item);
    if (picked.length >= limit) break;
  }
  return picked;
}

/** "1:05" style length for a take; empty when the backend did not record one. */
export function takeLength(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return '';
  const whole = Math.max(1, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}
