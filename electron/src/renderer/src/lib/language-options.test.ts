import { expect, it } from 'vitest';
import {
  cachedTtsLanguagesSupported,
  languageCode,
  languageOptions,
  languageSupported,
} from './language-options';
import { QueryClient } from '@tanstack/react-query';
import { rankOptions } from './language-search';
import { LANGUAGES } from './languages';

it('gives every canonical language a code without changing persisted values', () => {
  expect(LANGUAGES.filter((name) => name !== 'Auto' && !languageCode(name))).toEqual([]);
  expect(languageOptions(['German'], 'de', 'Auto')[0]).toMatchObject({
    value: 'German',
    code: 'de',
    label: 'Deutsch',
  });
});
it('searches native names, translated names, accents and codes locally', () => {
  const options = languageOptions(['German', 'French', 'Japanese'], 'en', 'Auto');
  expect(rankOptions(options, 'francais', [])[0]?.value).toBe('French');
  expect(rankOptions(options, 'DE', [])[0]?.value).toBe('German');
  expect(rankOptions(options, 'Deutsch', [])[0]?.value).toBe('German');
  expect(rankOptions(options, 'ger de', [])[0]?.value).toBe('German');
  expect(rankOptions(options, 'Germany', [])[0]?.value).toBe('German');
  expect(rankOptions(options, String.fromCodePoint(0x1f1e9, 0x1f1ea), [])[0]?.value).toBe('German');
});
it("matches a bare language against the engine's standard-form entry (#3723)", () => {
  // The OmniVoice vocabulary names its base Arabic entry "Standard Arabic"
  // ("arb") while the pickers call it "Arabic" ("ar"); the dub gate
  // compares codes, so every Arabic target failed before any request was
  // sent. The fold covers the bare request in both spellings.
  expect(languageSupported('Arabic', ['standard arabic'])).toBe(true);
  expect(languageSupported('ar', ['standard arabic'])).toBe(true);
  // Still strict: a dialect entry is not the base language, and vice versa.
  expect(languageSupported('Arabic', ['egyptian arabic'])).toBe(false);
  expect(languageSupported('Egyptian Arabic', ['standard arabic'])).toBe(false);
  // And deliberately one-directional (Greptile review on #3781): enabling
  // "Standard Arabic" on an engine that advertises bare "arabic" (e.g.
  // VoxCPM2, which declares "ar") would green-light a generation the
  // backend then rejects, since it resolves the request to "arb".
  expect(languageSupported('Standard Arabic', ['arabic'])).toBe(false);
});

it('does not infer support for regional variants, missing lists or a different model', () => {
  expect(languageSupported('German', ['german'])).toBe(true);
  expect(languageSupported('German', ['de'])).toBe(true);
  expect(languageSupported('German', ['english'])).toBe(false);
  expect(languageSupported('Chinese (Traditional)', ['chinese'])).toBe(false);
  expect(languageSupported('German', null)).toBe(true);
  expect(languageSupported('German', [])).toBe(false);
  expect(languageSupported('de', ['german'])).toBe(true);
});

it('blocks persisted incompatible local selections while metadata is current', async () => {
  const client = new QueryClient();
  client.setQueryData(['engines'], {
    tts: { active: 'kitten', backends: [{ id: 'kitten', supported_language_names: ['english'] }] },
  });
  expect(cachedTtsLanguagesSupported(client, 'clone', ['Japanese'])).toBe(false);
  expect(cachedTtsLanguagesSupported(client, 'clone', ['Auto'])).toBe(true);
  await client.invalidateQueries({ queryKey: ['engines'] });
  expect(cachedTtsLanguagesSupported(client, 'clone', ['Japanese'])).toBe(true);
});
it('keeps disabled recent choices behind enabled choices and uses exact matches first', () => {
  const options = languageOptions(['German', 'French'], 'en', 'Auto').map((item) => ({
    ...item,
    disabled: item.value === 'German',
  }));
  expect(rankOptions(options, '', ['German']).map((item) => item.value)).toEqual([
    'French',
    'German',
  ]);
});
it('marks regional and script variants with their primary language for grouping', () => {
  const [simplified, traditional, german] = languageOptions(
    ['Chinese (Simplified)', 'Chinese (Traditional)', 'German'],
    'en',
    'Auto',
  );
  expect(simplified.family).toBe('cmn');
  expect(traditional.family).toBe('cmn');
  expect(simplified.familyLabel).toBeTruthy();
  expect(german.family).toBeUndefined();
  const [brazil, portugal] = languageOptions(
    ['Portuguese (Brazil)', 'Portuguese (Portugal)'],
    'en',
    'Auto',
  );
  expect([brazil.family, portugal.family]).toEqual(['pt', 'pt']);
  expect(brazil.familyLabel).toBe('Portuguese');
});
