import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJson, describeError } from '@/lib/api/client';
import { useResolvedLicenceError } from '@/hooks/use-resolved-licence-error';
import {
  engineSelectionFeedback,
  type EngineSelectionResult,
} from '@/lib/engine-selection-feedback';

/** Toast the backend's routing verdict for a selection (warns on CPU fallback). */
export function useSelectionToast() {
  const { t } = useTranslation();
  return (result: EngineSelectionResult, family: string) => {
    const feedback = engineSelectionFeedback(result, family);
    if (feedback.tone === 'warning') toast.warning(t(feedback.key, feedback.values));
    else toast.success(t(feedback.key, feedback.values));
  };
}

/**
 * Run one selection request at a time and refresh what it changes. `request`
 * returns the backend's selection result for the routing toast.
 */
export function useSelection(family: string, invalidate: readonly string[]) {
  const client = useQueryClient();
  const notify = useSelectionToast();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ message: string; error: unknown } | null>(null);
  const clearFailure = useCallback(() => setFailure(null), []);
  useResolvedLicenceError(failure?.error, clearFailure);
  const run = async (request: () => Promise<EngineSelectionResult>) => {
    setBusy(true);
    setFailure(null);
    try {
      const result = await request();
      await Promise.all(invalidate.map((key) => client.invalidateQueries({ queryKey: [key] })));
      notify(result, family);
    } catch (error) {
      setFailure({ message: describeError(error), error });
    } finally {
      setBusy(false);
    }
  };
  return { busy, failed: failure?.message ?? null, clearFailure, run };
}

const ENGINE_KEYS = [
  'engines',
  'loaded-models',
  'sidebar-model-status',
  'performance-profile',
] as const;
const LLM_KEYS = ['llm-skills', 'translation-engines', 'dictation-refinement'] as const;

/** Select a TTS/ASR/LLM backend, optionally with one of its models. */
export function useEngineSelect(family: 'tts' | 'asr' | 'llm') {
  const selection = useSelection(family, [...ENGINE_KEYS, ...(family === 'llm' ? LLM_KEYS : [])]);
  return {
    ...selection,
    select: (backendId: string, modelId?: string) =>
      selection.run(() =>
        apiJson<EngineSelectionResult>('/engines/select', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            family,
            backend_id: backendId,
            ...(modelId ? { model_id: modelId } : {}),
          }),
        }),
      ),
  };
}
