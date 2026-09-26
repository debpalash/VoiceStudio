import { describe, expect, it } from 'vitest';
import { translationProgressByCode } from './multiLang';

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
