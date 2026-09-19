import { useSyncExternalStore } from 'react';
type Layout = {
  expandedLibraryContext?: string | null;
  editingProfileId?: string | null;
  panel: 'voice' | 'settings' | null;
  libraryOpen: boolean;
  libraryTab: 'voices' | 'takes';
  /** Shrink the sidebar to a rail when a workspace opens its own side panel on a narrow window. */
  autoCollapseSidebar: boolean;
};
const key = 'voicestudio.workspace-layout';
const defaults: Layout = {
  panel: null,
  libraryOpen: true,
  libraryTab: 'voices',
  autoCollapseSidebar: true,
};
function read(): Layout {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? '{}');
    return {
      panel: value?.panel === 'voice' || value?.panel === 'settings' ? value.panel : null,
      libraryOpen: typeof value?.libraryOpen === 'boolean' ? value.libraryOpen : true,
      libraryTab: value?.libraryTab === 'takes' ? 'takes' : 'voices',
      autoCollapseSidebar:
        typeof value?.autoCollapseSidebar === 'boolean' ? value.autoCollapseSidebar : true,
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
