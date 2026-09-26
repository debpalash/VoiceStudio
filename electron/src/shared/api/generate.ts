import { API, apiUrl, apiFetch, apiJson } from './client';
import { useAppStore } from '../store';
import { warnIfEngineUnderProvisioned } from '../utils/generatePreflight';

/**
 * Atomically admit one synthesis and hold its in-flight count for `fn`.
 */
export async function withTtsInflight<T>(fn: () => Promise<T>): Promise<T> {
  const state = useAppStore.getState();
  if ((state.ttsInflight ?? 0) > 0) throw new TtsGenerationBusyError();
  state.addTtsInflight?.(1);
  try {
    return await fn();
  } finally {
    useAppStore.getState().addTtsInflight?.(-1);
  }
}

export class TtsGenerationBusyError extends Error {
  code = 'tts_generation_busy';

  constructor() {
    super('A generation is already running. Wait for it to finish before starting another.');
    this.name = 'TtsGenerationBusyError';
  }
}

async function postGenerateSpeech(formData: FormData, signal?: AbortSignal): Promise<Response> {
  void warnIfEngineUnderProvisioned(
    typeof formData?.get === 'function' ? String(formData.get('text') ?? '') : '',
  );
  return apiFetch('/generate', { method: 'POST', body: formData, signal });
}

export async function generateSpeech(
  formData: FormData,
  { signal }: { signal?: AbortSignal } = {},
): Promise<Response> {
  return withTtsInflight(() => postGenerateSpeech(formData, signal));
}

/** Streaming already holds admission until its response body is drained. */
export async function generateSpeechWithinAdmission(
  formData: FormData,
  { signal }: { signal?: AbortSignal } = {},
): Promise<Response> {
  return postGenerateSpeech(formData, signal);
}

export async function listHistory(): Promise<unknown> {
  return apiJson('/history');
}

export async function clearHistory(): Promise<Response> {
  return apiFetch('/history', { method: 'DELETE' });
}

export async function setHistoryStarred(id: string, starred: boolean): Promise<unknown> {
  return apiJson(`/history/${id}/starred`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ starred }),
  });
}

export function audioUrl(filename: string): string {
  return `${API}/audio/${filename}`;
}

export function audioUrlWithCacheBust(filename: string): string {
  return `${apiUrl('/audio/' + filename)}?t=${Date.now()}`;
}
