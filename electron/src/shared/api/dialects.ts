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

/** The fields of a `/dub/translate` reply that decide whether the dialect counted. */
export interface DialectOutcome {
  dialect?: string;
  dialect_applied?: boolean;
  /** The variant the engine wrote instead, when it has only one (#3143). */
  dialect_used?: string;
  cinematic_skipped?: string;
}

/**
 * True when the user asked for a dialect and the translation did not honour it.
 *
 * The engine decides this, not the client: a dialect is a prompt instruction,
 * so only an LLM-backed path can act on it. A neural MT model has one code per
 * language — NLLB's `por_Latn` is simply "Portuguese", with no European variant
 * to select — so asking it for pt-PT returns whatever its training favoured and
 * the backend reports `dialect_applied: false` (#2752).
 *
 * `=== false` rather than a falsy check on purpose: a backend older than the
 * flag omits the field, and an absent answer is not a "no".
 *
 * The `cinematic_skipped` suppression is the #372 rule. Both notices at once
 * sends the user in a circle — "pick Cinematic" then "Cinematic needs an LLM" —
 * so the quality notice, which names the same remedy, speaks for both.
 *
 * Shared because the two shells answered this differently: the browser app
 * raised it and Electron typed the field but never read it, so a dub on the
 * desktop app silently ignored the dialect the user had picked. One rule, one
 * place, so they cannot drift apart again.
 */
export function dialectWasIgnored(outcome: DialectOutcome | null | undefined): boolean {
  return (
    Boolean(outcome?.dialect) &&
    outcome?.dialect_applied === false &&
    outcome?.cinematic_skipped !== 'no-llm-configured'
  );
}
