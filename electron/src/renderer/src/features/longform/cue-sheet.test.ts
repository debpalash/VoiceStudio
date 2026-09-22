/**
 * What belongs in a chapter cue sheet.
 *
 * The .txt has to agree with the audio it describes. The backend appends to
 * `chapters_meta` — the source of the m4b's embedded FFMETADATA chapters —
 * only after a chapter renders, and skips past a failure, so a failed chapter
 * occupies no time in the finished file. Give it a cue and every later
 * timestamp is wrong by that chapter's would-be duration.
 *
 * The timecode maths lives in `frontend/src/utils/storyExport` and is tested
 * there; this pins the part that is specific to a render — which chapters
 * count, and when there is nothing worth downloading.
 */
import { describe, expect, it } from 'vitest';
import { cueSheetFor, renderedChapters } from './cue-sheet';
import type { AudiobookRenderChapter } from './longform-session';

const chapter = (title: string, status: string, duration_s?: number): AudiobookRenderChapter => ({
  title,
  status,
  ...(duration_s === undefined ? {} : { duration_s }),
});

describe('renderedChapters', () => {
  it('keeps what rendered, fresh or resumed from cache', () => {
    // A cached chapter is in the finished audio exactly like a fresh one and
    // carries a real duration, so it takes a cue.
    const kept = renderedChapters([
      chapter('Fresh', 'done', 10),
      chapter('Cached', 'cached', 20),
      chapter('Broken', 'failed'),
    ]);
    expect(kept.map((c) => c.title)).toEqual(['Fresh', 'Cached']);
  });

  it('is total for a missing list', () => {
    expect(renderedChapters(null)).toEqual([]);
    expect(renderedChapters(undefined)).toEqual([]);
    expect(renderedChapters([])).toEqual([]);
  });
});

describe('cueSheetFor', () => {
  it('starts at zero and steps by each chapter duration', () => {
    const sheet = cueSheetFor(
      [chapter('Intro', 'done', 65), chapter('Middle', 'cached', 120), chapter('End', 'done', 5)],
      'audiobook_ab12.m4b',
    );
    expect(sheet?.body).toBe('00:00:00\tIntro\n00:01:05\tMiddle\n00:03:05\tEnd');
    expect(sheet?.filename).toBe('audiobook_ab12.txt');
  });

  it('gives a failed chapter no time at all', () => {
    // The load-bearing case. "Two" never reached the audio, so "Three" starts
    // where "Two" would have — 10s — not 10s + whatever "Two" claimed.
    const sheet = cueSheetFor(
      [chapter('One', 'done', 10), chapter('Two', 'failed', 999), chapter('Three', 'done', 30)],
      'audiobook_ab12.m4b',
    );
    expect(sheet?.body).toBe('00:00:00\tOne\n00:00:10\tThree');
    expect(sheet?.body).not.toContain('Two');
  });

  it('separates the timestamp from the title with a tab', () => {
    const sheet = cueSheetFor([chapter('A Title With Spaces', 'done', 1)], 'x.mp3');
    expect(sheet?.body).toBe('00:00:00\tA Title With Spaces');
    // A space separator would make the title unsplittable from the timestamp.
    expect(sheet?.body.split('\t')).toHaveLength(2);
  });

  it('names a chapter that arrived without a title', () => {
    const sheet = cueSheetFor([chapter('', 'done', 5), chapter('   ', 'done', 5)], 'story_x.mp3');
    expect(sheet?.body).toBe('00:00:00\tChapter 1\n00:00:05\tChapter 2');
  });

  it('offers nothing when every chapter failed', () => {
    // A render where nothing succeeded produces no audio file either, so there
    // is no sheet to write — and the caller shows no button.
    expect(cueSheetFor([chapter('One', 'failed'), chapter('Two', 'failed')], 'x.m4b')).toBeNull();
  });

  it('offers nothing before a render has produced chapters', () => {
    expect(cueSheetFor([], 'x.m4b')).toBeNull();
    expect(cueSheetFor(null, 'x.m4b')).toBeNull();
  });

  it('still writes a sheet when the output name is unrecognisable', () => {
    // The cues are worth keeping even if the filename has to fall back.
    const sheet = cueSheetFor([chapter('One', 'done', 1)], 'something.wav');
    expect(sheet?.filename).toBe('cuesheet.txt');
    expect(cueSheetFor([chapter('One', 'done', 1)], '')?.filename).toBe('cuesheet.txt');
  });

  it('drops a path prefix from the output name', () => {
    expect(cueSheetFor([chapter('One', 'done', 1)], '/outputs/story_cd34.mp3')?.filename).toBe(
      'story_cd34.txt',
    );
  });

  it('survives a chapter with no duration instead of losing the rest', () => {
    const sheet = cueSheetFor(
      [chapter('One', 'done'), chapter('Two', 'done', 10), chapter('Three', 'done', 5)],
      'x.m4b',
    );
    expect(sheet?.body).toBe('00:00:00\tOne\n00:00:00\tTwo\n00:00:10\tThree');
  });
});
