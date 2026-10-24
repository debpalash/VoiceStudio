export type DubExportFormat = 'mp4' | 'wav' | 'mp3' | 'srt' | 'vtt' | 'ass' | 'stems' | 'clips';
export interface DubExportOptions {
  jobId: string;
  format: DubExportFormat;
  track: string;
  tracks: string[];
  preserveBg: boolean;
  bedGain?: number;
  burn: boolean;
  dual: boolean;
  karaoke: boolean;
  bitrate: string;
}
export function dubExportRequest(options: DubExportOptions) {
  const { jobId, format, track, tracks, preserveBg, burn, dual, karaoke, bitrate } = options;
  if (!jobId || !track || (format === 'mp4' && (!tracks.length || !tracks.includes(track))))
    throw new Error('No export track selected');
  const query = new URLSearchParams({ lang: track });
  if (['mp4', 'wav', 'mp3'].includes(format))
    query.set('bed_gain', String(backgroundGain(options.bedGain)));
  let endpoint: string;
  let extension: string = format;
  if (format === 'mp4') {
    endpoint = 'download';
    query.delete('lang');
    query.set('include_tracks', tracks.join(','));
    query.set('default_track', track);
    query.set('preserve_bg', String(preserveBg));
    query.set('burn_subs', String(burn));
    query.set('dual', String(burn && dual));
    query.set('karaoke', String(burn && karaoke && !dual));
  } else if (format === 'wav' || format === 'mp3') {
    endpoint = format === 'wav' ? 'download-audio' : 'download-mp3';
    query.set('preserve_bg', String(preserveBg));
    if (format === 'mp3')
      query.set('bitrate', ['128', '192', '256', '320'].includes(bitrate) ? bitrate + 'k' : '192k');
  } else if (format === 'stems' || format === 'clips') {
    endpoint = format === 'stems' ? 'export-stems' : 'export-segments';
    extension = 'zip';
  } else {
    endpoint = format;
    if (format !== 'ass') query.set('dual', String(dual));
  }
  return {
    path: `/dub/${endpoint}/${encodeURIComponent(jobId)}?${query}`,
    name: `voicestudio-${jobId.replace(/[^a-zA-Z0-9_-]/g, '_')}-${track.replace(/[^a-zA-Z0-9_-]/g, '_')}-${format}.${extension}`,
  };
}

export interface DubExportPreferences {
  format?: DubExportFormat;
  track?: string;
  excluded?: string[];
  preserveBg?: boolean;
  bedGain?: number;
  burn?: boolean;
  dual?: boolean;
  karaoke?: boolean;
  bitrate?: string;
}
export function restoreExportPreferences(raw: unknown): DubExportPreferences {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const value = raw as Record<string, unknown>;
  const result: DubExportPreferences = {};
  if (['mp4', 'wav', 'mp3', 'srt', 'vtt', 'ass', 'stems', 'clips'].includes(String(value.format)))
    result.format = value.format as DubExportFormat;
  if (typeof value.track === 'string') result.track = value.track;
  if (Array.isArray(value.excluded))
    result.excluded = value.excluded.filter((code): code is string => typeof code === 'string');
  for (const key of ['preserveBg', 'burn', 'dual', 'karaoke'] as const)
    if (typeof value[key] === 'boolean') result[key] = value[key];
  if (['128', '192', '256', '320'].includes(String(value.bitrate)))
    result.bitrate = String(value.bitrate);
  if (typeof value.bedGain === 'number' && Number.isFinite(value.bedGain) && value.bedGain >= 0 && value.bedGain <= 4)
    result.bedGain = value.bedGain;
  return result;
}

export function backgroundGain(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 4
    ? value : 1;
}

/** Preview and exported media use the same saved mix options. */
export function dubPreviewPath(
  jobId: string, track: string, inputType: 'audio' | 'video',
  preferences: DubExportPreferences = {}, revision = '',
): string {
  const query = new URLSearchParams({
    lang: track,
    preserve_bg: String(preferences.preserveBg ?? true),
    bed_gain: String(backgroundGain(preferences.bedGain)),
  });
  if (inputType === 'video') query.set('mix', 'surgical2');
  if (revision) query.set('v', revision);
  const endpoint = inputType === 'audio' ? 'download-audio' : 'preview-video';
  return `/dub/${endpoint}/${encodeURIComponent(jobId)}?${query}`;
}
