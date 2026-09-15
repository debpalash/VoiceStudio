import { useSyncExternalStore } from 'react';
type Layout = {
  expandedLibraryContext?: string | null;
  editingProfileId?: string | null;
  panel: 'voice' | 'settings' | null;
  libraryOpen: boolean;
  libraryTab: 'voices' | 'takes';
};
const key = 'voicestudio.workspace-layout';
const defaults: Layout = { panel: null, libraryOpen: true, libraryTab: 'voices' };
function read(): Layout {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? '{}');
    return {
      panel: value?.panel === 'voice' || value?.panel === 'settings' ? value.panel : null,
      libraryOpen: typeof value?.libraryOpen === 'boolean' ? value.libraryOpen : true,
      libraryTab: value?.libraryTab === 'takes' ? 'takes' : 'voices',
    };
  } catch {
    return defaults;
  }
}
let current = read();
const listeners = new Set<() => void>();
export function setWorkspace(patch: Partial<Layout>) {
  current = { ...current, ...patch };
  try {
    localStorage.setItem(key, JSON.stringify(current));
  } catch {
    /* Session layout remains available. */
  }
  listeners.forEach((listener) => listener());
}
export function useWorkspace() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => current,
  );
}
