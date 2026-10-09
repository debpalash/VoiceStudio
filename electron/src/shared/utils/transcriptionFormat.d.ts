import type { TranscriptEntry, TranscriptSegment } from './transcriptionsStore';
export function segTimeRange(segment?: TranscriptSegment | null): string;
export function speakerTranscript(entry: TranscriptEntry): string | null;
export function preferredTranscript(entry: TranscriptEntry): string;
export function formatTranscriptExport(entries: TranscriptEntry[]): string;
