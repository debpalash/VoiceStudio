import { useModelCatalogue } from '@/features/settings/model-catalogue-query';
import { resolveRemoteRuntime } from '@/components/app-shell/status-runtime';
import { useComputeRuntime, useComputeTarget } from './use-compute-target';
import { engineFamilyState, useEngines } from './use-engines';
import { useBackendStatus } from './use-backend-status';

export type TtsReadinessBlocker = 'engine' | 'loading' | 'cloning' | null;

/** Resolve the selected TTS engine and explicit local weights without loading either. */
export function useTtsReadiness(operation?: 'tts' | 'longform' | 'audiobook' | 'batch' | 'dub', requireLocal?: boolean): Exclude<TtsReadinessBlocker, 'cloning'>;
export function useTtsReadiness(operation: string, requireLocal?: boolean): TtsReadinessBlocker;
export function useTtsReadiness(operation = 'tts', requireLocal = false): TtsReadinessBlocker {
  const backend = useBackendStatus();
  const engines = useEngines();
  const catalogue = useModelCatalogue();
  // Resolve the target for this exact surface. If a chosen worker is offline
  // or does not support the operation, routing falls back to Local and the
  // local readiness checks below remain authoritative.
  const computeTarget = useComputeTarget(backend.stage === 'ready', operation);
  const remoteTarget = computeTarget.data?.active.remote
    ? computeTarget.data.active.worker_id
    : undefined;
  const tts = engineFamilyState(engines.data, 'tts');
  const activeEngine = tts?.active ?? undefined;
  const remoteRuntime = useComputeRuntime(
    remoteTarget,
    activeEngine,
    operation,
    backend.stage === 'ready' && Boolean(remoteTarget),
  );
  if (backend.stage !== 'ready') return 'loading';
  if (engines.isLoading) return 'loading';
  if (engines.isError || !engines.data || !activeEngine) return 'engine';
  if (computeTarget.isLoading) return 'loading';
  if (remoteTarget && !requireLocal) {
    const resolved = resolveRemoteRuntime(
      remoteRuntime.data,
      remoteRuntime.isPending,
      remoteRuntime.isError,
      false,
    );
    return resolved.state === 'checking'
      ? 'loading'
      : resolved.state === 'unavailable'
        ? 'engine'
        : null;
  }
  if (!engines.activeTtsReady) return 'engine';
  if (operation === 'clone' && tts?.backends.find((item) => item.id === activeEngine)?.supports_cloning === false)
    return 'cloning';
  const activeModelId = tts?.active_model;
  if (activeModelId && catalogue.isLoading) return 'loading';
  const activeModel = catalogue.data?.models.find((model) => model.repo_id === activeModelId);
  return activeModel && (!activeModel.installed || activeModel.incomplete) ? 'engine' : null;
}
