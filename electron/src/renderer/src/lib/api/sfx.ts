import { apiJson } from './client';
import { acquireSynthesis } from '@/lib/synthesis-lock';
import { tr } from '@/lib/i18n-text';
import { beginAppActivity } from '@/lib/app-activity';

export interface SfxStatus {
  engine_id: string;
  installed: boolean;
  available: boolean;
  /** False on a host the model cannot run on (no NVIDIA GPU). */
  supported: boolean;
  /** Why Generate is disabled, or null when it can run. */
  reason: string | null;
  install_allowed: boolean;
  download_bytes: number;
  license: string;
  min_seconds: number;
  max_seconds: number;
  default_seconds: number;
  max_prompt_chars: number;
}

export interface SfxResult {
  id: string;
  audio_url: string;
  duration_s: number;
  gen_time_s: number;
  seed: number;
  engine: string;
}

export const SFX_ENGINE_ID = 'moss-soundeffect';

export function fetchSfxStatus(signal?: AbortSignal): Promise<SfxStatus> {
  return apiJson<SfxStatus>('/api/sfx/status', { signal });
}

export async function generateSfx(
  prompt: string,
  duration: number,
  signal: AbortSignal,
): Promise<SfxResult> {
  signal.throwIfAborted();
  const release = acquireSynthesis();
  if (!release) throw new Error(tr('tts_errors.generation_in_progress'));
  const finishActivity = beginAppActivity('synthesis');
  try {
    return await apiJson<SfxResult>('/api/sfx/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, duration }),
      signal,
    });
  } finally {
    finishActivity();
    release();
  }
}
