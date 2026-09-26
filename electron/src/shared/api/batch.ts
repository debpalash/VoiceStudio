/**
 * Batch dubbing API — wraps the /batch/* backend endpoints.
 *
 * Used by BatchQueue and BatchAddDialog to enqueue, monitor, and
 * manage batch dub jobs.
 */
import { apiJson, apiPost, apiDelete, ApiError } from './client';

export interface BatchJob {
  id: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  filename: string;
  langs: string[];
  voice_id?: string;
  preserve_bg: boolean;
  created_at: number;
  started_at?: number;
  finished_at?: number;
  error?: string;
  progress?: {
    stage: string;
    percent: number;
    current_lang?: string;
    current_segment?: number;
    total_segments?: number;
    segments_count?: number;
  };
  outputs?: Record<string, string>;
}

interface NativeWatchUpload {
  __voiceStudioNativeWatchUpload: true;
  token: string;
  name: string;
  size: number;
  mtime: number;
}

function isNativeWatchUpload(file: File | NativeWatchUpload): file is NativeWatchUpload {
  return !(file instanceof File) && file.__voiceStudioNativeWatchUpload === true;
}

/** List batch jobs, optionally filtered by status. */
export async function listBatchJobs(status?: string, limit = 50): Promise<BatchJob[]> {
  const qs = new URLSearchParams();
  if (status) qs.set('status', status);
  qs.set('limit', String(limit));
  return apiJson<BatchJob[]>(`/batch/jobs?${qs.toString()}`);
}

/** Get a single batch job (used to resolve why a job left the active list). */
export async function getBatchJob(id: string): Promise<BatchJob> {
  return apiJson<BatchJob>(`/batch/jobs/${id}`);
}

/** Enqueue a video for batch dubbing. */
export async function enqueueBatchJob(
  file: File | NativeWatchUpload,
  langs: string[],
  voiceId?: string,
  preserveBg = true,
): Promise<{ job_id: string; status: string; queue_position: number }> {
  if (isNativeWatchUpload(file)) {
    const { invoke } = await import('@tauri-apps/api/core');
    const reply = await invoke<{ status: number; body: unknown }>('watch_folder_enqueue', {
      token: file.token,
      name: file.name,
      expectedSize: file.size,
      expectedMtime: file.mtime,
      langs,
      voiceId,
      preserveBg,
    });
    if (reply.status < 200 || reply.status >= 300) {
      const detail = (reply.body as { detail?: unknown })?.detail ?? reply.body;
      throw new ApiError(`Watch-folder upload failed (${reply.status})`, {
        status: reply.status,
        detail,
      });
    }
    return reply.body as { job_id: string; status: string; queue_position: number };
  }
  const form = new FormData();
  form.append('video', file);
  form.append('langs', langs.join(','));
  if (voiceId) form.append('voice_id', voiceId);
  form.append('preserve_bg', String(preserveBg));
  return apiPost('/batch/enqueue', form);
}

/** Cancel a batch job. */
export async function cancelBatchJob(id: string): Promise<unknown> {
  return apiPost(`/batch/jobs/${id}/cancel`, {});
}

/** Delete a batch job and its files. */
export async function deleteBatchJob(id: string): Promise<unknown> {
  const res = await apiDelete(`/batch/jobs/${id}`);
  return res.json();
}
