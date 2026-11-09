import { useSyncExternalStore } from 'react';
import { onStorageKey } from '@/lib/storage-events';

const STORAGE_KEY = 'voicestudio.dictation-settings.v1';
let aecEnabled = read();
const listeners = new Set<() => void>();

onStorageKey(STORAGE_KEY, () => {
  const next = read();
  if (next === aecEnabled) return;
  aecEnabled = next;
  listeners.forEach((listener) => listener());
});

function read(): boolean {
  if (typeof localStorage === 'undefined') return false;
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')?.aecEnabled === true;
  } catch {
    return false;
  }
}

export function getAecEnabled(): boolean {
  return aecEnabled;
}

export function setAecEnabled(value: boolean): void {
  if (aecEnabled === value) return;
  aecEnabled = value;
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ aecEnabled: value }));
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useAecEnabled(): boolean {
  return useSyncExternalStore(subscribe, getAecEnabled, () => false);
}
