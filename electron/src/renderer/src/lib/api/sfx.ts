import { ApiError, apiJson } from './client';
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
  /** Stable code for `reason`: host_unsupported, not_installed, weights_missing, sidecar_missing. */
  reason_code: string | null;
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

/** The page shows these catalog keys, never the server's English `reason`/`detail` text. */
const SFX_REASON_KEYS: Record<string, string> = {
  host_unsupported: 'sfx.reason_host_unsupported',
  weights_missing: 'sfx.reason_weights_missing',
  not_installed: 'sfx.reason_not_installed',
  sidecar_missing: 'sfx.reason_sidecar_missing',
};

const SFX_ERROR_KEYS: Record<string, string> = {
  sfx_prompt_required: 'sfx.prompt_required',
  sfx_prompt_too_long: 'sfx.error_prompt_too_long',
  sfx_bad_duration: 'sfx.error_bad_duration',
  sfx_unsupported_host: 'sfx.reason_host_unsupported',
  sfx_not_installed: 'sfx.reason_not_installed',
  sfx_busy: 'sfx.error_busy',
  sfx_timeout: 'sfx.error_timeout',
  sfx_out_of_memory: 'sfx.error_out_of_memory',
  sfx_failed: 'sfx.failed',
};

/** Catalog key for a status `reason_code`; unknown codes get the generic message. */
export function sfxReasonKey(code: string | null | undefined): string {
  return (code && SFX_REASON_KEYS[code]) || 'sfx.reason_unknown';
}

/** Catalog key for a generate failure; unknown codes and non-API errors get the generic message. */
export function sfxErrorKey(error: unknown): string {
  if (!(error instanceof ApiError)) return 'sfx.failed';
  const detail = error.payload?.detail;
  const code =
    detail && typeof detail === 'object' ? (detail as { code?: unknown }).code : undefined;
  return (typeof code === 'string' && SFX_ERROR_KEYS[code]) || 'sfx.failed';
}

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
