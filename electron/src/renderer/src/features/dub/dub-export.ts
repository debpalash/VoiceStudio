import { apiJson } from '@/lib/api/client';
import { cueSourceId } from '@shared/utils/segments';
export type DubExportFormat = 'mp4' | 'wav' | 'mp3' | 'srt' | 'vtt' | 'ass' | 'stems' | 'clips';
export interface DubExportOptions {
  jobId: string;
  format: DubExportFormat;
  track: string;
  tracks: string[];
  preserveBg: boolean;
  /** Background bed level in percent (#2964); 100 keeps the original level. */
  bedLevel?: number;
  burn: boolean;
  dual: boolean;
  karaoke: boolean;
  bitrate: string;
}
export const BED_LEVEL_DEFAULT = 100;
export const BED_LEVEL_MAX = 400;
export function clampBedLevel(value: unknown): number {
  const level = Number(value);
  if (!Number.isFinite(level)) return BED_LEVEL_DEFAULT;
  return Math.round(Math.min(BED_LEVEL_MAX, Math.max(0, level)));
}
function setBedLevel(query: URLSearchParams, preserveBg: boolean, bedLevel: number | undefined) {
  const level = clampBedLevel(bedLevel ?? BED_LEVEL_DEFAULT);
  // Only a changed level is sent, so default export URLs stay as they were.
  if (preserveBg && level !== BED_LEVEL_DEFAULT) query.set('bed_level', String(level / 100));
}
export function dubExportRequest(options: DubExportOptions) {
  const { jobId, format, track, tracks, preserveBg, bedLevel, burn, dual, karaoke, bitrate } =
    options;
  if (!jobId || !track || (format === 'mp4' && (!tracks.length || !tracks.includes(track))))
    throw new Error('No export track selected');
  const query = new URLSearchParams({ lang: track });
  let endpoint: string;
  let extension: string = format;
  if (format === 'mp4') {
    endpoint = 'download';
    query.delete('lang');
    query.set('include_tracks', tracks.join(','));
    query.set('default_track', track);
    query.set('preserve_bg', String(preserveBg));
    setBedLevel(query, preserveBg, bedLevel);
    query.set('burn_subs', String(burn));
    query.set('dual', String(burn && dual));
    query.set('karaoke', String(burn && karaoke && !dual));
  } else if (format === 'wav' || format === 'mp3') {
    endpoint = format === 'wav' ? 'download-audio' : 'download-mp3';
    query.set('preserve_bg', String(preserveBg));
    setBedLevel(query, preserveBg, bedLevel);
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
  bedLevel?: number;
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
  if (typeof value.bedLevel === 'number' && Number.isFinite(value.bedLevel))
    result.bedLevel = clampBedLevel(value.bedLevel);
  if (['128', '192', '256', '320'].includes(String(value.bitrate)))
    result.bitrate = String(value.bitrate);
  return result;
}

interface TranscriptLine {
  id: string;
  start: number;
  end: number;
  text: string;
  text_original?: string;
}
/** Before any dub exists, exports read the job's stored transcript; send the
 *  editor's lines first so the original video or subtitles carry the edits
 *  (#3393). The backend refuses once a dub exists. */
export function transcriptSyncBody(segments: readonly TranscriptLine[]) {
  return {
    segments: segments.map((segment) => ({
      id: segment.id,
      start: Math.max(0, segment.start),
      end: Math.max(0, segment.end),
      text: segment.text || '',
      text_original: segment.text_original || undefined,
      cue_source_id: cueSourceId(segment) ?? undefined,
    })),
  };
}
export async function syncDubTranscript(jobId: string, segments: readonly TranscriptLine[]) {
  await apiJson('/dub/sync-transcript/' + encodeURIComponent(jobId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(transcriptSyncBody(segments)),
  });
}
