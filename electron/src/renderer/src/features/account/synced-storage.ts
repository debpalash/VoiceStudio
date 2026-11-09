/**
 * The renderer half of Cloud settings sync (desktop-app-guide §5): every write
 * to a synced localStorage key is mirrored to the backend, and pulled values
 * are written back without echoing.
 *
 * Each key's storage format comes from the backend allowlist: `rawKeys` hold
 * plain strings, every other key holds JSON.
 *
 * The wrapper patches Storage.prototype and checks the instance: assigning
 * `localStorage.setItem = …` would store an item named "setItem" instead.
 */
import { notifyStorageKey } from '@/lib/storage-events';

type Post = (key: string, value: unknown) => Promise<void>;
const UNPARSABLE = Symbol('unparsable');

export function installSyncedStorage(opts: {
  storage: Storage;
  keys: readonly string[];
  rawKeys?: readonly string[];
  /** Keys of which only some fields sync: a pulled object merges into the local one. */
  partialKeys?: readonly string[];
  post: Post;
}) {
  const { storage, post } = opts;
  const keys = new Set(opts.keys);
  const rawKeys = new Set(opts.rawKeys ?? []);
  const partialKeys = new Set(opts.partialKeys ?? []);
  const isObject = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null && !Array.isArray(value);
  const proto = Storage.prototype;
  const originalSet = proto.setItem;
  const originalRemove = proto.removeItem;
  let applying = false;

  const decode = (key: string, stored: string | null): unknown => {
    if (stored === null) return null;
    if (rawKeys.has(key)) return stored;
    try {
      return JSON.parse(stored);
    } catch {
      return UNPARSABLE;
    }
  };

  const announce = (key: string, stored: string | null) => {
    if (applying || !keys.has(key)) return;
    const value = decode(key, stored);
    if (value === UNPARSABLE) return; // never sync a value this app could not read back
    post(key, value).catch(() => {});
  };

  proto.setItem = function setItem(this: Storage, key: string, value: string) {
    originalSet.call(this, key, value);
    if (this === storage) announce(String(key), String(value));
  };
  proto.removeItem = function removeItem(this: Storage, key: string) {
    originalRemove.call(this, key);
    if (this === storage) announce(String(key), null);
  };

  return {
    /** Write a pulled value; stores listening for `storage` events re-read it. */
    apply(key: string, value: unknown) {
      if (!keys.has(key)) return;
      applying = true;
      try {
        if (value === null || value === undefined) storage.removeItem(key);
        else if (rawKeys.has(key)) storage.setItem(key, String(value));
        else {
          const current = partialKeys.has(key) ? decode(key, storage.getItem(key)) : null;
          // Device-local fields of a partly-synced object (the clone draft, the selected
          // voice) are kept: only the synced fields come from the pulled value.
          const next = isObject(current) && isObject(value) ? { ...current, ...value } : value;
          storage.setItem(key, JSON.stringify(next));
        }
      } finally {
        applying = false;
      }
      notifyStorageKey(key);
    },
    /** Current values of every synced key, for the first mirror after start-up. */
    snapshot(): Array<[string, unknown]> {
      const out: Array<[string, unknown]> = [];
      for (const key of keys) {
        const value = decode(key, storage.getItem(key));
        if (value !== null && value !== UNPARSABLE) out.push([key, value]);
      }
      return out;
    },
    uninstall() {
      proto.setItem = originalSet;
      proto.removeItem = originalRemove;
    },
  };
}
