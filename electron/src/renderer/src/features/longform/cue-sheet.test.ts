import { describe, expect, it } from 'vitest';
import { cueSheetFor, renderedChapters } from './cue-sheet';

type Sample = { status: string; title: string; duration_ms?: number; duration_s?: number };

const delivered = (title: string, milliseconds?: number, status = 'done'): Sample => {
  const item: Sample = { status, title };
  if (milliseconds !== undefined) item.duration_ms = milliseconds;
  return item;
};

describe('chapter timing export', () => {
  it('forms offsets from the delivered sequence, including cached audio', () => {
    const result = cueSheetFor(
      [delivered('Prologue', 2400, 'cached'), delivered('Night Train', 600)],
      '/exports/journey.m4b',
    );
    expect(result?.filename).toBe('journey.txt');
    expect(result?.body.split('\n')).toEqual(['00:00:00\tPrologue', '00:00:02\tNight Train']);
  });

  it('does not let an unsuccessful chapter consume timeline space', () => {
    const items = [
      delivered('Arrival', 800),
      delivered('Storm', 45_000, 'failed'),
      delivered('Afterwards', 300),
    ];
    expect(cueSheetFor(items, 'journey.wav')?.body).toBe('00:00:00\tArrival\n00:00:00\tAfterwards');
  });

  it('recognizes only terminal, usable chapter states', () => {
    const states = ['queued', 'rendering', 'paused', 'done', 'cached'];
    expect(
      renderedChapters(states.map((status) => delivered(status, 5, status))).map((x) => x.status),
    ).toEqual(['done', 'cached']);
  });

  it('offers no file when every attempted chapter failed', () => {
    const failures = [delivered('North', 900, 'failed'), delivered('South', 700, 'failed')];
    expect(cueSheetFor(failures, 'journey.m4b')).toBeNull();
  });

  it.each([
    { chapters: null, output: 'novel.m4b' },
    { chapters: undefined, output: 'novel.m4b' },
    { chapters: [], output: 'novel.m4b' },
    { chapters: [delivered('Opening')], output: '' },
    { chapters: [delivered('Opening')], output: null },
  ])('has no download for incomplete input: $chapters / $output', ({ chapters, output }) => {
    expect(cueSheetFor(chapters, output)).toBeNull();
  });

  it('numbers untitled delivered entries after filtering', () => {
    const data = [
      delivered('discarded', 7, 'failed'),
      delivered('', 1200),
      delivered('   ', 0, 'cached'),
    ];
    const rows = cueSheetFor(data, 'novel.m4b', (position) => `Section ${position}`)?.body.split(
      '\n',
    );
    expect(rows).toEqual(['00:00:00\tSection 1', '00:00:01\tSection 2']);
  });

  it('uses millisecond precision when both duration representations arrive', () => {
    const first = { ...delivered('Prelude', 1750), duration_s: 40 };
    const rows = cueSheetFor([first, delivered('Finale')], 'novel.m4b')?.body.split('\n');
    expect(rows?.at(-1)).toBe('00:00:01\tFinale');
  });
});
