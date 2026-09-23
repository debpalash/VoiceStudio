import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import { matchLocale } from './locale-match';

export const SUPPORTED_LOCALES = [
  'en',
  'zh-CN',
  'es',
  'fr',
  'de',
  'ja',
  'pt',
  'it',
  'ru',
  'ko',
  'hi',
  'tr',
  'pl',
  'nl',
  'sv',
  'th',
  'vi',
  'id',
  'uk',
  'ar',
  'zh-TW',
] as const;

export type AppLocale = (typeof SUPPORTED_LOCALES)[number];

const LOCALE_KEY = 'voicestudio.locale';
const localeModules = import.meta.glob<{ default: Record<string, unknown> }>([
  './locales/*.json',
  '!./locales/en.json',
]);
const loading = new Map<AppLocale, Promise<void>>();

function normalizeLocale(value: string | null | undefined): AppLocale | null {
  return matchLocale(value, SUPPORTED_LOCALES);
}

function initialLocale(): AppLocale {
  try {
    const saved = normalizeLocale(localStorage.getItem(LOCALE_KEY));
    if (saved) return saved;
  } catch {
    // Storage can be unavailable in hardened browser contexts.
  }
  if (typeof navigator !== 'undefined') {
    for (const candidate of navigator.languages || [navigator.language]) {
      const locale = normalizeLocale(candidate);
      if (locale) return locale;
    }
  }
  return 'en';
}

async function loadLocale(locale: AppLocale): Promise<void> {
  if (locale === 'en' || i18next.hasResourceBundle(locale, 'translation')) return;
  const pending = loading.get(locale);
  if (pending) return pending;
  const load = localeModules[`./locales/${locale}.json`];
  if (!load) return;
  const promise = load()
    .then((module) => {
      i18next.addResourceBundle(locale, 'translation', module.default, true, true);
    })
    .finally(() => loading.delete(locale));
  loading.set(locale, promise);
  return promise;
}

function applyDocumentLanguage(locale: string) {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = locale;
  document.documentElement.dir = i18next.dir(locale);
}

const selectedLocale = initialLocale();

void i18next
  .use(initReactI18next)
  .init({
    resources: { en: { translation: en } },
    lng: selectedLocale,
    fallbackLng: 'en',
    partialBundledLanguages: true,
    interpolation: { escapeValue: false },
    returnNull: false,
    react: { bindI18n: 'languageChanged', bindI18nStore: 'added' },
  })
  .catch(() => {});

i18next.on('languageChanged', (locale) => {
  applyDocumentLanguage(locale);
  const normalized = normalizeLocale(locale);
  if (normalized) void loadLocale(normalized).catch(() => {});
});
applyDocumentLanguage(selectedLocale);
void loadLocale(selectedLocale).catch(() => {});

export const APP_LANGUAGES = SUPPORTED_LOCALES.map((code) => {
  try {
    return { code, label: new Intl.DisplayNames([code], { type: 'language' }).of(code) || code };
  } catch {
    return { code, label: code };
  }
});
/** Select `items`: a language picker must show the language name, never its code. */
export const APP_LANGUAGE_ITEMS = APP_LANGUAGES.map(({ code, label }) => ({ value: code, label }));

let languageRequest = 0;

export async function setAppLanguage(locale: AppLocale) {
  const request = ++languageRequest;
  try {
    await loadLocale(locale);
  } catch {
    return false;
  }
  // Bundles may finish out of order; only the latest user choice may apply.
  if (request !== languageRequest) return;
  try {
    localStorage.setItem(LOCALE_KEY, locale);
  } catch {
    // The in-memory choice still applies for this session.
  }
  await i18next.changeLanguage(locale);
  return true;
}

export default i18next;
