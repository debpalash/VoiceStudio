import languageCodes from '@shared/language-codes.json';
import languageRegions from '@shared/language-regions.json';
import { LANG_CODES } from '@shared/utils/languages';
import { TARGET_ONLY_LANGUAGES } from './translation-targets';
import { normalizeSearch, type SearchOption } from './language-search';
import type { QueryClient } from '@tanstack/react-query';
import type { EnginesResponse } from './api/types';
import { LANGUAGE_VARIANTS } from './language-variants';

const codes: Record<string, string> = {
  ...languageCodes,
  ...Object.fromEntries(LANG_CODES.map((item) => [item.label.toLowerCase(), item.code])),
  ...Object.fromEntries(TARGET_ONLY_LANGUAGES.map((item) => [item.label.toLowerCase(), item.code])),
  // #3143: regional choices carry their BCP-47 tag for names, flags and search.
  ...Object.fromEntries(
    Object.entries(LANGUAGE_VARIANTS).map(([name, variant]) => [
      name.toLowerCase(),
      variant.dialect,
    ]),
  ),
};
const codeValues = new Set(Object.values(codes));
// These display names are accepted by the backend; keep persisted values unchanged.
export function languageCode(name: string) {
  return codes[name.toLowerCase()] || '';
}

// #3723: the OmniVoice vocabulary names its base Arabic entry
// "Standard Arabic", which resolves to ISO 639-3 "arb", while the pickers
// call the language "Arabic" (ISO 639-1 "ar"). The support check below
// compares codes, so every Arabic dub target failed against an engine that
// speaks it — the "Choose a supported language" gate fired before any
// request was sent, for plain Arabic and for every Arabic dialect choice
// (the dialect is only a translation hint; the gate sees the base
// language). The comparison therefore folds the qualified form's code on
// the engine side only ("arb" → "ar"). The fold is deliberately
// one-directional: folding the request side too would newly enable
// "Standard Arabic" on engines that advertise bare "arabic" (e.g.
// VoxCPM2, which declares "ar"), but the backend resolves that request
// to "arb" and `_check_language` rejects it — the gate would green-light
// a generation that cannot run. This is a base-name equivalence, not a
// variant inference: a dialect entry such as "Egyptian Arabic" ("arz")
// still does not satisfy a bare "Arabic" request, and vice versa.
const STANDARD_FORM_CODES: Record<string, string> = { arb: 'ar' };
const foldCode = (code: string) => STANDARD_FORM_CODES[code] ?? code;

export function languageSupported(name: string, supported?: readonly string[] | null) {
  if (name.toLowerCase() === 'auto' || supported == null) return true;
  const code = languageCode(name) || (codeValues.has(name.toLowerCase()) ? name.toLowerCase() : '');
  return supported.some(
    (candidate) =>
      candidate.toLowerCase() === name.toLowerCase() ||
      Boolean(code && foldCode(languageCode(candidate) || candidate.toLowerCase()) === code),
  );
}

/** Shared guard for persisted selections after an engine switch. Unknown metadata remains open. */
export function ttsLanguagesSupported(
  data:
    | {
        tts?: {
          active: string | null;
          backends: { id: string; supported_language_names?: string[] | null }[];
        };
      }
    | undefined,
  languages: string[],
) {
  const selected = data?.tts?.backends.find((engine) => engine.id === data.tts?.active);
  return languages.every((language) =>
    languageSupported(language, selected?.supported_language_names),
  );
}

export function cachedTtsLanguagesSupported(
  client: QueryClient,
  _operation: string,
  languages: string[],
) {
  const state = client.getQueryState(['engines']);
  if (state?.status === 'error' || state?.isInvalidated) return true;
  return ttsLanguagesSupported(client.getQueryData<EnginesResponse>(['engines']), languages);
}

export interface LanguageOption extends SearchOption {
  code: string;
  native: string;
}
const cache = new Map<string, Map<string, LanguageOption>>();
export function languageOptions(
  names: string[],
  locale: string,
  autoLabel: string,
): LanguageOption[] {
  let entries = cache.get(locale);
  if (!entries) {
    entries = new Map();
    cache.set(locale, entries);
  }
  const display = new Intl.DisplayNames([locale, 'en'], { type: 'language', fallback: 'none' });
  const regionDisplay = new Intl.DisplayNames([locale, 'en'], { type: 'region', fallback: 'none' });
  const englishRegions = new Intl.DisplayNames(['en'], { type: 'region', fallback: 'none' });
  return [...new Set(names)].map((value) => {
    if (value.toLowerCase() === 'auto')
      return {
        value,
        label: autoLabel,
        native: '',
        code: '',
        search: [normalizeSearch(autoLabel), 'auto'],
        pinned: true,
      };
    const cached = entries.get(value);
    if (cached) return cached;
    const code = languageCode(value);
    let label = value,
      native = '';
    if (code) {
      try {
        label = display.of(code) || value;
        native =
          new Intl.DisplayNames([code], { type: 'language', fallback: 'none' }).of(code) || '';
      } catch {
        /* A language without ICU data retains its canonical name. */
      }
    }
    const region = (languageRegions as Record<string, string>)[code === 'zh' ? 'cmn-Hans' : code];
    const flagAliases =
      region && /^[A-Z]{2}$/.test(region)
        ? [
            region,
            regionDisplay.of(region) || '',
            englishRegions.of(region) || '',
            String.fromCodePoint(...[...region].map((char) => char.charCodeAt(0) + 127397)),
          ]
        : [];
    // Regional/script variants (pt-BR, cmn-Hant) group under their primary language.
    const family = code.includes('-') ? code.split('-')[0] : undefined;
    let familyLabel = '';
    if (family) {
      try {
        familyLabel = display.of(family) || '';
      } catch {
        /* Fall back to the canonical name's base below. */
      }
      familyLabel ||= value.replace(/\s*\(.*\)$/, '');
    }
    const aliases = code === 'zh' ? ['mandarin'] : code === 'tl' ? ['tagalog'] : [];
    const option = {
      value,
      label,
      code,
      native,
      ...(family ? { family, familyLabel } : {}),
      search: [
        ...new Set(
          [value, label, native, code, ...flagAliases, ...aliases]
            .map(normalizeSearch)
            .filter(Boolean),
        ),
      ],
    };
    entries.set(value, option);
    return option;
  });
}
