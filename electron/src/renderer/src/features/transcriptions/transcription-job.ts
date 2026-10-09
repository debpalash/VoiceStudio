import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import i18next from 'i18next';
import { toast } from 'sonner';
import { apiJson, describeError } from '@/lib/api/client';
import { beginAppActivity } from '@/lib/app-activity';
import { recordActionBreadcrumb } from '@/lib/report-breadcrumb';
import { runRendererTask } from '@/lib/global-error-recovery';
import { addTranscription, type TranscriptEntry } from '@shared/utils/transcriptionsStore';

export type TranscriptionMode = 'fast' | 'accurate';
export type TranscriptionOutcome = 'saved' | 'not-ready' | 'failed' | 'cancelled' | 'busy';

interface JobState {
  running: boolean;
  file: File | null;
  error: string | null;
  /** Id of the newest entry this job saved, so a remounted page can select it. */
  savedId: number | null;
}

/**
 * File transcription runs here, not in the page component: leaving the page
 * must not abort the request or drop the result. Only cancelTranscription()
 * aborts.
 */
export const transcriptionJob = new Store<JobState>({
  running: false,
  file: null,
  error: null,
  savedId: null,
});

let controller: AbortController | null = null;
let viewing = false;

export function useTranscriptionJob(): JobState {
  return useStore(transcriptionJob, (state) => state);
}

/** The page reports whether it is on screen so a background finish can toast. */
export function setTranscriptionsViewing(value: boolean): void {
  viewing = value;
}

export function cancelTranscription(): void {
  controller?.abort();
}

export function clearTranscriptionFailure(): void {
  transcriptionJob.setState((state) => ({ ...state, error: null }));
}

function openTranscriptions(): void {
  runRendererTask('Navigate to transcriptions', async () => {
    const { router } = await import('@/router');
    await router.navigate({ to: '/transcriptions' });
  });
}

export async function startTranscription(
  audio: File,
  mode: TranscriptionMode,
  options: { diarize?: boolean } = {},
): Promise<TranscriptionOutcome> {
  if (controller) return 'busy';
  const ctl = new AbortController();
  controller = ctl;
  recordActionBreadcrumb(`transcribe:${mode}:start`);
  transcriptionJob.setState((state) => ({ ...state, running: true, file: audio, error: null }));
  const finishActivity = beginAppActivity(mode === 'accurate' ? 'transcription' : 'dictation');
  let outcome: TranscriptionOutcome = 'failed';
  try {
    const ready = await apiJson<{ ready: boolean }>(
      mode === 'accurate' ? '/dictation/readiness?purpose=transcribe' : '/dictation/readiness',
      { signal: ctl.signal },
    );
    if (!ready.ready) {
      outcome = 'not-ready';
      return outcome;
    }
    const body = new FormData();
    body.set('audio', audio);
    body.set('mode', mode);
    // Speaker identification needs the segment timings only accurate mode produces.
    if (options.diarize && mode === 'accurate') body.set('diarize', 'true');
    const refinement = await apiJson<{ auto: boolean }>('/api/settings/dictation-refinement', {
      signal: ctl.signal,
    }).catch(() => ({ auto: false }));
    if (ctl.signal.aborted) return (outcome = 'cancelled');
    body.set('refine', String(refinement.auto));
    const result = await apiJson<Partial<TranscriptEntry> & { diarization_error?: string }>('/transcribe', {
      method: 'POST',
      body,
      signal: ctl.signal,
    });
    if (ctl.signal.aborted) return (outcome = 'cancelled');
    const saved = addTranscription(result);
    if (result.diarization_error) toast.warning(i18next.t('transcriptions.speakers_unavailable'));
    transcriptionJob.setState((state) => ({ ...state, savedId: saved.id }));
    outcome = 'saved';
    if (!viewing) {
      toast.success(i18next.t('transcriptions.title'), {
        description: audio.name,
        action: { label: i18next.t('common.open'), onClick: openTranscriptions },
      });
    }
    return outcome;
  } catch (error) {
    if (ctl.signal.aborted) return (outcome = 'cancelled');
    const message = describeError(error);
    transcriptionJob.setState((state) => ({ ...state, error: message }));
    if (!viewing) {
      toast.error(i18next.t('transcriptions.failed'), {
        description: message,
        action: { label: i18next.t('common.open'), onClick: openTranscriptions },
      });
    }
    return outcome;
  } finally {
    recordActionBreadcrumb(
      `transcribe:${mode}:${outcome === 'saved' ? 'complete' : outcome === 'cancelled' ? 'cancel' : 'error'}`,
    );
    finishActivity();
    controller = null;
    transcriptionJob.setState((state) => ({ ...state, running: false }));
  }
}
