import { expect, it } from 'vitest';
import i18n from '@/i18n';

it('counts segments and glossary terms with real plurals', () => {
  const t = (key: string, count?: number) => i18n.t(key, { count, lng: 'en' });
  expect(t('dub.segment_count', 1)).toBe('1 segment');
  expect(t('dub.segment_count', 4)).toBe('4 segments');
  expect(t('dub.glossary_label')).toBe('Glossary');
  expect(t('dub.glossary_terms', 1)).toBe('Glossary · 1 term');
  expect(t('dub.glossary_terms', 3)).toBe('Glossary · 3 terms');
});
