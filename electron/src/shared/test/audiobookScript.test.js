import { describe, it, expect } from 'vitest';
import {
  parseCastNames,
  scriptStats,
  formatRuntimeClock,
  validateScript,
  AUDIOBOOK_WPM,
} from '../utils/audiobookScript';

describe('parseCastNames', () => {
  it('returns distinct [voice:NAME] names in first-seen order', () => {
    const script =
      '# One\n[voice:Narrator] hi [voice:Mara] hey [voice:Narrator] again [voice:Cole] yo';
    expect(parseCastNames(script)).toEqual(['Narrator', 'Mara', 'Cole']);
  });
  it('skips the empty [voice:] reset and trims names', () => {
    expect(parseCastNames('[voice:] plain [voice: Mara ] x')).toEqual(['Mara']);
  });
  it('is empty for a script with no voice tags', () => {
    expect(parseCastNames('# Chapter\nJust narration.')).toEqual([]);
    expect(parseCastNames('')).toEqual([]);
  });
});

describe('scriptStats', () => {
  it('counts H1 chapters, spoken words (markup stripped), and runtime', () => {
    const script =
      '# One\n[voice:Mara] Hello world here. [pause 500ms]\n# Two\nFour more spoken words.';
    const { chapters, words, runtimeSec } = scriptStats(script);
    expect(chapters).toBe(2);
    // "Hello world here" (3) + "Four more spoken words" (4) = 7 — markup excluded.
    expect(words).toBe(7);
    expect(runtimeSec).toBeCloseTo((7 / AUDIOBOOK_WPM) * 60, 5);
  });
  it('treats a title-less script as one chapter', () => {
    expect(scriptStats('just some words').chapters).toBe(1);
  });
});

describe('formatRuntimeClock', () => {
  it('formats <1h as M:SS and ≥1h as H:MM', () => {
    expect(formatRuntimeClock(45)).toBe('0:45');
    expect(formatRuntimeClock(125)).toBe('2:05');
    expect(formatRuntimeClock(3720)).toBe('1:02');
  });
});

describe('validateScript', () => {
  it('flags an unknown voice and clears once it is mapped', () => {
    const script = '# One\n[voice:Mara] hello there';
    const unmapped = validateScript(script, { mappedNames: [], profileIds: [] });
    expect(unmapped).toEqual([{ type: 'unknown_voice', name: 'Mara' }]);
    // Mapping Mara clears the warning…
    expect(validateScript(script, { mappedNames: ['Mara'], profileIds: [] })).toEqual([]);
    // …and an exact profile-id match also clears it.
    expect(validateScript(script, { mappedNames: [], profileIds: ['Mara'] })).toEqual([]);
  });
  it('flags empty chapters and unrecognized tags', () => {
    const script = '# Empty\n\n# Full\nSome [wobble] words [pause 1s] and [slow]slow[/slow].';
    const warns = validateScript(script, {});
    expect(warns).toContainEqual({ type: 'empty_chapter', title: 'Empty' });
    expect(warns).toContainEqual({ type: 'unknown_tag', tag: '[wobble]' });
    // Known grammar (pause / SSML / voice / reactions) must NOT warn.
    expect(warns.some((w) => w.type === 'unknown_tag' && w.tag !== '[wobble]')).toBe(false);
  });
  it('does not flag known reaction tags', () => {
    const warns = validateScript('# C\nHa [laughter] ha.', {});
    expect(warns.filter((w) => w.type === 'unknown_tag')).toEqual([]);
  });
});
