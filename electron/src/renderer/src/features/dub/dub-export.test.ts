import { expect, it } from 'vitest';
import { backgroundGain, dubExportRequest, dubPreviewPath, restoreExportPreferences, type DubExportOptions } from './dub-export';
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
it('exports only chosen video tracks with the chosen default and compatible subtitles', () => {
  const result = dubExportRequest(options);
  const url = new URL(result.path, 'http://localhost');
  expect(url.pathname).toBe('/dub/download/job');
  expect(Object.fromEntries(url.searchParams)).toEqual({
    bed_gain: '1',
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

it('uses the saved background level for all mixed exports and both previews', () => {
  for (const gain of [0, 1, 2, 4]) {
    const prefs = restoreExportPreferences(JSON.parse(JSON.stringify({ bedGain: gain, preserveBg: true })));
    for (const format of ['mp4', 'wav', 'mp3'] as const) {
      const url = new URL(dubExportRequest({ ...options, format, ...prefs }).path, 'http://localhost');
      expect(url.searchParams.get('bed_gain')).toBe(String(gain));
    }
    for (const kind of ['audio', 'video'] as const) {
      const url = new URL(dubPreviewPath('job', 'fr', kind, prefs, 'revision'), 'http://localhost');
      expect(url.searchParams.get('bed_gain')).toBe(String(gain));
      expect(url.searchParams.get('preserve_bg')).toBe('true');
      expect(url.searchParams.get('v')).toBe('revision');
    }
  }
  expect(new URL(dubPreviewPath('job', 'fr', 'video', { preserveBg: false }), 'http://localhost')
    .searchParams.get('preserve_bg')).toBe('false');
  for (const format of ['srt', 'vtt', 'ass', 'stems', 'clips'] as const)
    expect(new URL(dubExportRequest({ ...options, format, bedGain: 2 }).path, 'http://localhost')
      .searchParams.has('bed_gain')).toBe(false);
});
it('retains zero and rejects invalid saved background levels without changing legacy defaults', () => {
  expect(restoreExportPreferences({ bedGain: 0 }).bedGain).toBe(0);
  expect(backgroundGain(undefined)).toBe(1);
  expect(restoreExportPreferences({}).bedGain).toBeUndefined();
  for (const bad of [-1, 4.01, Infinity, NaN, '2', null]) {
    expect(restoreExportPreferences({ bedGain: bad }).bedGain).toBeUndefined();
    expect(backgroundGain(bad)).toBe(1);
  }
});
