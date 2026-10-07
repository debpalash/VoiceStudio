import { useSyncExternalStore } from 'react';
import type { HistoryItem } from '@/lib/api/types';
import type { CloneSettings } from './clone-settings';
import { acceptsCloneSetting, DEFAULT_CLONE_SETTINGS, patchCloneSettings } from './clone-settings';
import { setReferenceFile } from './reference';

const listeners = new Set<() => void>();
let selected: HistoryItem | null = null;
export function openTake(item: HistoryItem | null) {
  selected = item;
  listeners.forEach((listener) => listener());
}
export function useSelectedTake() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => selected,
  );
}
const key = 'voicestudio.take-settings.v1';
/** Application preferences, not generation controls: never stored or restored per take. */
const PREFERENCES = new Set<keyof CloneSettings>(['autoPlay', 'showOverrides']);
type TakeSettings = Omit<CloneSettings, 'autoPlay' | 'showOverrides'>;
export function rememberTake(id: string | null, settings: CloneSettings) {
  if (!id) return;
  try {
    const stored = JSON.parse(localStorage.getItem(key) ?? '{}');
    const records = stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {};
    const { autoPlay: _autoPlay, showOverrides: _showOverrides, ...snapshot } = settings;
    records[id] = snapshot;
    localStorage.setItem(
      key,
      JSON.stringify(Object.fromEntries(Object.entries(records).slice(-200))),
    );
  } catch {
    /* History still works if local metadata cannot be saved. */
  }
}
export function takeSettings(item: HistoryItem): Partial<TakeSettings> {
  const fallback = {
    text: item.text,
    language: item.language || 'Auto',
    instruct: item.instruct || '',
    selectedProfileId: item.profile_id,
  };
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? '{}')?.[item.id];
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fallback;
    // Validate every generation control with the settings store's own rules,
    // so a control added to CloneSettings is restored without a second list.
    const safe: Record<string, unknown> = {};
    for (const name of Object.keys(DEFAULT_CLONE_SETTINGS) as Array<keyof CloneSettings>)
      if (!PREFERENCES.has(name) && name in raw && acceptsCloneSetting(name, raw[name]))
        safe[name] = raw[name];
    return { ...safe, ...fallback };
  } catch {
    return fallback;
  }
}
export async function reuseTake(item: HistoryItem) {
  await setReferenceFile(null);
  patchCloneSettings(takeSettings(item));
  openTake(null);
}
