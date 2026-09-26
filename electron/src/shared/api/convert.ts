import { apiPost } from './client';
import { withTtsInflight } from './generate';

/** JSON body of a successful POST /convert (speech-to-speech voice change). */
export interface ConvertResult {
  id: string;
  /** Backend-relative path (`/audio/<take>.wav`) — prefix with `API` to play. */
  audio_url: string;
  /** What the ASR heard in the source clip (the re-synthesized script). */
  text: string;
  duration_s: number;
  gen_time_s: number;
}

/**
 * Convert a spoken clip into an existing voice profile's voice.
 * Multipart: `audio` (source clip), `profile_id`, `match_duration` ('1'|'0').
 *
 * Runs under the same process-wide TTS admission guard as /generate
 * (`withTtsInflight`): a convert IS a native synthesis, and overlapping it
 * with another render is the exact capacity/crash class the guard exists
 * for. A concurrent request rejects with `TtsGenerationBusyError`.
 *
 * `signal` mirrors `generateSpeech`: aborting releases the admission slot,
 * so an obsolete convert (inputs changed mid-flight) doesn't block the next.
 */
export async function convertSpeech(
  formData: FormData,
  { signal }: { signal?: AbortSignal } = {},
): Promise<ConvertResult> {
  return withTtsInflight(() => apiPost<ConvertResult>('/convert', formData, { signal }));
}
