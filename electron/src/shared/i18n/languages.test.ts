import { describe, expect, it } from 'vitest';

import { LANGUAGES } from './index';

describe('selectable UI languages', () => {
  it('lists every supported locale with a native label and English first', () => {
    expect(LANGUAGES[0].code).toBe('en');
    expect(LANGUAGES.map(({ code }) => code)).toEqual([
      'en',
      'zh-CN',
      'es',
      'fr',
      'de',
      'ja',
      'pt',
      'it',
      'ru',
      'ko',
      'hi',
      'tr',
      'pl',
      'nl',
      'sv',
      'th',
      'vi',
      'id',
      'uk',
      'ar',
      'zh-TW',
      'gl',
    ]);
    expect(LANGUAGES.every(({ label }) => label.trim().length > 0)).toBe(true);
  });
});
