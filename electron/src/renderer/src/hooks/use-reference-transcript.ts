import { useEffect, useRef, useState } from 'react';
import { apiJson, ApiError } from '@/lib/api/client';
import { cloneSettingsStore, setCloneSetting } from '@/lib/store/clone-settings';
import { beginAppActivity } from '@/lib/app-activity';

/**
 * The existing capture endpoint preflights installed models and honors the selected ASR engine.
 *
 * `skip`: the active engine keeps only part of this clip and picks that part
 * itself (#2281). A whole-clip transcript would not match what it keeps — and
 * OmniVoice rejects one outright — so leave the transcript to the engine.
 * When `skip` turns true after this hook already filled the transcript (the
 * engine list loaded late, or the user switched engines), that machine
 * transcript is cleared; a transcript the user edited is kept.
 */
export function useReferenceTranscript(file: File | null, { skip = false } = {}) {
  const [state, setState] = useState<'idle' | 'busy' | 'ready' | 'unavailable' | 'failed'>('idle');
  const [attempt, setAttempt] = useState(0);
  // The transcript this hook last wrote, so it can be withdrawn when `skip`
  // turns true without touching anything the user typed.
  const autoFilled = useRef<string | null>(null);
  useEffect(() => {
    if (skip && autoFilled.current !== null) {
      if (cloneSettingsStore.state.refText === autoFilled.current) setCloneSetting('refText', '');
      autoFilled.current = null;
    }
    if (!file || skip) {
      setState('idle');
      return;
    }
    const controller = new AbortController();
    const finishActivity = beginAppActivity('transcription');
    const before = cloneSettingsStore.state.refText;
    setState('busy');
    void (async () => {
      try {
        const body = new FormData();
        body.set('audio', file);
        body.set('mode', 'reference');
        body.set('refine', 'false');
        const result = await apiJson<{ text: string }>('/transcribe', {
          method: 'POST',
          body,
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        if (
          !cloneSettingsStore.state.selectedProfileId &&
          cloneSettingsStore.state.refText === before
        ) {
          const text = result.text.trim();
          setCloneSetting('refText', text);
          autoFilled.current = text;
        }
        setState('ready');
      } catch (error) {
        if (!controller.signal.aborted) {
          const detail = error instanceof ApiError ? error.payload?.detail : null;
          const missing =
            detail &&
            typeof detail === 'object' &&
            'error' in detail &&
            detail.error === 'asr_model_missing' &&
            !('reason' in detail && detail.reason === 'verification_failed');
          setState(missing ? 'unavailable' : 'failed');
        }
      } finally {
        finishActivity();
      }
    })();
    return () => {
      controller.abort();
      finishActivity();
    };
  }, [file, attempt, skip]);
  return { state, retry: () => setAttempt((value) => value + 1) };
}
