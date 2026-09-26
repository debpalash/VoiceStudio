/**
 * Synced-lyrics timing (audiobook player). `evenSplitWords` mirrors
 * `backend/services/karaoke_ass.even_split_words` (same fixtures as
 * tests/test_karaoke_ass.py's even-split cases); the chapter split mirrors
 * `backend/services/longform_parser.py`'s drop rules so cue indices line up
 * with the render stream's chapter list.
 */
import { describe, it, expect } from 'vitest';
import {
  activeWordIndex,
  buildLyricsTimeline,
  evenSplitWords,
  scriptChapters,
} from './audiobookLyrics';

describe('evenSplitWords', () => {
  it('distributes tokens uniformly over [start, end]', () => {
    const words = evenSplitWords('one two three four', 10, 12);
    expect(words.map((w) => w.text)).toEqual(['one', 'two', 'three', 'four']);
    expect(words[0].start).toBeCloseTo(10);
    expect(words[0].end).toBeCloseTo(10.5);
    expect(words[2].start).toBeCloseTo(11);
    expect(words[3].end).toBeCloseTo(12);
  });

  it('collapses arbitrary whitespace and returns [] for blank text', () => {
    expect(evenSplitWords('  a \n b\t c  ', 0, 3).map((w) => w.text)).toEqual(['a', 'b', 'c']);
    expect(evenSplitWords('   ', 0, 3)).toEqual([]);
    expect(evenSplitWords('', 0, 3)).toEqual([]);
  });

  it('clamps a degenerate span to zero-length windows instead of going backwards', () => {
    const words = evenSplitWords('a b', 5, 4);
    expect(words[0].start).toBeCloseTo(5);
    expect(words[1].end).toBeCloseTo(5);
  });
});

describe('scriptChapters', () => {
  it('splits on H1 headings and keeps intro text as its own chapter', () => {
    const chs = scriptChapters('Intro line.\n# One\nAlpha beta.\n# Two\nGamma.');
    expect(chs.map((c) => c.title)).toEqual(['Chapter 1', 'One', 'Two']);
    expect(chs[1].tokens).toEqual(['Alpha', 'beta.']);
  });

  it('strips control tokens but keeps reaction tags as highlightable words', () => {
    const [ch] = scriptChapters(
      '# C\n[voice:Mara] Hello [pause 500ms] there [laughs] [slow]end[/slow]',
    );
    expect(ch.tokens).toEqual(['Hello', 'there', '[laughs]', 'end']);
  });

  it('expands spell markup into the same separately spoken tokens as the renderer', () => {
    const [ch] = scriptChapters('# C\nCall [spell]USA[/spell] now.');
    expect(ch.tokens).toEqual(['Call', 'U', 'S', 'A', 'now.']);
  });

  it("mirrors the parser's drop rules: pause-only chapters survive, empty ones don't", () => {
    const chs = scriptChapters('# Silence\n[pause 1s]\n# Nothing\n[voice:Mara]\n# Words\nHi.');
    expect(chs.map((c) => c.title)).toEqual(['Silence', 'Words']);
    expect(chs[0].tokens).toEqual([]);
  });

  it('returns [] for a blank script', () => {
    expect(scriptChapters('')).toEqual([]);
    expect(scriptChapters('   \n ')).toEqual([]);
  });
});

const SCRIPT = '# One\nAlpha beta gamma delta.\n# Two\nEpsilon zeta.';

describe('buildLyricsTimeline — stream chapter durations', () => {
  it('lays chapters end to end and even-splits words inside each', () => {
    const { chapters, words } = buildLyricsTimeline(SCRIPT, {
      chapters: [
        { title: 'One', status: 'done', duration_s: 8 },
        { title: 'Two', status: 'cached', duration_s: 4 },
      ],
    });
    expect(chapters.map((c) => [c.title, c.start, c.end])).toEqual([
      ['One', 0, 8],
      ['Two', 8, 12],
    ]);
    expect(words).toHaveLength(6);
    expect(words[0]).toMatchObject({ text: 'Alpha', start: 0, end: 2, chapterIndex: 0 });
    expect(words[4].start).toBeCloseTo(8); // Epsilon opens chapter two
    expect(words[5].end).toBeCloseTo(12);
  });

  it('skips failed chapters — they are absent from the muxed audio', () => {
    const { chapters, words } = buildLyricsTimeline(SCRIPT, {
      chapters: [
        { title: 'One', status: 'failed' },
        { title: 'Two', status: 'done', duration_s: 4 },
      ],
    });
    expect(chapters).toHaveLength(1);
    expect(chapters[0]).toMatchObject({ title: 'Two', start: 0, end: 4 });
    expect(words.map((w) => w.text)).toEqual(['Epsilon', 'zeta.']);
  });

  it('falls back when the stream list no longer matches the script (post-render edit)', () => {
    const { chapters } = buildLyricsTimeline(SCRIPT + '\n# Three\nNew words.', {
      chapters: [
        { title: 'One', status: 'done', duration_s: 8 },
        { title: 'Two', status: 'done', duration_s: 4 },
      ],
      duration: 18,
    });
    expect(chapters).toHaveLength(3);
    expect(chapters[2].end).toBeCloseTo(18);
  });

  it('falls back when a render stopped mid-book left chapters untimed', () => {
    const { words } = buildLyricsTimeline(SCRIPT, {
      chapters: [
        { title: 'One', status: 'done', duration_s: 8 },
        { title: '', status: 'pending' },
      ],
      duration: 12,
    });
    expect(words).toHaveLength(6);
    expect(words[5].end).toBeCloseTo(12);
  });
});

describe('buildLyricsTimeline — proportional fallback (reload case)', () => {
  it('splits the file duration across all words, chapters proportional to word count', () => {
    const { chapters, words } = buildLyricsTimeline(SCRIPT, { duration: 12 });
    // 6 words over 12s = 2s each: chapter One (4 words) 0–8, Two (2 words) 8–12.
    expect(chapters[0]).toMatchObject({ start: 0, end: 8 });
    expect(chapters[1]).toMatchObject({ start: 8, end: 12 });
    expect(words[1]).toMatchObject({ start: 2, end: 4 });
  });

  it('yields nothing without a usable duration', () => {
    expect(buildLyricsTimeline(SCRIPT, {})).toEqual({ chapters: [], words: [] });
    expect(buildLyricsTimeline(SCRIPT, { duration: 0 })).toEqual({ chapters: [], words: [] });
    expect(buildLyricsTimeline(SCRIPT, { duration: NaN })).toEqual({ chapters: [], words: [] });
  });

  it('yields nothing for a blank script', () => {
    expect(buildLyricsTimeline('', { duration: 10 })).toEqual({ chapters: [], words: [] });
  });
});

describe('activeWordIndex', () => {
  const words = buildLyricsTimeline(SCRIPT, { duration: 12 }).words; // 2s per word

  it('is -1 before the first word and tracks the word under t', () => {
    expect(activeWordIndex(words, -0.5)).toBe(-1);
    expect(activeWordIndex(words, 0)).toBe(0);
    expect(activeWordIndex(words, 1.99)).toBe(0);
    expect(activeWordIndex(words, 2)).toBe(1);
    expect(activeWordIndex(words, 9.5)).toBe(4);
  });

  it('keeps the last word lit at and past the end (karaoke gap behaviour)', () => {
    expect(activeWordIndex(words, 12)).toBe(5);
    expect(activeWordIndex(words, 99)).toBe(5);
  });

  it('handles empty/invalid input', () => {
    expect(activeWordIndex([], 1)).toBe(-1);
    expect(activeWordIndex(null, 1)).toBe(-1);
    expect(activeWordIndex(words, NaN)).toBe(-1);
  });
});
