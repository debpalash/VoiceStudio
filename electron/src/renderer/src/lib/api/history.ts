import { apiFetch, apiJson } from './client';
import type { HistoryItem } from './types';

export interface StarredResponse {
  id: string;
  starred: boolean;
}

/** The list clips book-length scripts so every navigation does not parse megabytes (#4483). */
export const HISTORY_LIST_TEXT_LIMIT = 2000;

export async function listHistory(): Promise<HistoryItem[]> {
  return apiJson<HistoryItem[]>(`/history?text_limit=${HISTORY_LIST_TEXT_LIMIT}`);
}

export async function getHistoryItem(id: string): Promise<HistoryItem> {
  return apiJson<HistoryItem>(`/history/${encodeURIComponent(id)}`);
}

/** The take with its whole script: a no-op unless the list clipped it. */
export async function fullTake(item: HistoryItem): Promise<HistoryItem> {
  return item.text_truncated ? getHistoryItem(item.id) : item;
}

export async function clearHistory(): Promise<void> {
  await apiFetch('/history', { method: 'DELETE' });
}

export async function deleteHistoryItem(id: string): Promise<void> {
  await apiFetch(`/history/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function setHistoryStarred(id: string, starred: boolean): Promise<StarredResponse> {
  return apiJson<StarredResponse>(`/history/${encodeURIComponent(id)}/starred`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ starred }),
  });
}
