import { apiJson } from './client';
import { acquireSynthesis } from '@/lib/synthesis-lock';
import { tr } from '@/lib/i18n-text';
import { beginAppActivity } from '@/lib/app-activity';
export interface ConvertResult {
  id: string;
  audio_url: string;
  text: string;
  duration_s: number;
  gen_time_s: number;
}
export async function convertSpeech(
  audio: File,
  profileId: string,
  matchDuration: boolean,
  signal: AbortSignal,
  language = 'Auto',
): Promise<ConvertResult> {
  signal.throwIfAborted();
  const release = acquireSynthesis();
  if (!release) throw new Error(tr('tts_errors.generation_in_progress'));
  const finishActivity = beginAppActivity('synthesis');
  try {
    const body = new FormData();
    body.append('audio', audio);
    body.append('profile_id', profileId);
    body.append('match_duration', matchDuration ? '1' : '0');
    if (language !== 'Auto') body.append('language', language);
    return await apiJson<ConvertResult>('/convert', { method: 'POST', body, signal });
  } finally {
    finishActivity();
    release();
  }
}
