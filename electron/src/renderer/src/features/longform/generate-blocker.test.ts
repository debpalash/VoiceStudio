import { describe, expect, it } from 'vitest';
import { generateBlocker, type GenerateBlockerInput } from './generate-blocker';

const allClear = (mode: GenerateBlockerInput['mode']): GenerateBlockerInput => ({
  mode,
  busyElsewhere: false,
  importing: false,
  tts: null,
  usable: true,
  voicesReady: true,
  duplicateLexicon: false,
});

describe.each(['stories', 'audiobook'] as const)('%s', (mode) => {
  const ask = (changes: Partial<GenerateBlockerInput>) =>
    generateBlocker({ ...allClear(mode), ...changes });

  it('is free to run when nothing is wrong', () => {
    expect(ask({})).toBeNull();
  });

  it('puts the engine before content problems', () => {
    expect(ask({ tts: 'engine', usable: false, voicesReady: false })).toBe('engine');
    expect(ask({ tts: 'loading', usable: false })).toBe('engine_loading');
  });

  it('reports a missing voice only when there is something to speak', () => {
    expect(ask({ voicesReady: false })).toBe('voice');
    expect(ask({ voicesReady: false, usable: false })).toBe(
      mode === 'stories' ? 'no_lines' : 'no_script',
    );
  });

  it('lets another render and an import outrank everything below them', () => {
    expect(ask({ importing: true, tts: 'loading' })).toBe('importing');
    expect(ask({ busyElsewhere: true, importing: true, tts: 'engine' })).toBe('busy');
  });
});

it.each([
  ['stories', null],
  ['audiobook', 'lexicon'],
] as const)('a repeated lexicon word in %s gives %s', (mode, expected) => {
  expect(generateBlocker({ ...allClear(mode), duplicateLexicon: true })).toBe(expected);
});

it('shows the voice problem before the lexicon one', () => {
  expect(
    generateBlocker({ ...allClear('audiobook'), duplicateLexicon: true, voicesReady: false }),
  ).toBe('voice');
});
