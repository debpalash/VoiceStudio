/**
 * Regional variants offered as their own language choices (#3143).
 *
 * Mirrors `backend/core/language_variants.py`. Engines know one Portuguese,
 * so the backend resolves a variant to that base language and applies the
 * dialect where it can (number words, translation prompts). A saved plain
 * "Portuguese" keeps working and means Brazilian Portuguese.
 */

export interface LanguageVariant {
  /** Picker name engines and dub tracks already use. */
  base: string;
  /** BCP-47 dialect the variant selects. */
  dialect: string;
}

export const LANGUAGE_VARIANTS: Record<string, LanguageVariant> = {
  'Portuguese (Brazil)': { base: 'Portuguese', dialect: 'pt-BR' },
  'Portuguese (Portugal)': { base: 'Portuguese', dialect: 'pt-PT' },
};

/** Base picker name (lowercase) → the variant it means when named alone. */
const DEFAULT_VARIANTS: Record<string, string> = {
  portuguese: 'Portuguese (Brazil)',
  pt: 'Portuguese (Brazil)',
};

const byLowerName = new Map(
  Object.keys(LANGUAGE_VARIANTS).map((name) => [name.toLowerCase(), name] as const),
);

/** The picker choice a saved value stands for: legacy "Portuguese" → "Portuguese (Brazil)". */
export function canonicalLanguage<T extends string | undefined | null>(name: T): T | string {
  if (!name) return name;
  const key = name.toLowerCase();
  return byLowerName.get(key) ?? DEFAULT_VARIANTS[key] ?? name;
}

/** BCP-47 dialect a picker name selects, including the legacy default. */
export function languageDialect(name: string | undefined | null): string | undefined {
  const canonical = canonicalLanguage(name);
  return canonical ? LANGUAGE_VARIANTS[canonical]?.dialect : undefined;
}

/** Replace each base language that has variants with its variant choices. */
export function withVariantChoices(names: readonly string[]): string[] {
  return names.flatMap((name) => {
    const variants = Object.entries(LANGUAGE_VARIANTS)
      .filter(([, variant]) => variant.base === name)
      .map(([choice]) => choice);
    return variants.length ? variants : [name];
  });
}

/** The variant choice for a base language and an optional dialect (dub target). */
export function variantChoice(base: string, dialect?: string | null): string {
  const exact = Object.entries(LANGUAGE_VARIANTS).find(
    ([, variant]) => variant.base === base && variant.dialect === dialect,
  );
  if (exact) return exact[0];
  return DEFAULT_VARIANTS[base.toLowerCase()] ?? base;
}

/** True when the base language is offered as variant choices instead of one entry. */
export function hasVariantChoices(base: string | undefined | null): boolean {
  if (!base) return false;
  const key = base.toLowerCase();
  return Object.values(LANGUAGE_VARIANTS).some(
    (variant) =>
      variant.base.toLowerCase() === key || variant.dialect.split('-')[0].toLowerCase() === key,
  );
}

/**
 * What the active engine will do with the chosen dialect.
 *
 * `rendered` is the engine's `rendered_dialects`: accents it renders on its
 * own. `null` = it does (no notice); a string = it speaks that other variant
 * instead; `'voice'` = it has one model for the language and the accent
 * follows the reference voice.
 */
export function dialectOutcome(
  dialect: string | undefined,
  rendered: readonly string[] | null | undefined,
): null | 'voice' | { used: string } {
  if (!dialect || rendered == null) return null;
  if (rendered.includes(dialect)) return null;
  const base = dialect.split('-')[0];
  const used = rendered.find((candidate) => candidate.split('-')[0] === base);
  return used ? { used } : 'voice';
}

/** Localized language name for a BCP-47 tag: "pt-BR" → "Brazilian Portuguese". */
export function languageTagName(tag: string, locale: string): string {
  try {
    return new Intl.DisplayNames([locale, 'en'], { type: 'language' }).of(tag) || tag;
  } catch {
    return tag;
  }
}
