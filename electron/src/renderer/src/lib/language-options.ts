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
const codeValuesLower = new Set([...codeValues].map((code) => code.toLowerCase()));

// Chinese written standards (#3685). A dub segment stores the script tag
// (`cmn-Hans` / `cmn-Hant`) while an engine advertises the picker names
// (`chinese (simplified)` / `chinese (traditional)`); without a fold the gate
// compares "cmn-hans" against "chinese" and refuses a language the engine
// renders fine. Both sides meet on the BCP-47 code. Scripts stay distinct, so
// Traditional is never accepted by an engine that only lists Simplified.
const SCRIPT_CODES: Record<string, string> = {
  'cmn-hans': 'zh-cn',
  'zh-hans': 'zh-cn',
  'chinese (simplified)': 'zh-cn',
  'cmn-hant': 'zh-hant',
  'zh-hant': 'zh-hant',
  'chinese (traditional)': 'zh-hant',
};

/** Compare key for a language name or stored tag; lowercased BCP-47 code. */
function engineCode(value: string) {
  const key = value.toLowerCase();
  return (
    SCRIPT_CODES[key] ??
    (languageCode(value) || (codeValuesLower.has(key) ? key : '')).toLowerCase()
  );
}

// These display names are accepted by the backend; keep persisted values unchanged.
export function languageCode(name: string) {
  return codes[name.toLowerCase()] || '';
}

export function languageSupported(name: string, supported?: readonly string[] | null) {
  if (name.toLowerCase() === 'auto' || supported == null) return true;
  const code = engineCode(name);
  return supported.some(
    (candidate) =>
      candidate.toLowerCase() === name.toLowerCase() ||
      Boolean(code && engineCode(candidate) === code),
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
