import { describe, expect, it } from 'vitest';
import { hasCompleteTranslation, translationProgressByCode } from './multiLang';

describe('translationProgressByCode', () => {
  it('never credits stale visible text to a different language', () => {
    const segments = [
      {
        text_original: 'Hello',
        text: 'Hola',
        translations: { es: 'Hola' },
      },
    ];

    expect(translationProgressByCode(segments, [{ code: 'es' }, { code: 'ja' }])).toEqual({
      es: { ready: 1, total: 1 },
      ja: { ready: 0, total: 1 },
    });
  });
});


describe('hasCompleteTranslation', () => {
  it('accepts a fully translated target with an empty spoken cue', () => {
    const segments = [
      { text_original: 'Hello', text: 'Hola', translations: { es: 'Hola' } },
      { text_original: ' ', text: '' },
    ];
    expect(translationProgressByCode(segments, [{ code: 'es' }])).toEqual({ es: { ready: 1, total: 1 } });
    expect(hasCompleteTranslation(segments, 'es')).toBe(true);
  });
  it('still requires each nonempty cue to have the requested language', () => {
    const segments = [
      { text_original: 'Hello', text: 'Hola', translations: { es: 'Hola' } },
      { text_original: 'Goodbye', text: 'Goodbye' },
    ];
    expect(hasCompleteTranslation(segments, 'es')).toBe(false);
    expect(hasCompleteTranslation(segments, 'ja')).toBe(false);
  });
  it('does not mark an empty job or an empty language complete', () => {
    expect(hasCompleteTranslation([], 'es')).toBe(false);
    expect(hasCompleteTranslation([{ text_original: '', text: '' }], 'es')).toBe(false);
    expect(hasCompleteTranslation([{ text_original: 'Hello', translations: { es: 'Hola' } }], '')).toBe(false);
  });
});
