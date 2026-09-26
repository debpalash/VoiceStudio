export interface TranscriptSegment {
  start?: number | null;
  end?: number | null;
  text: string;
}
export interface TranscriptEntry {
  id: number;
  text: string;
  refined_text?: string;
  language: string;
  duration_s: number;
  segments: TranscriptSegment[];
  timestamp: string;
}
export const TRANSCRIPTIONS_KEY: string;
export const TRANSCRIPTION_EVENT: string;
export function loadTranscriptions(): TranscriptEntry[];
export function addTranscription(entry: Partial<TranscriptEntry>): TranscriptEntry;
export function subscribeTranscriptions(listener: (entries: TranscriptEntry[]) => void): () => void;
export function removeTranscription(id: number): void;
