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
  it('collapses every field and line break in titles', () => {
    expect(buildCueSheet([{ time: 0, title: ' A\t\n\r\v\f\u0085\u2028\u2029B ' }])).toBe(
      '00:00:00\tA B',
    );
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
  it('places each heading at the accumulated millisecond boundary', () => {
    const result = cuesFromChapters([
      { title: 'Lighthouse', duration_ms: 2345 },
      { title: 'Low Tide', duration_ms: 655 },
      { title: 'Open Water' },
    ]);
    expect(result.map(({ time }) => time)).toEqual([0, 2.345, 3]);
    expect(result.map(({ title }) => title)).toEqual(['Lighthouse', 'Low Tide', 'Open Water']);
  });
  it('treats unusable lengths as zero rather than corrupting later offsets', () => {
    const malformed = [-4, NaN, 'unknown', Infinity].map((duration_s) => ({ duration_s }));
    malformed.push({ duration_ms: -Infinity });
    const offsets = cuesFromChapters([...malformed, { title: 'Safe shore' }]).map(
      (cue) => cue.time,
    );
    expect(new Set(offsets)).toEqual(new Set([0]));
    expect(offsets.every(Number.isFinite)).toBe(true);
  });
  it('accumulates fractional seconds through integer milliseconds', () => {
    const sequence = Array.from({ length: 8 }, () => ({ duration_s: 0.125 }));
    const finalCue = cuesFromChapters([...sequence, { title: 'Exactly one second' }]).at(-1);
    expect(finalCue).toEqual({ time: 1, title: 'Exactly one second' });
    expect(buildCueSheet([finalCue])).toBe('00:00:01\tExactly one second');
  });
  it('chooses exact milliseconds before legacy seconds', () => {
    const result = cuesFromChapters([
      { duration_ms: '2750', duration_s: 100 },
      { duration_ms: '', duration_s: '1.5' },
      { duration_ms: null, duration_s: 0.25 },
      { title: 'Checkpoint' },
    ]);
    expect(result.at(-1)?.time).toBe(4.5);
  });
  it('allows an audiobook timeline to pass the 24-hour boundary', () => {
    const later = cuesFromChapters([{ duration_ms: 93_600_875 }, { title: 'Day two' }])[1];
    expect(later.time).toBe(93_600.875);
    expect(buildCueSheet([later])).toBe('26:00:00\tDay two');
  });
  it('normalizes supplied headings and delegates missing names', () => {
    const named = cuesFromChapters(
      [{ title: '  Dawn  ' }, {}, { title: 'North\nStar' }],
      (position) => `Segment-${position}`,
    );
    expect(named.map((cue) => cue.title)).toEqual(['Dawn', 'Segment-2', 'North Star']);
    expect(cuesFromChapters([{}])).toMatchObject([{ title: 'Chapter 1' }]);
  });
  it.each([null, undefined, []])('accepts empty input %j', (value) => {
    expect(cuesFromChapters(value)).toEqual([]);
  });
  it('needs no placeholders for chapters already removed by its caller', () => {
    const deliveredOnly = [{ title: 'Before', duration_ms: 875 }, { title: 'After' }];
    expect(cuesFromChapters(deliveredOnly).at(-1)).toEqual({ time: 0.875, title: 'After' });
  });
});

describe('cueSheetFilename', () => {
  it.each([
    ['/tmp/sea-story.m4b', 'sea-story.txt'],
    ['C:\\audio\\episode-07.MP3', 'episode-07.txt'],
    ['ARCHIVE.M4B', 'ARCHIVE.txt'],
    ['mixdown.wav', 'cuesheet.txt'],
    ['extensionless', 'cuesheet.txt'],
    ['', 'cuesheet.txt'],
    [null, 'cuesheet.txt'],
  ])('maps %j to %s', (input, expected) => {
    expect(cueSheetFilename(input)).toBe(expected);
  });
  it('handles a hostile-length name without nonlinear work', () => {
    const stem = 'z'.repeat(80_000);
    const name = `${stem}.mp3`;
    const started = performance.now();
    expect(cueSheetFilename(name)).toBe(`${stem}.txt`);
    expect(performance.now() - started).toBeLessThan(1000);
  });
});
