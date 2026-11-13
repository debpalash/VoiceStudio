import { afterEach, describe, expect, it } from 'vitest';
import en from './locales/en.json';
import i18n, { APP_LANGUAGES, SUPPORTED_LOCALES } from './index';

const catalogs = import.meta.glob<{ default: Record<string, unknown> }>('./locales/*.json', {
  eager: true,
});

describe('selectable UI languages', () => {
  it('lists every supported locale with a language name and English first', () => {
    expect(SUPPORTED_LOCALES[0]).toBe('en');
    expect(APP_LANGUAGES.map(({ code }) => code)).toEqual([...SUPPORTED_LOCALES]);
    expect(APP_LANGUAGES.every(({ label }) => label.trim().length > 0)).toBe(true);
  });

  it('ships a catalog file for each supported locale', () => {
    for (const code of SUPPORTED_LOCALES)
      expect(catalogs[`./locales/${code}.json`], code).toBeTruthy();
    expect(Object.keys(catalogs)).toHaveLength(SUPPORTED_LOCALES.length);
  });
});

describe('document direction follows the active locale', () => {
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('switching to Arabic sets <html dir="rtl" lang="ar">', async () => {
    await i18n.changeLanguage('ar');
    expect(document.documentElement.dir).toBe('rtl');
    expect(document.documentElement.lang).toBe('ar');
  });

  it('switching back to English restores <html dir="ltr" lang="en">', async () => {
    await i18n.changeLanguage('ar');
    await i18n.changeLanguage('en');
    expect(document.documentElement.dir).toBe('ltr');
    expect(document.documentElement.lang).toBe('en');
  });

  it('left-to-right locales stay left-to-right', async () => {
    await i18n.changeLanguage('de');
    expect(document.documentElement.dir).toBe('ltr');
    expect(document.documentElement.lang).toBe('de');
  });
});

describe('failure copy keeps the real error', () => {
  it('gallery.use_failed interpolates the backend message', () => {
    expect((en.gallery as Record<string, string>).use_failed).toContain('{{message}}');
  });

  it.each([
    ['it', 'Altre righe: 1', 'Altre righe: 2'],
    ['nl', 'Extra rijen: 1', 'Extra rijen: 2'],
    ['pl', 'Dodatkowe wiersze: 1', 'Dodatkowe wiersze: 2'],
    ['pt', 'Linhas adicionais: 1', 'Linhas adicionais: 2'],
    ['ru', 'Дополнительных строк: 1', 'Дополнительных строк: 2'],
  ])('%s keeps the pasted-translation row count readable', async (locale, one, other) => {
    const catalog = catalogs[`./locales/${locale}.json`]!.default;
    const instance = i18n.createInstance();
    await instance.init({
      lng: locale,
      fallbackLng: false,
      resources: { [locale]: { translation: catalog } },
    });
    expect(instance.t('dub.paste_translation_more_rows', { count: 1 })).toBe(one);
    expect(instance.t('dub.paste_translation_more_rows', { count: 2 })).toBe(other);
  });
});
