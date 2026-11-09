import { useSyncExternalStore } from 'react';
type Layout = {
  expandedLibraryContext?: string | null;
  editingProfileId?: string | null;
  panel: 'voice' | null;
  libraryOpen: boolean;
  libraryTab: 'voices' | 'takes';
  /** Allow the sidebar to fold into an icon rail on its own (default on). */
  autoCollapseSidebar: boolean;
};
const key = 'voicestudio.workspace-layout';
const defaults: Layout = {
  autoCollapseSidebar: true,
  libraryOpen: true,
  libraryTab: 'voices',
  panel: null,
};
function read(): Layout {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? '{}');
    return {
      // Voice controls moved to a composer popover; a stored 'settings' pane is dropped.
      panel: value?.panel === 'voice' ? 'voice' : null,
      libraryOpen: typeof value?.libraryOpen === 'boolean' ? value.libraryOpen : true,
      libraryTab: value?.libraryTab === 'takes' ? 'takes' : 'voices',
      autoCollapseSidebar: value?.autoCollapseSidebar !== false,
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
