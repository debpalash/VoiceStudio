import { useSyncExternalStore } from 'react';
import type { HistoryItem } from '@/lib/api/types';
import type { CloneSettings } from './clone-settings';
import { patchCloneSettings } from './clone-settings';
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
    const safe: Record<string, unknown> = {};
    for (const name of ['steps', 'cfg', 'speed', 'tShift', 'posTemp', 'classTemp', 'layerPenalty'])
      if (typeof raw[name] === 'number' && Number.isFinite(raw[name])) safe[name] = raw[name];
    for (const name of ['denoise', 'postprocess'])
      if (typeof raw[name] === 'boolean') safe[name] = raw[name];
    for (const name of ['refText', 'duration'])
      if (typeof raw[name] === 'string') safe[name] = raw[name];
    if (raw.wavBits === 16 || raw.wavBits === 24 || raw.wavBits === 32)
      safe.wavBits = raw.wavBits;
    if (raw.effectPreset === 'broadcast' || raw.effectPreset === 'raw')
      safe.effectPreset = raw.effectPreset;
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
