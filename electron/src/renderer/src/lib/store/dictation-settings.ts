import { useSyncExternalStore } from 'react';
import { onStorageKey } from '@/lib/storage-events';

const STORAGE_KEY = 'voicestudio.dictation-settings.v1';
interface DictationSettings {
  aecEnabled: boolean;
  compactOverlay: boolean;
}
let settings = read();
const listeners = new Set<() => void>();

const same = (a: DictationSettings, b: DictationSettings) =>
  a.aecEnabled === b.aecEnabled && a.compactOverlay === b.compactOverlay;

onStorageKey(STORAGE_KEY, () => {
  const next = read();
  if (same(next, settings)) return;
  settings = next;
  listeners.forEach((listener) => listener());
});

function read(): DictationSettings {
  const off = { aecEnabled: false, compactOverlay: false };
  if (typeof localStorage === 'undefined') return off;
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return {
      aecEnabled: stored?.aecEnabled === true,
      compactOverlay: stored?.compactOverlay === true,
    };
  } catch {
    return off;
  }
}

function update(patch: Partial<DictationSettings>): void {
  const next = { ...settings, ...patch };
  if (same(next, settings)) return;
  settings = next;
  // The compact flag is only stored when on, so the shape older builds wrote is unchanged.
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        aecEnabled: next.aecEnabled,
        ...(next.compactOverlay ? { compactOverlay: true } : {}),
      }),
    );
  }
  listeners.forEach((listener) => listener());
}

export function getAecEnabled(): boolean {
  return settings.aecEnabled;
}

export function setAecEnabled(value: boolean): void {
  update({ aecEnabled: value });
}

export function getCompactOverlay(): boolean {
  return settings.compactOverlay;
}

/** Press-and-hold dictation shows only a level meter and timer instead of the full recorder. */
export function setCompactOverlay(value: boolean): void {
  update({ compactOverlay: value });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useAecEnabled(): boolean {
  return useSyncExternalStore(subscribe, getAecEnabled, () => false);
}

export function useCompactOverlay(): boolean {
  return useSyncExternalStore(subscribe, getCompactOverlay, () => false);
}
