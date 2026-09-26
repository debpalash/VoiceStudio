// Regional dialect options for dub translation (#280, item 2).
//
// Mirrors the curated DIALECT_HINTS list in backend/api/routers/dub_translate.py.
// The dropdown only offers regions for languages where the regional variant
// meaningfully changes vocabulary/grammar (es-AR voseo, pt-BR vs pt-PT, …).
//
// Labels are produced with Intl.DisplayNames so region names are localized by
// the platform for free — no hardcoded user-facing text, no i18n keys per
// region (per the project's localization rule).

/** Target-language ISO 639-1 code → BCP-47 dialect codes we curate hints for. */
export const DIALECTS: Record<string, string[]> = {
  es: ['es-ES', 'es-MX', 'es-AR', 'es-CO', 'es-CL'],
  pt: ['pt-BR', 'pt-PT'],
  en: ['en-US', 'en-GB', 'en-AU', 'en-IN'],
  fr: ['fr-FR', 'fr-CA', 'fr-BE'],
  de: ['de-DE', 'de-AT', 'de-CH'],
  ar: ['ar-EG', 'ar-SA', 'ar-MA'],
  nl: ['nl-NL', 'nl-BE'],
};

/** Dialect codes available for a target language code ('' / unknown → []). */
export function dialectOptionsFor(langCode: string | null | undefined): string[] {
  if (!langCode) return [];
  const base = String(langCode).toLowerCase().split('-')[0];
  return DIALECTS[base] || [];
}

/** True when `dialect` is a variant of `langCode` (guards stale selections). */
export function dialectMatchesLang(
  dialect: string | null | undefined,
  langCode: string | null | undefined,
): boolean {
  if (!dialect || !langCode) return false;
  const base = String(langCode).toLowerCase().split('-')[0];
  return String(dialect).toLowerCase().startsWith(`${base}-`);
}

/**
 * Localized human label for a dialect code, e.g. "es-AR" → "Argentina"
 * (or "Argentine" under a French UI). Falls back to the region code when
 * Intl.DisplayNames is unavailable (very old WebViews).
 */
export function dialectLabel(dialect: string, uiLocale: string = 'en'): string {
  const region = dialect.split('-')[1] || dialect;
  try {
    const dn = new Intl.DisplayNames([uiLocale, 'en'], { type: 'region' });
    return dn.of(region.toUpperCase()) || region;
  } catch {
    return region;
  }
}
