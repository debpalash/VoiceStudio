/**
 * #3143: Brazilian and European Portuguese are separate language choices.
 * A value saved before the split ("Portuguese") keeps working and means
 * Brazilian Portuguese.
 */
import { describe, expect, it } from 'vitest';
import { LANGUAGES } from './languages';
import { languageCode, languageOptions, languageSupported } from './language-options';
import {
  canonicalLanguage,
  dialectOutcome,
  hasVariantChoices,
  languageDialect,
  variantChoice,
  withVariantChoices,
} from './language-variants';

describe('Portuguese choices', () => {
  it('lists both variants instead of a single Portuguese', () => {
    expect(LANGUAGES).toContain('Portuguese (Brazil)');
    expect(LANGUAGES).toContain('Portuguese (Portugal)');
    expect(LANGUAGES).not.toContain('Portuguese');
  });

  it('shows a saved plain Portuguese as Brazilian', () => {
    expect(canonicalLanguage('Portuguese')).toBe('Portuguese (Brazil)');
    expect(canonicalLanguage('pt')).toBe('Portuguese (Brazil)');
    expect(canonicalLanguage('Portuguese (Portugal)')).toBe('Portuguese (Portugal)');
    expect(canonicalLanguage('English')).toBe('English');
    expect(languageDialect('Portuguese')).toBe('pt-BR');
    expect(languageDialect('Portuguese (Portugal)')).toBe('pt-PT');
    expect(languageDialect('Spanish')).toBeUndefined();
  });

  it('carries the BCP-47 tag for names, flags and search', () => {
    expect(languageCode('Portuguese (Portugal)')).toBe('pt-PT');
    const [brazil, portugal] = languageOptions(
      ['Portuguese (Brazil)', 'Portuguese (Portugal)'],
      'en',
      'Auto',
    );
    expect(brazil.code).toBe('pt-BR');
    expect(portugal.code).toBe('pt-PT');
    expect(brazil.label).not.toBe(portugal.label);
    expect(portugal.search).toContain('portugal');
  });

  it('is supported wherever the engine lists the variant', () => {
    expect(languageSupported('Portuguese (Portugal)', ['portuguese (portugal)'])).toBe(true);
    expect(languageSupported('Portuguese (Portugal)', ['english'])).toBe(false);
  });

  it('maps dub targets between the Portuguese track and its variants', () => {
    expect(withVariantChoices(['English', 'Portuguese'])).toEqual([
      'English',
      'Portuguese (Brazil)',
      'Portuguese (Portugal)',
    ]);
    expect(variantChoice('Portuguese', 'pt-PT')).toBe('Portuguese (Portugal)');
    expect(variantChoice('Portuguese', undefined)).toBe('Portuguese (Brazil)');
    expect(hasVariantChoices('pt')).toBe(true);
    expect(hasVariantChoices('es')).toBe(false);
  });
});

describe('dialectOutcome', () => {
  it('stays quiet when the engine renders the dialect or metadata is unknown', () => {
    expect(dialectOutcome('pt-BR', ['pt-BR'])).toBeNull();
    expect(dialectOutcome('pt-PT', null)).toBeNull();
    expect(dialectOutcome(undefined, [])).toBeNull();
  });

  it('names the variant a single-variant engine speaks instead', () => {
    expect(dialectOutcome('pt-PT', ['pt-BR'])).toEqual({ used: 'pt-BR' });
  });

  it('says the accent follows the voice when the engine has one model', () => {
    expect(dialectOutcome('pt-PT', [])).toBe('voice');
    expect(dialectOutcome('pt-BR', [])).toBe('voice');
  });
});
