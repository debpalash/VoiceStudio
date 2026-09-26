import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import en from './locales/en.json';

/**
 * Only English ships in the main bundle (it's the fallback and renders the
 * first paint). Every other locale is a lazy `import()` — Vite splits each
 * JSON into its own chunk, fetched the moment that language is detected or
 * selected. This took the i18n chunk from ~1.8 MB down to just en.json.
 */
const LOADERS: Record<string, () => Promise<{ default: Record<string, unknown> }>> = {
  'zh-CN': () => import('./locales/zh-CN.json'),
  es: () => import('./locales/es.json'),
  fr: () => import('./locales/fr.json'),
  de: () => import('./locales/de.json'),
  ja: () => import('./locales/ja.json'),
  pt: () => import('./locales/pt.json'),
  it: () => import('./locales/it.json'),
  ru: () => import('./locales/ru.json'),
  ko: () => import('./locales/ko.json'),
  hi: () => import('./locales/hi.json'),
  tr: () => import('./locales/tr.json'),
  pl: () => import('./locales/pl.json'),
  nl: () => import('./locales/nl.json'),
  sv: () => import('./locales/sv.json'),
  th: () => import('./locales/th.json'),
  vi: () => import('./locales/vi.json'),
  id: () => import('./locales/id.json'),
  uk: () => import('./locales/uk.json'),
  ar: () => import('./locales/ar.json'),
  'zh-TW': () => import('./locales/zh-TW.json'),
};

const loading = new Set<string>();

async function loadLocale(lng: string): Promise<void> {
  const base = lng in LOADERS ? lng : lng.split('-')[0];
  const loader = LOADERS[lng] || LOADERS[base];
  const key = LOADERS[lng] ? lng : base;
  if (!loader || loading.has(key) || i18n.hasResourceBundle(key, 'translation')) return;
  loading.add(key);
  try {
    const mod = await loader();
    // deep + overwrite so a re-load after an HMR update wins.
    i18n.addResourceBundle(key, 'translation', mod.default, true, true);
  } catch (e) {
    console.warn(`i18n: failed to load locale "${key}"`, e);
  } finally {
    loading.delete(key);
  }
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
    },
    // Non-bundled languages arrive via addResourceBundle after a lazy fetch;
    // don't treat their initial absence as "missing language".
    partialBundledLanguages: true,
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
    detection: {
      order: ['querystring', 'navigator', 'htmlTag'],
      lookupQuerystring: 'lng',
    },
    react: {
      // Re-render not just on language switch but also when a lazily-loaded
      // bundle lands (addResourceBundle emits 'added') — the UI flashes the
      // English fallback for the fetch round-trip, then snaps to the locale.
      bindI18n: 'languageChanged added',
    },
  });

// Keep the document's text direction and language in sync with the active
// locale — without this, RTL locales (Arabic today, any future RTL addition)
// render in an LTR layout and screen readers get the wrong language.
function applyDocumentDirection(lng: string): void {
  if (typeof document === 'undefined') return;
  document.documentElement.dir = i18n.dir(lng);
  document.documentElement.lang = lng;
}

// Fetch the bundle whenever a non-English language becomes active — covers
// both the initial browser-detected language and later picker switches.
i18n.on('languageChanged', (lng) => {
  applyDocumentDirection(lng);
  void loadLocale(lng);
});
if (i18n.language) {
  applyDocumentDirection(i18n.language);
  if (i18n.language !== 'en') void loadLocale(i18n.language);
}

// Selectable UI languages. Native language names live here, in the i18n
// layer — never hardcoded in component code (see the "no hardcoded
// non-English UI text outside locale files" rule in CLAUDE.md).
export const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'zh-CN', label: '简体中文' },
  { code: 'es', label: 'Español' },
  { code: 'fr', label: 'Français' },
  { code: 'de', label: 'Deutsch' },
  { code: 'ja', label: '日本語' },
  { code: 'pt', label: 'Português' },
  { code: 'it', label: 'Italiano' },
  { code: 'ru', label: 'Русский' },
  { code: 'ko', label: '한국어' },
  { code: 'hi', label: 'हिन्दी' },
  { code: 'tr', label: 'Türkçe' },
  { code: 'pl', label: 'Polski' },
  { code: 'nl', label: 'Nederlands' },
  { code: 'sv', label: 'Svenska' },
  { code: 'th', label: 'ไทย' },
  { code: 'vi', label: 'Tiếng Việt' },
  { code: 'id', label: 'Bahasa Indonesia' },
  { code: 'uk', label: 'Українська' },
  { code: 'ar', label: 'العربية' },
  { code: 'zh-TW', label: '繁體中文' },
];

export default i18n;
