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
/** Restore saved take parameters without carrying emotion over from another take.
 *
 * Older history has no emotion fields. Treat missing (or corrupt) emotion
 * settings as Off, while preserving correctly saved text/vector controls.
 * Script, voice identity, and language always come from the history entry.
 */
export function takeSettings(item: HistoryItem): Partial<TakeSettings> {
  const identity = {
    text: item.text,
    language: item.language || 'Auto',
    instruct: item.instruct || '',
    selectedProfileId: item.profile_id,
    // A transcript never rides along with a take, not even its own saved
    // edit: blank means the selected saved voice uses its stored transcript.
    refText: '',
  };
  const neutralEmotion = {
    emotionMode: DEFAULT_CLONE_SETTINGS.emotionMode,
    emotionText: DEFAULT_CLONE_SETTINGS.emotionText,
    emotionAlpha: DEFAULT_CLONE_SETTINGS.emotionAlpha,
    emotionVector: [...DEFAULT_CLONE_SETTINGS.emotionVector],
    direction: DEFAULT_CLONE_SETTINGS.direction,
  };
  const fallback = { ...neutralEmotion, ...identity };
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? '{}')?.[item.id];
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fallback;
    // Validate every generation control with the settings store's own rules,
    // so a control added to CloneSettings is restored without a second list.
    const safe: Record<string, unknown> = {};
    for (const name of Object.keys(DEFAULT_CLONE_SETTINGS) as Array<keyof CloneSettings>)
      if (!PREFERENCES.has(name) && name in raw && acceptsCloneSetting(name, raw[name]))
        safe[name] = raw[name];
    // The mode and its required input form a single setting. If the saved
    // active input is missing, corrupt, or blank, restoring the mode would
    // silently reuse a meaningless instruction. Reset the entire emotion
    // selection to Off, while retaining other saved generation controls.
    const validEmotion =
      safe.emotionMode === 'none' ||
      (safe.emotionMode === 'text' &&
        typeof safe.emotionText === 'string' &&
        Boolean(safe.emotionText.trim())) ||
      (safe.emotionMode === 'vector' && Array.isArray(safe.emotionVector));
    if (!validEmotion) {
      delete safe.emotionMode;
      delete safe.emotionText;
      delete safe.emotionAlpha;
      delete safe.emotionVector;
    }
    // A take rendered with a delivery direction has style + direction joined
    // in the history's instruct; restore the two fields from the snapshot so
    // reusing it does not send the direction twice.
    const directed = typeof safe.direction === 'string' && safe.direction.trim() !== '';
    return {
      ...neutralEmotion,
      ...safe,
      ...identity,
      ...(directed && typeof safe.instruct === 'string' ? { instruct: safe.instruct } : {}),
    };
  } catch {
    return fallback;
  }
}
export async function reuseTake(item: HistoryItem) {
  await setReferenceFile(null);
  patchCloneSettings(takeSettings(item));
  openTake(null);
}
