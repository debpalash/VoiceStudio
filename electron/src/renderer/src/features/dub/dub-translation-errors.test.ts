import { beforeEach, expect, it } from 'vitest';
import {
  applyDubTranslationRows,
  clearDubEditHistory,
  dubSession,
  editDubSegment,
  editDubSegments,
  redoDubEdit,
  undoDubEdit,
} from './dub-session';
import { getDubTranslationError } from './transcript-issues';

beforeEach(() => {
  clearDubEditHistory();
  dubSession.setState((current) => ({
    ...current,
    phase: 'editing',
    recovery: null,
    target: 'French',
    segments: [
      {
        id: 'a',
        start: 0,
        end: 1,
        text: 'Hello',
        text_original: 'Hello',
        translations: { fr: 'Hello', de: 'Hallo' },
        translate_error: 'French translation failed',
        translate_errors: { fr: 'French translation failed', de: 'German translation failed' },
      },
    ],
  }));
});

it.each(['Bonjour', 'Hello'])(
  'accepting pasted text %s clears only its language error and supports undo',
  (text) => {
    expect(
      applyDubTranslationRows('fr', [
        { id: 'a', index: 0, start: 0, end: 1, before: 'Hello', after: text, matched: true },
      ]),
    ).toBe(true);
    const repaired = dubSession.state.segments[0];
    expect(getDubTranslationError(repaired, 'fr')).toBeUndefined();
    expect(getDubTranslationError(repaired, 'de')).toBe('German translation failed');
    expect(repaired.translate_error).toBeUndefined();
    expect(repaired.translations).toEqual({ fr: text, de: 'Hallo' });

    undoDubEdit();
    expect(getDubTranslationError(dubSession.state.segments[0], 'fr')).toBe(
      'French translation failed',
    );
    redoDubEdit();
    expect(getDubTranslationError(dubSession.state.segments[0], 'fr')).toBeUndefined();
  },
);

it('clears the last saved error when editing a translated line', () => {
  dubSession.setState((current) => ({
    ...current,
    segments: current.segments.map((segment) => ({
      ...segment,
      translate_errors: { fr: 'French translation failed' },
    })),
  }));
  editDubSegment('a', { text: 'Bonjour', translations: { fr: 'Bonjour', de: 'Hallo' } });
  expect(dubSession.state.segments[0].translate_errors).toBeUndefined();
  expect(getDubTranslationError(dubSession.state.segments[0], 'fr')).toBeUndefined();
});

it('preserves other language errors when editing a translated line', () => {
  editDubSegment('a', { text: 'Bonjour', translations: { fr: 'Bonjour', de: 'Hallo' } });
  expect(dubSession.state.segments[0].translate_errors).toEqual({
    de: 'German translation failed',
  });
});

it.each(['single', 'bulk'])(
  'clears the legacy error after a %s edit repairs only the saved translation',
  (mode) => {
    dubSession.setState((current) => ({
      ...current,
      segments: current.segments.map((segment) => ({
        ...segment,
        translate_errors: { fr: 'French translation failed' },
      })),
    }));
    const changes = { translations: { fr: 'Bonjour', de: 'Hallo' } };
    if (mode === 'single') editDubSegment('a', changes);
    else editDubSegments(new Set(['a']), changes);
    const repaired = dubSession.state.segments[0];
    expect(repaired.text).toBe('Hello');
    expect(repaired.translate_errors).toBeUndefined();
    expect(getDubTranslationError(repaired, 'fr')).toBeUndefined();
    expect(getDubTranslationError(repaired, 'de')).toBeUndefined();
    expect(repaired.translate_error).toBeUndefined();

    undoDubEdit();
    expect(getDubTranslationError(dubSession.state.segments[0], 'fr')).toBe(
      'French translation failed',
    );
    redoDubEdit();
    expect(getDubTranslationError(dubSession.state.segments[0], 'fr')).toBeUndefined();
  },
);

it('keeps saved failures for timing edits and unmatched or blank pasted text', () => {
  editDubSegment('a', { end: 2 });
  expect(
    applyDubTranslationRows('fr', [
      { id: 'a', index: 0, start: 0, end: 1, before: 'Hello', after: 'Bonjour', matched: false },
      { id: 'a', index: 0, start: 0, end: 1, before: 'Hello', after: ' ', matched: true },
    ]),
  ).toBe(false);
  expect(dubSession.state.segments[0].translate_errors).toEqual({
    fr: 'French translation failed',
    de: 'German translation failed',
  });
});
