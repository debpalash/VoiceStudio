import type { TranscriptEntry, TranscriptSegment } from './transcriptionsStore';
export function segTimeRange(segment?: TranscriptSegment | null): string;
export function speakerTranscript(entry: TranscriptEntry): string | null;
export function preferredTranscript(entry: TranscriptEntry): string;
export type TranscriptExportFormat = 'history' | 'text' | 'srt';
export function canExportTranscriptSubtitles(entry: TranscriptEntry): boolean;
export function formatTranscriptExport(
  entries: TranscriptEntry[],
  options?: { format?: TranscriptExportFormat },
): string;
