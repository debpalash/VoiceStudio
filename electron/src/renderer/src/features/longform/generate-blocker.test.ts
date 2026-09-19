import { expect, it } from 'vitest';
import { generateBlocker } from './generate-blocker';

const ready = {
  mode: 'stories' as const,
  busyElsewhere: false,
  importing: false,
  tts: null,
  usable: true,
  voicesReady: true,
  duplicateLexicon: false,
};

it('is null only when everything Generate needs is in place', () => {
  expect(generateBlocker(ready)).toBeNull();
});

it('names the most fundamental reason first', () => {
  expect(generateBlocker({ ...ready, usable: false, voicesReady: false })).toBe('no_lines');
  expect(generateBlocker({ ...ready, mode: 'audiobook', usable: false })).toBe('no_script');
  expect(generateBlocker({ ...ready, voicesReady: false })).toBe('voice');
  expect(generateBlocker({ ...ready, tts: 'engine', voicesReady: false })).toBe('engine');
  expect(generateBlocker({ ...ready, tts: 'loading' })).toBe('engine_loading');
  expect(generateBlocker({ ...ready, importing: true, tts: 'engine' })).toBe('importing');
  expect(generateBlocker({ ...ready, busyElsewhere: true, importing: true })).toBe('busy');
});

it('a duplicated pronunciation word only blocks the audiobook', () => {
  expect(generateBlocker({ ...ready, duplicateLexicon: true })).toBeNull();
  expect(generateBlocker({ ...ready, mode: 'audiobook', duplicateLexicon: true })).toBe('lexicon');
});
