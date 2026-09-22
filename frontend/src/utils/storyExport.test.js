import { describe, it, expect } from 'vitest';
import {
  silenceBuffer,
  concatBuffers,
  encodeWav,
  isChapterLine,
  chapterTitle,
  formatTimecode,
  tracksByCharacter,
  buildCueSheet,
  cuesFromChapters,
  cueSheetFilename,
} from './storyExport';

function fakeBuffer(samples, sampleRate = 24000) {
  const data = Float32Array.from(samples);
  return { sampleRate, numberOfChannels: 1, length: data.length, getChannelData: () => data };
}

describe('silenceBuffer', () => {
  it('produces sampleRate * seconds zeroed samples (mono)', () => {
    const b = silenceBuffer(0.5, 24000);
    expect(b.length).toBe(12000);
    expect(b.numberOfChannels).toBe(1);
    expect(b.getChannelData(0).every((v) => v === 0)).toBe(true);
  });
});

describe('concatBuffers', () => {
  it('joins buffers in order into one of summed length', () => {
    const out = concatBuffers([fakeBuffer([1, 2]), fakeBuffer([3, 4, 5])], 24000);
    expect(out.length).toBe(5);
    expect(Array.from(out.getChannelData(0))).toEqual([1, 2, 3, 4, 5]);
  });
});

describe('encodeWav', () => {
  it('writes a 44-byte RIFF/WAVE PCM16 header', () => {
    const wav = encodeWav(fakeBuffer([0, 0.5, -0.5]), 24000);
    const dv = new DataView(wav);
    const tag = (o) =>
      String.fromCharCode(
        dv.getUint8(o),
        dv.getUint8(o + 1),
        dv.getUint8(o + 2),
        dv.getUint8(o + 3),
      );
    expect(tag(0)).toBe('RIFF');
    expect(tag(8)).toBe('WAVE');
    expect(tag(36)).toBe('data');
    expect(dv.getUint16(22, true)).toBe(1); // mono
    expect(dv.getUint32(24, true)).toBe(24000); // sample rate
    expect(dv.getUint16(34, true)).toBe(16); // bits/sample
    expect(wav.byteLength).toBe(44 + 3 * 2); // header + 3 int16 samples
  });
});

describe('chapter helpers', () => {
  it('detects + titles H1 chapter lines (H1-only, #27)', () => {
    expect(isChapterLine('# Chapter One')).toBe(true);
    expect(isChapterLine('  # Indented')).toBe(true); // leading space ok
    // #27 convergence: H2–H6 narrate as body, not chapter breaks.
    expect(isChapterLine('  ## Part 2 ')).toBe(false);
    expect(isChapterLine('### Deep')).toBe(false);
    // `# ` / `#   ` with no non-space title is body (matches server _HEADING_RE).
    expect(isChapterLine('# ')).toBe(false);
    expect(isChapterLine('#   ')).toBe(false);
    expect(isChapterLine('#Title')).toBe(false); // needs a space
    expect(isChapterLine('Not a chapter')).toBe(false);
    expect(chapterTitle('# Chapter One')).toBe('Chapter One');
    // strip only the single H1 marker; remainder verbatim (raw-title behavior).
    expect(chapterTitle('# [voice:x] Title')).toBe('[voice:x] Title');
  });
  it('formats timecodes HH:MM:SS', () => {
    expect(formatTimecode(0)).toBe('00:00:00');
    expect(formatTimecode(65)).toBe('00:01:05');
    expect(formatTimecode(3661)).toBe('01:01:01');
  });
  it('builds a cue sheet', () => {
    expect(
      buildCueSheet([
        { time: 0, title: 'Intro' },
        { time: 65, title: 'Two' },
      ]),
    ).toBe('00:00:00\tIntro\n00:01:05\tTwo');
  });
});

describe('tracksByCharacter', () => {
  it('groups spoken lines by character, skipping chapter headings', () => {
    const groups = tracksByCharacter([
      { character: 'narrator', text: '# Chapter 1' },
      { character: 'narrator', text: 'Once.' },
      { character: 'fox', text: 'Hi.' },
      { character: 'narrator', text: 'Then.' },
    ]);
    expect(groups.map((g) => g.character)).toEqual(['narrator', 'fox']);
    expect(groups[0].tracks).toHaveLength(2);
    expect(groups[1].tracks).toHaveLength(1);
  });
});

describe('cuesFromChapters', () => {
  it('starts at zero and accumulates each chapter duration', () => {
    expect(
      cuesFromChapters([
        { title: 'Intro', duration_s: 65 },
        { title: 'Two', duration_s: 120.5 },
        { title: 'Three', duration_s: 10 },
      ]),
    ).toEqual([
      { time: 0, title: 'Intro' },
      { time: 65, title: 'Two' },
      { time: 185.5, title: 'Three' },
    ]);
  });

  it('never goes backwards, whatever the durations say', () => {
    // A missing, negative, NaN or non-numeric duration contributes zero rather
    // than shifting every later chapter out of step with the audio.
    const cues = cuesFromChapters([
      { title: 'A', duration_s: 10 },
      { title: 'B', duration_s: -5 },
      { title: 'C' },
      { title: 'D', duration_s: Number.NaN },
      { title: 'E', duration_s: 'nonsense' },
      { title: 'F', duration_s: 7 },
    ]);
    expect(cues.map((c) => c.time)).toEqual([0, 10, 10, 10, 10, 10]);
    for (let i = 1; i < cues.length; i += 1) {
      expect(cues[i].time).toBeGreaterThanOrEqual(cues[i - 1].time);
      expect(Number.isFinite(cues[i].time)).toBe(true);
    }
  });

  it('treats an infinite duration as zero', () => {
    const cues = cuesFromChapters([
      { title: 'A', duration_s: 5 },
      { title: 'B', duration_s: Infinity },
      { title: 'C', duration_s: -Infinity },
      { title: 'D', duration_ms: Infinity },
      { title: 'E' },
    ]);
    expect(cues.map((c) => c.time)).toEqual([0, 5, 5, 5, 5]);
  });

  it('does not drift below a whole second from float addition', () => {
    // Ten 0.1 s chapters summed as float seconds give 0.9999999999999999,
    // which floors to 00:00:00 — a second early. Whole milliseconds cannot.
    const tenths = Array.from({ length: 10 }, (_, i) => ({ title: `c${i}`, duration_s: 0.1 }));
    const last = cuesFromChapters([...tenths, { title: 'end' }]).at(-1);
    expect(last.time).toBe(1);
    expect(buildCueSheet([last])).toBe('00:00:01\tend');
  });

  it('prefers the exact duration_ms the m4b chapters were built from', () => {
    expect(
      cuesFromChapters([{ title: 'A', duration_s: 60, duration_ms: 59996 }, { title: 'B' }])[1]
        .time,
    ).toBe(59.996);
    // Older backends send only duration_s.
    expect(cuesFromChapters([{ title: 'A', duration_s: 12.34 }, { title: 'B' }])[1].time).toBe(
      12.34,
    );
  });

  it('matches cumulative millisecond START offsets past 24 hours', () => {
    // 30 chapters of 59:59.999 each — the same ms the backend sums into START.
    const long = Array.from({ length: 30 }, (_, i) => ({ title: `c${i}`, duration_ms: 3_599_999 }));
    const cues = cuesFromChapters([...long, { title: 'end' }]);
    expect(cues.at(-1).time).toBe((30 * 3_599_999) / 1000);
    expect(formatTimecode(cues.at(-1).time)).toBe('29:59:59');
    expect(formatTimecode(100 * 3600)).toBe('100:00:00');
  });

  it('accepts a numeric string duration', () => {
    expect(cuesFromChapters([{ title: 'A', duration_s: '12.5' }, { title: 'B' }])[1].time).toBe(
      12.5,
    );
  });

  it('falls back to duration_s when duration_ms is blank', () => {
    for (const blank of ['', '  ', null, undefined]) {
      const cues = cuesFromChapters([
        { title: 'A', duration_s: 12.5, duration_ms: blank },
        { title: 'B' },
      ]);
      expect(cues[1].time).toBe(12.5);
    }
  });

  it('names a blank title through the caller-supplied fallback', () => {
    expect(cuesFromChapters([{ title: '' }], (n) => `Capítulo ${n}`)[0].title).toBe('Capítulo 1');
  });

  it('falls back to the chapter position when the title is blank', () => {
    expect(
      cuesFromChapters([{ title: '   ', duration_s: 1 }, { duration_s: 1 }, { title: null }]).map(
        (c) => c.title,
      ),
    ).toEqual(['Chapter 1', 'Chapter 2', 'Chapter 3']);
  });

  it('trims a padded title', () => {
    expect(cuesFromChapters([{ title: '  Prologue  ' }])[0].title).toBe('Prologue');
  });

  it('keeps one line and two fields per cue when a title holds tabs or line breaks', () => {
    const cues = cuesFromChapters([
      { title: 'Part\tOne', duration_s: 5 },
      { title: 'Two\r\nlines\u2028here', duration_s: 5 },
      { title: '\t\n\r' },
    ]);
    expect(cues.map((c) => c.title)).toEqual(['Part One', 'Two lines here', 'Chapter 3']);
    expect(buildCueSheet(cues).split('\n')).toEqual([
      '00:00:00\tPart One',
      '00:00:05\tTwo lines here',
      '00:00:10\tChapter 3',
    ]);
    // The format boundary also holds for cues not built by cuesFromChapters.
    expect(buildCueSheet([{ time: 0, title: 'A\tB\nC\vD\fE\u0085F\u2029G' }])).toBe(
      '00:00:00\tA B C D E F G',
    );
  });

  it('is total — empty, null and undefined all give no cues', () => {
    expect(cuesFromChapters([])).toEqual([]);
    expect(cuesFromChapters(null)).toEqual([]);
    expect(cuesFromChapters(undefined)).toEqual([]);
    expect(buildCueSheet(cuesFromChapters([]))).toBe('');
  });

  it('matches the embedded chapter offsets when a chapter is left out', () => {
    // The backend appends to chapters_meta — the source of the m4b's embedded
    // chapters — only on success, so a failed chapter must never contribute a
    // cue. Filtering happens at the call site; this pins what the helper does
    // with the filtered list: the following chapters keep the earlier offsets.
    const all = [
      { title: 'A', duration_s: 10, status: 'done' },
      { title: 'B', duration_s: 999, status: 'failed' },
      { title: 'C', duration_s: 20, status: 'done' },
    ];
    const rendered = all.filter((c) => c.status !== 'failed');
    expect(cuesFromChapters(rendered)).toEqual([
      { time: 0, title: 'A' },
      { time: 10, title: 'C' },
    ]);
  });
});

describe('cueSheetFilename', () => {
  it.each([
    ['audiobook_abc.m4b', 'audiobook_abc.txt'],
    ['story_abc.mp3', 'story_abc.txt'],
    ['/outputs/story_abc.m4b', 'story_abc.txt'],
    ['X.M4B', 'X.txt'],
    ['C:\\outputs\\story_abc.mp3', 'story_abc.txt'],
  ])('%s -> %s', (output, expected) => {
    expect(cueSheetFilename(output)).toBe(expected);
  });

  it.each(['weird.wav', 'report.tar.gz', 'noextension', '', null, undefined])(
    'falls back to cuesheet.txt for %s',
    (output) => {
      // Deliberately not "strip whatever extension is there" — that would turn
      // report.tar.gz into report.tar.txt.
      expect(cueSheetFilename(output)).toBe('cuesheet.txt');
    },
  );

  it('returns promptly on a hostile length', () => {
    // Both patterns are anchored with fixed alternations, so a long input
    // cannot make them backtrack.
    const started = performance.now();
    expect(cueSheetFilename('a'.repeat(100000) + '.m4b')).toMatch(/^a+\.txt$/);
    expect(performance.now() - started).toBeLessThan(1000);
  });
});
