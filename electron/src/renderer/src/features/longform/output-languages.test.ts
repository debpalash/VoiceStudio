import { expect, it } from 'vitest';
import { LANGUAGES } from '@/lib/languages';
import { languageSupported } from '@/lib/language-options';
import { outputLanguageOptions } from './output-languages';

it('offers the full TTS vocabulary, including Uighur (#2810)', () => {
  const options = outputLanguageOptions('Auto');
  expect(options).toBe(LANGUAGES);
  expect(options).toContain('Uighur');
  expect(languageSupported('Uighur', ['english', 'uighur'])).toBe(true);
});

it('keeps a saved language that is outside the TTS vocabulary', () => {
  const options = outputLanguageOptions('Chinese (Simplified)');
  expect(options.at(-1)).toBe('Chinese (Simplified)');
  expect(options.filter((name) => name === 'Chinese (Simplified)')).toHaveLength(1);
  expect(outputLanguageOptions('')).toBe(LANGUAGES);
});

it('shows a saved plain Portuguese as its regional choice, not an extra row (#3143)', () => {
  expect(outputLanguageOptions('Portuguese')).toBe(LANGUAGES);
});
