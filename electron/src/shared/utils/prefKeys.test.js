/**
 * Guard for the factory-reset preference registry (utils/prefKeys.js).
 *
 * Scans the whole src tree for localStorage keys — direct string literals in
 * localStorage.*Item(...) calls plus the LS_* / *_KEY constant convention —
 * and fails when a key is neither a resettable preference (covered by the
 * registry, so Settings → Factory reset clears it) nor an explicitly
 * PRESERVED key. This makes "add a pref key but forget factory reset"
 * impossible to reintroduce silently.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  isPrefKey,
  PRESERVED_KEYS,
  PREF_KEYS,
  PREF_KEY_PREFIXES,
  clearLocalPreferences,
} from './prefKeys';
import {
  flushPendingWrites,
  installPersistenceLifecycleFlush,
  queueJsonWrite,
} from './coalescedJsonStorage';

const SRC_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE_EXT = new Set(['.js', '.jsx', '.ts', '.tsx']);

function* sourceFiles(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'test' || entry.name === 'node_modules') continue;
      yield* sourceFiles(p);
    } else if (SOURCE_EXT.has(path.extname(entry.name)) && !/\.test\.[jt]sx?$/.test(entry.name)) {
      yield p;
    }
  }
}

function collectLocalStorageKeys() {
  const found = new Map(); // key → first file seen
  const record = (key, file) => {
    if (!found.has(key)) found.set(key, path.relative(SRC_ROOT, file));
  };
  // 1. Literal keys passed straight to localStorage.
  const directRe = /localStorage\.(?:getItem|setItem|removeItem)\(\s*['"]([^'"]+)['"]/g;
  // 2. The storage-key constant convention (LS_FOO / FOO_KEY / zustand `name:`).
  const constRe = /(?:const|let|var)\s+(?:LS_[A-Z0-9_]+|[A-Z0-9_]*_KEY)\s*=\s*['"]([^'"]+)['"]/g;
  const zustandRe = /name:\s*['"](omnivoice\.[^'"]+)['"]\s*,?\s*\n\s*storage:\s*createJSONStorage/g;
  for (const file of sourceFiles(SRC_ROOT)) {
    const text = fs.readFileSync(file, 'utf8');
    for (const re of [directRe, constRe, zustandRe]) {
      re.lastIndex = 0;
      for (let m; (m = re.exec(text)); ) record(m[1], file);
    }
  }
  return found;
}

describe('prefKeys registry', () => {
  it('categorizes every localStorage key in src as pref (resettable) or preserved', () => {
    const found = collectLocalStorageKeys();
    expect(found.size).toBeGreaterThanOrEqual(15); // sanity: the scan actually finds keys
    const uncategorized = [...found.entries()].filter(
      ([key]) => !isPrefKey(key) && !PRESERVED_KEYS.includes(key),
    );
    expect(
      uncategorized,
      `localStorage keys not covered by the factory-reset registry (utils/prefKeys.js).\n` +
        `Either use a registered prefix (${PREF_KEY_PREFIXES.join(', ')}), add the key to ` +
        `PREF_KEYS, or — if it is user data / connection state — to PRESERVED_KEYS with a reason:\n` +
        uncategorized.map(([k, f]) => `  '${k}' (${f})`).join('\n'),
    ).toEqual([]);
  });

  it('never classifies preserved keys as resettable', () => {
    for (const k of PRESERVED_KEYS) expect(isPrefKey(k)).toBe(false);
    for (const k of PREF_KEYS) expect(isPrefKey(k)).toBe(true);
  });

  describe('clearLocalPreferences', () => {
    beforeEach(() => localStorage.clear());
    afterEach(() => vi.useRealTimers());

    it('removes every pref key and keeps data/connection keys', () => {
      localStorage.setItem('omnivoice.app', '{"state":{}}');
      localStorage.setItem('omnivoice.navRailSide', 'right');
      localStorage.setItem('omnivoice.logs.collapsed', '1');
      localStorage.setItem('omnivoice.settings.category', 'storage');
      localStorage.setItem('omni_capture_live_typing', '1');
      localStorage.setItem('ov_stories_global_speed', '1.2');
      localStorage.setItem('omni_ui', '{"uiScale":1.1}');
      localStorage.setItem('dismissed_lang_suggestion', 'true');
      // Connection target, the not-yet-migrated legacy master (the user's only
      // copy until the first successful session exchange consumes it), and
      // user data all survive.
      localStorage.setItem('ov_backend_url', 'http://192.168.1.10:7842');
      localStorage.setItem('ov_api_key', 'k');
      localStorage.setItem('omni_transcriptions', '[{"text":"hi"}]');

      const removed = clearLocalPreferences();

      expect(removed).toHaveLength(8);
      expect(localStorage.getItem('omnivoice.app')).toBeNull();
      expect(localStorage.getItem('omnivoice.navRailSide')).toBeNull();
      expect(localStorage.getItem('omnivoice.logs.collapsed')).toBeNull();
      expect(localStorage.getItem('omnivoice.settings.category')).toBeNull();
      expect(localStorage.getItem('omni_capture_live_typing')).toBeNull();
      expect(localStorage.getItem('ov_stories_global_speed')).toBeNull();
      expect(localStorage.getItem('omni_ui')).toBeNull();
      expect(localStorage.getItem('dismissed_lang_suggestion')).toBeNull();
      expect(localStorage.getItem('ov_backend_url')).toBe('http://192.168.1.10:7842');
      expect(localStorage.getItem('ov_api_key')).toBe('k');
      expect(localStorage.getItem('omni_transcriptions')).toBe('[{"text":"hi"}]');
    });

    it('preserves project data in a revisioned full fallback while resetting preferences', () => {
      localStorage.setItem(
        'omnivoice.app',
        JSON.stringify({
          version: 9,
          longformFallbackRevision: 3,
          state: {
            theme: 'dark',
            currentProjectId: 'p_book',
            script: 'newer fallback manuscript',
            storyProjects: [{ id: 'p_book', name: 'Book' }],
          },
        }),
      );
      localStorage.setItem('omnivoice.navRailSide', 'right');

      const reset = clearLocalPreferences();

      expect(reset).toEqual(expect.arrayContaining(['omnivoice.app', 'omnivoice.navRailSide']));
      const fallback = JSON.parse(localStorage.getItem('omnivoice.app'));
      expect(fallback).toEqual({
        version: 9,
        longformFallbackRevision: 3,
        state: {
          script: 'newer fallback manuscript',
          storyProjects: [{ id: 'p_book', name: 'Book' }],
        },
      });
      expect(localStorage.getItem('omnivoice.navRailSide')).toBeNull();
    });

    it('preserves an unrevisioned legacy fallback when adding its revision hit quota', () => {
      localStorage.setItem(
        'omnivoice.app',
        JSON.stringify({
          version: 8,
          state: {
            theme: 'dark',
            script: 'last recoverable manuscript',
            storyProjects: [{ id: 'p_book', name: 'Book' }],
          },
        }),
      );

      clearLocalPreferences();

      expect(JSON.parse(localStorage.getItem('omnivoice.app'))).toEqual({
        version: 8,
        state: {
          script: 'last recoverable manuscript',
          storyProjects: [{ id: 'p_book', name: 'Book' }],
        },
      });
    });

    it('preserves a pending IndexedDB-clear tombstone during a preference reset', () => {
      localStorage.setItem(
        'omnivoice.app',
        JSON.stringify({
          version: 9,
          longformPendingDurableClear: true,
          state: { theme: 'dark', currentProjectId: null },
        }),
      );

      clearLocalPreferences();

      expect(JSON.parse(localStorage.getItem('omnivoice.app'))).toEqual({
        version: 9,
        longformPendingDurableClear: true,
        state: {},
      });
    });

    it('preserves physical-removal intent while resetting preferences', () => {
      localStorage.setItem(
        'omnivoice.app',
        JSON.stringify({
          version: 9,
          longformPendingDurableClear: true,
          longformPendingStorageRemove: true,
          state: {},
        }),
      );

      clearLocalPreferences();

      expect(JSON.parse(localStorage.getItem('omnivoice.app'))).toEqual({
        version: 9,
        longformPendingDurableClear: true,
        longformPendingStorageRemove: true,
        state: {},
      });
    });

    it('prevents pending and post-reset writes from resurrecting preferences', () => {
      vi.useFakeTimers();
      localStorage.setItem('omnivoice.app', '{"state":{"old":true},"version":7}');
      localStorage.setItem('omni_ui', '{"text":"old"}');
      const cleanupLifecycle = installPersistenceLifecycleFlush();
      queueJsonWrite('omnivoice.app', () => ({ state: { text: 'pending' }, version: 7 }));
      queueJsonWrite('omni_ui', () => ({ text: 'pending' }));

      clearLocalPreferences();
      // Store/effect activity can continue during ResetPanel's 400 ms reload
      // delay. These writes must be rejected for the rest of this document.
      queueJsonWrite('omnivoice.app', () => ({ state: { text: 'after reset' }, version: 7 }));
      queueJsonWrite('omni_ui', () => ({ text: 'after reset' }));
      vi.runAllTimers();
      window.dispatchEvent(new Event('pagehide'));
      flushPendingWrites();

      expect(localStorage.getItem('omnivoice.app')).toBeNull();
      expect(localStorage.getItem('omni_ui')).toBeNull();
      cleanupLifecycle();
    });

    it('releases write suspension when storage enumeration fails', () => {
      const failingStorage = {
        get length() {
          throw new DOMException('private value', 'SecurityError');
        },
        key: vi.fn(),
        removeItem: vi.fn(),
      };

      expect(() => clearLocalPreferences(failingStorage)).toThrowError(DOMException);
      queueJsonWrite('omnivoice.app', () => ({ state: { recovered: true }, version: 7 }));
      flushPendingWrites();
      expect(JSON.parse(localStorage.getItem('omnivoice.app'))).toEqual({
        state: { recovered: true },
        version: 7,
      });
    });

    it('releases write suspension when storage key access fails', () => {
      const failingStorage = {
        length: 1,
        key: vi.fn(() => {
          throw new DOMException('private value', 'SecurityError');
        }),
        removeItem: vi.fn(),
      };

      expect(() => clearLocalPreferences(failingStorage)).toThrowError(DOMException);
      queueJsonWrite('omni_ui', () => ({ text: 'recovered after key failure' }));
      flushPendingWrites();
      expect(localStorage.getItem('omni_ui')).toBe('{"text":"recovered after key failure"}');
    });

    it('releases write suspension and propagates a removal failure', () => {
      const failingStorage = {
        length: 1,
        key: vi.fn(() => 'omnivoice.app'),
        removeItem: vi.fn(() => {
          throw new DOMException('private value', 'SecurityError');
        }),
      };

      expect(() => clearLocalPreferences(failingStorage)).toThrowError(DOMException);
      queueJsonWrite('omni_ui', () => ({ text: 'recovered' }));
      flushPendingWrites();
      expect(localStorage.getItem('omni_ui')).toBe('{"text":"recovered"}');
    });
  });
});
