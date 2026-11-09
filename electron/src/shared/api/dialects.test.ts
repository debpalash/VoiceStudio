import { describe, it, expect } from 'vitest';
import {
  DIALECTS,
  dialectLabel,
  dialectMatchesLang,
  dialectOptionsFor,
  dialectWasIgnored,
} from './dialects';

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

/**
 * #2752 — whether a requested dialect actually counted.
 *
 * A dialect is a prompt instruction, so only an LLM-backed translation can act
 * on it. A neural MT model has one code per language — NLLB's `por_Latn` is
 * just "Portuguese", with no European variant — so asking it for pt-PT returns
 * whatever its training favoured. The backend says so with `dialect_applied`,
 * and the client's only job is to not throw that answer away.
 */
describe('dialectWasIgnored', () => {
  it('is true when a dialect was asked for and the engine did not apply it', () => {
    expect(dialectWasIgnored({ dialect: 'pt-PT', dialect_applied: false })).toBe(true);
  });

  it('is false when the engine applied it', () => {
    expect(dialectWasIgnored({ dialect: 'pt-PT', dialect_applied: true })).toBe(false);
  });

  it('is false when no dialect was asked for', () => {
    expect(dialectWasIgnored({ dialect_applied: false })).toBe(false);
    expect(dialectWasIgnored({ dialect: '', dialect_applied: false })).toBe(false);
  });

  it('stays quiet when the backend never answered', () => {
    // A build older than the flag omits the field entirely. An absent answer is
    // not a "no", and claiming the dialect was dropped would be a guess.
    expect(dialectWasIgnored({ dialect: 'pt-PT' })).toBe(false);
    expect(dialectWasIgnored({})).toBe(false);
    expect(dialectWasIgnored(null)).toBe(false);
    expect(dialectWasIgnored(undefined)).toBe(false);
  });

  it('defers to the quality notice when that one is already showing', () => {
    // #372: both at once sends the user in a circle — "pick Cinematic", then
    // "Cinematic needs an LLM". The quality notice names the same remedy.
    expect(
      dialectWasIgnored({
        dialect: 'pt-PT',
        dialect_applied: false,
        cinematic_skipped: 'no-llm-configured',
      }),
    ).toBe(false);
  });

  it('still speaks when the quality was skipped for some other reason', () => {
    expect(
      dialectWasIgnored({ dialect: 'pt-PT', dialect_applied: false, cinematic_skipped: 'budget' }),
    ).toBe(true);
  });
});
