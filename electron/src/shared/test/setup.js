import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach } from 'vitest';
// Initialize the real i18n instance so components that call the global
// i18next.t() singleton (e.g. class components like ErrorBoundary) render
// actual strings in tests instead of bare keys. fallbackLng: 'en' keeps
// assertions on English text stable regardless of detected locale.
import '../i18n';

// Radix menus scroll the keyboard-focused item; JSDOM has no layout scrolling.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

const localStorageMock = (function () {
  let store = {};
  return {
    getItem(key) {
      return store[key] || null;
    },
    setItem(key, value) {
      store[key] = value.toString();
    },
    clear() {
      store = {};
    },
    removeItem(key) {
      delete store[key];
    },
    key(i) {
      return Object.keys(store)[i] ?? null;
    },
    get length() {
      return Object.keys(store).length;
    },
  };
})();

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
});

const persistence = await import('../utils/coalescedJsonStorage');
const longformPersistence = await import('../utils/longformPersistence');

// Application bootstrap resolves this role before rendering. Unit tests import
// the store directly, so give each test the equivalent isolated main-window
// contract and tear down every timer/listener/suspension afterward.
persistence.configurePersistenceRole('main');
beforeEach(() => {
  let longformRecord = null;
  longformPersistence.configureLongformDurableStoreForTests({
    read: async () => (longformRecord ? structuredClone(longformRecord) : null),
    write: async (record) => {
      longformRecord = structuredClone(record);
    },
    clear: async () => {
      longformRecord = null;
    },
  });
  persistence.resetCoalescedJsonStorageForTests();
  persistence.configurePersistenceRole('main');
});
afterEach(() => {
  longformPersistence.resetLongformPersistenceForTests();
  persistence.resetCoalescedJsonStorageForTests();
});

// jsdom doesn't implement navigation, so window.location.reload() throws
// ("Not implemented"). Components legitimately schedule a reload on a timer
// (e.g. ResetPanel after a reset: setTimeout(reload, 400)); when that timer
// fires after its test has moved on, the throw surfaces as an *unhandled*
// error and fails the whole run even though every test passed — an
// intermittent, order-dependent flake. No-op the navigation methods so a
// lingering reload timer can never redden CI.
try {
  window.location.reload = () => {};
  window.location.assign = () => {};
  window.location.replace = () => {};
} catch {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      ...window.location,
      reload: () => {},
      assign: () => {},
      replace: () => {},
    },
  });
}
