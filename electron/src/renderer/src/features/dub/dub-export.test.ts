import { expect, it } from 'vitest';
import {
  dubExportRequest,
  restoreExportPreferences,
  transcriptSyncBody,
  type DubExportOptions,
} from './dub-export';
const options: DubExportOptions = {
  jobId: 'job',
  format: 'mp4',
  track: 'fr',
  tracks: ['original', 'fr'],
  preserveBg: true,
  burn: true,
  dual: true,
  karaoke: true,
  bitrate: '320',
};
it('sends a changed background level only when the background is mixed in (#2964)', () => {
  const level = (overrides: Partial<DubExportOptions>) =>
    new URL(
      dubExportRequest({ ...options, ...overrides }).path,
      'http://localhost',
    ).searchParams.get('bed_level');
  expect(level({})).toBeNull();
  expect(level({ bedLevel: 100 })).toBeNull();
  expect(level({ bedLevel: 250 })).toBe('2.5');
  expect(level({ format: 'wav', bedLevel: 0 })).toBe('0');
  expect(level({ format: 'mp3', bedLevel: 900 })).toBe('4');
  expect(level({ format: 'mp3', bedLevel: 250, preserveBg: false })).toBeNull();
  expect(level({ format: 'srt', bedLevel: 250 })).toBeNull();
});
it('restores a saved background level within range', () => {
  expect(restoreExportPreferences({ bedLevel: 150 }).bedLevel).toBe(150);
  expect(restoreExportPreferences({ bedLevel: -5 }).bedLevel).toBe(0);
  expect(restoreExportPreferences({ bedLevel: '150' }).bedLevel).toBeUndefined();
});
it('exports only chosen video tracks with the chosen default and compatible subtitles', () => {
  const result = dubExportRequest(options);
  const url = new URL(result.path, 'http://localhost');
  expect(url.pathname).toBe('/dub/download/job');
  expect(Object.fromEntries(url.searchParams)).toEqual({
    include_tracks: 'original,fr',
    default_track: 'fr',
    preserve_bg: 'true',
    burn_subs: 'true',
    dual: 'true',
    karaoke: 'false',
  });
  expect(() => dubExportRequest({ ...options, tracks: [] })).toThrow();
  expect(() => dubExportRequest({ ...options, tracks: ['original'] })).toThrow();
});
it('targets the selected language for audio, sidecars and production packages', () => {
  for (const [format, endpoint, extension] of [
    ['mp3', 'download-mp3', 'mp3'],
    ['wav', 'download-audio', 'wav'],
    ['srt', 'srt', 'srt'],
    ['vtt', 'vtt', 'vtt'],
    ['ass', 'ass', 'ass'],
    ['stems', 'export-stems', 'zip'],
    ['clips', 'export-segments', 'zip'],
  ] as const) {
    const result = dubExportRequest({ ...options, format, preserveBg: false });
    const url = new URL(result.path, 'http://localhost');
    expect(url.pathname).toBe(`/dub/${endpoint}/job`);
    expect(url.searchParams.get('lang')).toBe('fr');
    expect(result.name.endsWith('.' + extension)).toBe(true);
    if (format === 'mp3') expect(url.searchParams.get('bitrate')).toBe('320k');
    if (format === 'wav' || format === 'mp3')
      expect(url.searchParams.get('preserve_bg')).toBe('false');
    if (format === 'srt' || format === 'vtt') expect(url.searchParams.get('dual')).toBe('true');
  }
});
it('exports the original video with only the original track before any dub (#3393)', () => {
  const url = new URL(
    dubExportRequest({ ...options, track: 'original', tracks: ['original'] }).path,
    'http://localhost',
  );
  expect(url.searchParams.get('include_tracks')).toBe('original');
  expect(url.searchParams.get('default_track')).toBe('original');
});
it('sends the edited transcript lines, keeping the source under an unspoken translation', () => {
  expect(
    transcriptSyncBody([
      { id: '0', start: -1, end: 1, text: 'Hola', text_original: 'Hello' },
      { id: '1', start: 1, end: 2, text: 'Same' },
    ]),
  ).toEqual({
    segments: [
      { id: '0', start: 0, end: 1, text: 'Hola', text_original: 'Hello', cue_source_id: undefined },
      {
        id: '1',
        start: 1,
        end: 2,
        text: 'Same',
        text_original: undefined,
        cue_source_id: undefined,
      },
    ],
  });
});
