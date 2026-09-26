import { describe, it, expect } from 'vitest';
import { DIALECTS, dialectOptionsFor, dialectMatchesLang, dialectLabel } from './dialects';

// #280 item 2: regional dialect / vocabulary for dub translation.
describe('dialectOptionsFor', () => {
  it('returns curated variants for Spanish, incl. Argentina (the issue example)', () => {
    const opts = dialectOptionsFor('es');
    expect(opts).toContain('es-AR');
    expect(opts).toContain('es-ES');
  });

  it('returns [] for languages without curated variants', () => {
    expect(dialectOptionsFor('ja')).toEqual([]);
    expect(dialectOptionsFor('')).toEqual([]);
    expect(dialectOptionsFor(null)).toEqual([]);
    expect(dialectOptionsFor(undefined)).toEqual([]);
  });

  it('normalizes regioned lang codes to their base language', () => {
    expect(dialectOptionsFor('pt-BR')).toEqual(DIALECTS.pt);
  });

  it('every curated dialect code is a variant of its language key', () => {
    for (const [lang, codes] of Object.entries(DIALECTS)) {
      for (const code of codes) {
        expect(code.toLowerCase().startsWith(`${lang}-`)).toBe(true);
      }
    }
  });
});

describe('dialectMatchesLang', () => {
  it('matches a dialect to its language', () => {
    expect(dialectMatchesLang('es-AR', 'es')).toBe(true);
    expect(dialectMatchesLang('pt-BR', 'pt')).toBe(true);
  });

  it('rejects a stale dialect from another language', () => {
    expect(dialectMatchesLang('es-AR', 'fr')).toBe(false);
  });

  it('rejects empty inputs', () => {
    expect(dialectMatchesLang('', 'es')).toBe(false);
    expect(dialectMatchesLang('es-AR', '')).toBe(false);
    expect(dialectMatchesLang(null, 'es')).toBe(false);
  });
});

describe('dialectLabel', () => {
  it('renders a localized region name', () => {
    expect(dialectLabel('es-AR', 'en')).toBe('Argentina');
    expect(dialectLabel('en-GB', 'en')).toBe('United Kingdom');
  });

  it('localizes with the UI locale', () => {
    // German UI: AR → "Argentinien"
    expect(dialectLabel('es-AR', 'de')).toBe('Argentinien');
  });

  it('falls back to the region code for nonsense input', () => {
    expect(typeof dialectLabel('zz', 'en')).toBe('string');
  });
});
