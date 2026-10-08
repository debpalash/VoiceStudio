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
  const segments = [
    { text_original: 'Hello', translations: { es: 'Hola' } },
    { text_original: '   ', text: '', translations: {} },
    { text_original: '', text: '', translations: {} },
  ];

  it('ignores empty-source cues, matching translationProgressByCode', () => {
    expect(hasCompleteTranslation(segments, 'es')).toBe(true);
    expect(translationProgressByCode(segments, [{ code: 'es' }]).es).toEqual({
      ready: 1,
      total: 1,
    });
  });

  it('agrees with progress for every language', () => {
    const mixed = [
      { text_original: 'A', translations: { es: 'a', ja: 'a' } },
      { text_original: 'B', translations: { es: 'b' } },
      { text_original: '', translations: {} },
    ];
    for (const code of ['es', 'ja', 'fr']) {
      const { ready, total } = translationProgressByCode(mixed, [{ code }])[code];
      expect(hasCompleteTranslation(mixed, code)).toBe(total > 0 && ready === total);
    }
  });

  it('is false with no translatable cues or no language', () => {
    expect(hasCompleteTranslation([], 'es')).toBe(false);
    expect(hasCompleteTranslation([{ text_original: '' }], 'es')).toBe(false);
    expect(hasCompleteTranslation(segments, '')).toBe(false);
    expect(hasCompleteTranslation([{ text_original: 'Hi', translations: {} }], 'es')).toBe(false);
  });
});
