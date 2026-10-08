import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { useBackendStatus } from './use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import { IDLE_STATUS_POLL_MS, relaxWhenBackendBusy } from '@/lib/status-polling';

export const performanceTiers = ['fast', 'balanced', 'quality', 'max'] as const;
export type PerformanceTier = (typeof performanceTiers)[number];
export type PerformanceChoice = PerformanceTier | 'auto';
export type PerformanceFamily = 'tts' | 'asr' | 'dictation' | 'diarisation' | 'translation' | 'llm';
export interface PerformanceProfileState {
  global: PerformanceChoice;
  overrides: Partial<Record<PerformanceFamily, PerformanceTier>>;
  effective: Record<PerformanceFamily, PerformanceTier>;
  families: PerformanceFamily[];
  implemented_families: PerformanceFamily[];
  applicable_families?: PerformanceFamily[];
  /** TTS engines whose sampling the tiers tune (backend-owned list). */
  tts_tiered_engines?: string[];
  targets: Record<
    PerformanceFamily,
    {
      steps?: number;
      postprocess?: boolean;
      mode?: string;
      engine?: string;
      model?: string;
      models?: string[];
      model_policy?: string;
      beam_size?: number;
      best_of?: number;
      num_beams?: number;
      decoding_method?: string;
      max_active_paths?: number;
    }
  >;
  selections: Record<
    PerformanceFamily,
    {
      engine: string;
      model: string | null;
      label?: string | null;
    }
  >;
  capacity_activations?: Partial<Record<PerformanceFamily, { engine?: string; model?: string }>>;
  runtime_activations?: Partial<Record<PerformanceFamily, { engine?: string; model?: string }>>;
  plan?: {
    resolved: PerformanceTier;
    status: 'fits' | 'adjusted' | 'unknown' | 'limited';
    max_status: 'fits' | 'adjusted' | 'unknown' | 'limited';
    hardware: {
      device: string;
      ram_gb: number | null;
      vram_gb: number | null;
      cpu_threads: number;
    };
    families: Record<
      PerformanceFamily,
      {
        tier: PerformanceTier;
        reason: 'fits' | 'memory' | 'installed' | 'kept' | 'unknown';
        selection: { engine: string; model: string; label?: string | null } | null;
      }
    >;
  };
}

/** Preference persistence only; runtime application must report its own result. */
export function usePerformanceProfile() {
  const backend = useBackendStatus();
  const client = useQueryClient();
  const saving = useIsMutating({ mutationKey: ['performance-profile'] }) > 0;
  const query = useQuery({
    queryKey: ['performance-profile'],
    enabled: isBackendReachable(backend.stage),
    staleTime: 30_000,
    refetchInterval: () => relaxWhenBackendBusy(IDLE_STATUS_POLL_MS),
    queryFn: () => apiJson<PerformanceProfileState>('/api/settings/performance-profile'),
  });
  const mutation = useMutation({
    mutationKey: ['performance-profile'],
    scope: { id: 'performance-profile' },
    mutationFn: async ({
      tier,
      family,
    }: {
      tier: PerformanceChoice;
      family: PerformanceFamily | null;
    }) => {
      // Persist first. The old path waited for the relatively expensive engine
      // registry probe before sending this request, so the control appeared to
      // do nothing for several seconds on a cold model catalogue.
      const state = await apiJson<PerformanceProfileState>('/api/settings/performance-profile', {
        method: 'PUT',
        body: JSON.stringify({ tier, family }),
      });
      const target = state.targets.tts;
      if (
        // Backends before 0.5.7 don't report the list; they tier only OmniVoice.
        (state.tts_tiered_engines ?? ['omnivoice', 'omnivoice-subprocess']).includes(state.selections.tts.engine) &&
        typeof target.steps === 'number' &&
        typeof target.postprocess === 'boolean'
      ) {
        // The backend owns the effective preset. Mirroring it into the clone
        // draft only keeps its visible override controls in sync; a chunk-load
        // failure must not turn an already-applied backend change into a failed
        // mutation.
        await import('@/lib/store/clone-settings')
          .then(({ patchCloneSettings }) =>
            patchCloneSettings({ steps: target.steps!, postprocess: target.postprocess! }),
          )
          .catch(() => {});
      }
      // Dubbing omits unset steps so the backend applies this preset. Keep any
      // explicit production override, including saved project settings, intact.
      return state;
    },
    onSuccess: (state) => client.setQueryData(['performance-profile'], state),
    onSettled: () => {
      // Refresh dependent displays in the background. Returning this promise
      // would make mutateAsync (and its applied feedback) wait for every cold
      // catalogue/status query even though the preference is already saved.
      void Promise.all([
        client.invalidateQueries({ queryKey: ['engines'] }),
        client.invalidateQueries({ queryKey: ['sidebar-model-status'] }),
        client.invalidateQueries({ queryKey: ['loaded-models'] }),
        client.invalidateQueries({ queryKey: ['translation-engines'] }),
        client.invalidateQueries({ queryKey: ['settings-dictation'] }),
        client.invalidateQueries({ queryKey: ['sidebar-dictation'] }),
        client.invalidateQueries({ queryKey: ['diarisation-status'] }),
      ]);
    },
  });
  return { ...query, setTier: mutation.mutateAsync, isSaving: saving || mutation.isPending };
}
