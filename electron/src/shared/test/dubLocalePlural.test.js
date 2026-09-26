import i18next from 'i18next';
import { describe, expect, it } from 'vitest';
import ar from '../i18n/locales/ar.json';
import de from '../i18n/locales/de.json';
import en from '../i18n/locales/en.json';
import es from '../i18n/locales/es.json';
import fr from '../i18n/locales/fr.json';
import hi from '../i18n/locales/hi.json';
import itLocale from '../i18n/locales/it.json';
import nl from '../i18n/locales/nl.json';
import pl from '../i18n/locales/pl.json';
import pt from '../i18n/locales/pt.json';
import ru from '../i18n/locales/ru.json';

const resources = { ar, de, en, es, fr, hi, it: itLocale, nl, pl, pt, ru };

async function translate(locale, key, count, options = {}) {
  const instance = i18next.createInstance();
  await instance.init({
    lng: locale,
    fallbackLng: false,
    resources: { [locale]: { translation: resources[locale] } },
  });
  return instance.t(key, { count, ...options });
}

describe('dubbing count translations', () => {
  it.each([
    ['en', '1 language selected', '2 languages selected'],
    ['de', '1 Sprache ausgewählt', '2 Sprachen ausgewählt'],
    ['es', '1 idioma seleccionado', '2 idiomas seleccionados'],
    ['fr', '1 langue sélectionnée', '2 langues sélectionnées'],
    ['hi', '1 भाषा चुनी गई', '2 भाषाएँ चुनी गईं'],
    ['it', '1 lingua selezionata', '2 lingue selezionate'],
    ['nl', '1 taal geselecteerd', '2 talen geselecteerd'],
    ['pt', '1 idioma selecionado', '2 idiomas selecionados'],
  ])('%s selects singular and plural language wording', async (locale, one, other) => {
    expect(await translate(locale, 'dub.languages_selected', 1)).toBe(one);
    expect(await translate(locale, 'dub.languages_selected', 2)).toBe(other);
  });

  it.each([
    ['it', 'Altre righe: 1', 'Altre righe: 2'],
    ['nl', 'Extra rijen: 1', 'Extra rijen: 2'],
    ['pl', 'Dodatkowe wiersze: 1', 'Dodatkowe wiersze: 2'],
    ['pt', 'Linhas adicionais: 1', 'Linhas adicionais: 2'],
    ['ru', 'Дополнительных строк: 1', 'Дополнительных строк: 2'],
  ])('%s keeps paste row counts grammatical for one and many', async (locale, one, other) => {
    expect(await translate(locale, 'dub.paste_translation_more_rows', 1)).toBe(one);
    expect(await translate(locale, 'dub.paste_translation_more_rows', 2)).toBe(other);
  });

  it.each([
    ['es', '1 segmento · 9 s', '2 segmentos · 9 s', '5 segmentos · 9 s'],
    ['fr', '1 segment · 9 s', '2 segments · 9 s', '5 segments · 9 s'],
    ['it', '1 segmento · 9 s', '2 segmenti · 9 s', '5 segmenti · 9 s'],
    ['nl', '1 segment · 9 s', '2 segmenten · 9 s', '5 segmenten · 9 s'],
    ['pl', '1 segment · 9 s', '2 segmenty · 9 s', '5 segmentów · 9 s'],
    ['pt', '1 segmento · 9 s', '2 segmentos · 9 s', '5 segmentos · 9 s'],
    ['ru', '1 сегмент · 9 с', '2 сегмента · 9 с', '5 сегментов · 9 с'],
  ])('%s formats Dub history counts for one, few, and many', async (locale, one, few, many) => {
    expect(await translate(locale, 'history.dub_meta', 1, { duration: 9 })).toBe(one);
    expect(await translate(locale, 'history.dub_meta', 2, { duration: 9 })).toBe(few);
    expect(await translate(locale, 'history.dub_meta', 5, { duration: 9 })).toBe(many);
  });

  it('formats Arabic zero and dual Dub history counts', async () => {
    expect(await translate('ar', 'history.dub_meta', 0, { duration: 9 })).toBe('0 مقاطع · 9 ث');
    expect(await translate('ar', 'history.dub_meta', 2, { duration: 9 })).toBe('2 مقطعان · 9 ث');
  });
});
