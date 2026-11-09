/** Fired in this window when Cloud sync writes a pulled value into localStorage. */
const APPLIED = 'voicestudio:storage-applied';

/**
 * Re-read a localStorage-backed store when its key changes outside this module:
 * another window (a native `storage` event), or a value Cloud sync just pulled
 * (synced-storage calls `notifyStorageKey`).
 */
export function onStorageKey(key: string, callback: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const native = (event: StorageEvent) => {
    if (event.key === key) callback();
  };
  const applied = (event: Event) => {
    if ((event as CustomEvent<{ key: string }>).detail?.key === key) callback();
  };
  window.addEventListener('storage', native);
  window.addEventListener(APPLIED, applied);
  return () => {
    window.removeEventListener('storage', native);
    window.removeEventListener(APPLIED, applied);
  };
}

export function notifyStorageKey(key: string): void {
  if (typeof window !== 'undefined')
    window.dispatchEvent(new CustomEvent(APPLIED, { detail: { key } }));
}
