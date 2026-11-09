import { expect, it } from 'vitest';
import i18n from '@/i18n';

it('counts one chapter in the singular', () => {
  const stats = (count: number) =>
    i18n.t('audiobook.stats', { count, words: 0, runtime: '0:00', lng: 'en' });
  expect(stats(1)).toBe('1 chapter · 0 words · 0:00 est. runtime');
  expect(stats(2)).toBe('2 chapters · 0 words · 0:00 est. runtime');
});
