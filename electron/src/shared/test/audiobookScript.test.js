import { describe, it, expect } from 'vitest';
import {
  parseCastNames,
  parseChapterOutline,
  chapterHeadingSnippet,
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

describe('parseChapterOutline', () => {
  it('returns heading offsets, 1-based lines, and spoken word counts', () => {
    const script =
      '# One\n[voice:Mara] Hello world here. [pause 500ms]\n# Two\nFour more spoken words.';
    expect(parseChapterOutline(script)).toEqual([
      {
        index: 0,
        title: 'One',
        start: 0,
        headingEnd: 5,
        line: 1,
        words: 3,
      },
      {
        index: 1,
        title: 'Two',
        start: 51,
        headingEnd: 56,
        line: 3,
        words: 4,
      },
    ]);
  });

  it('normalizes CRLF, keeps indented headings, and skips empty manuscripts', () => {
    expect(parseChapterOutline('')).toEqual([]);
    expect(parseChapterOutline('just some words')).toEqual([]);
    const outline = parseChapterOutline('# First\r\nbody\r\n  # Second\r\n');
    expect(
      outline.map((chapter) => ({
        title: chapter.title,
        line: chapter.line,
        words: chapter.words,
      })),
    ).toEqual([
      { title: 'First', line: 1, words: 1 },
      { title: 'Second', line: 3, words: 0 },
    ]);
    expect(outline[1].start).toBe(outline[0].start + '# First\nbody\n'.length);
  });

  it('counts lines cumulatively across many chapters', () => {
    const script = Array.from({ length: 200 }, (_, i) => `# C${i}\na\n\nb`).join('\n');
    const outline = parseChapterOutline(script);
    expect(outline).toHaveLength(200);
    outline.forEach((chapter, i) => expect(chapter.line).toBe(i * 4 + 1));
  });
});

describe('chapterHeadingSnippet', () => {
  it('inserts a heading on the current line or the next one', () => {
    expect(chapterHeadingSnippet('', 0)).toBe('# ');
    expect(chapterHeadingSnippet('Hello', 5)).toBe('\n# ');
    expect(chapterHeadingSnippet('Hello\n', 6)).toBe('# ');
  });
  it('moves text after a mid-line caret or selection off the heading line', () => {
    expect(chapterHeadingSnippet('Hello world', 6)).toBe('\n# \n');
    expect(chapterHeadingSnippet('Hello world', 0)).toBe('# \n');
    expect(chapterHeadingSnippet('Hello world\nNext', 6, 11)).toBe('\n# ');
    expect(chapterHeadingSnippet('Hello world', 0, 5)).toBe('# \n');
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
  it('knows [emotion:] and says once when the engine will skip it (#3422)', () => {
    const script = '# C\n[emotion:quietly terrified] Hello? [EMOTION:] Bye. [emotion:sad] Gone.';
    // Parsed and stripped, never read aloud: never an unknown tag.
    expect(validateScript(script, {})).toEqual([]);
    expect(validateScript(script, { emotionIgnoredBy: 'OmniVoice' })).toEqual([
      { type: 'emotion_ignored', name: 'OmniVoice' },
    ]);
    // No tag, no warning, whatever the engine.
    expect(validateScript('# C\nPlain.', { emotionIgnoredBy: 'OmniVoice' })).toEqual([]);
  });
});
