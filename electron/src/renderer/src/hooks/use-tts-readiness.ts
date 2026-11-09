import {
  useModelCatalogue,
  type ModelCatalogueResponse,
} from '@/features/settings/model-catalogue-query';
import { engineFamilyState, useEngines } from './use-engines';
import { useBackendStatus } from './use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';

export type TtsReadinessBlocker = 'engine' | 'loading' | 'cloning' | null;

/** Resolve the selected TTS engine and explicit local weights without loading either. */
export function useTtsReadiness(
  operation?: 'tts' | 'longform' | 'audiobook' | 'batch' | 'dub',
): Exclude<TtsReadinessBlocker, 'cloning'>;
export function useTtsReadiness(operation: string): TtsReadinessBlocker;
export function useTtsReadiness(operation = 'tts'): TtsReadinessBlocker {
  const backend = useBackendStatus();
  const engines = useEngines();
  const tts = engineFamilyState(engines.data, 'tts');
  const activeEngine = tts?.active ?? undefined;
  const activeModelId = tts?.active_model;
  // The engines query polls while the engine is unavailable; the model half of
  // this gate must too, or a download finished elsewhere (Settings, another
  // window, the CLI) leaves the "not ready" notice up until a remount.
  const catalogue = useModelCatalogue({
    pollWhile: (data) => Boolean(activeModelId) && modelMissing(data, activeModelId),
  });
  // A live-but-busy backend (#2430) is not a loading state: it is mid-job, so
  // the engine answers as soon as that job releases the event loop. Blocking
  // generation here is what left the workspace visible but unusable.
  if (!isBackendReachable(backend.stage)) return 'loading';
  if (engines.isLoading) return 'loading';
  if (engines.isError || !engines.data || !activeEngine) return 'engine';
  if (!engines.activeTtsReady) return 'engine';
  const activeBackend = tts?.backends.find((item) => item.id === activeEngine);
  // A preset-only engine (KittenTTS, Supertonic-3) still speaks on the Clone
  // page: it renders the chosen preset voice instead of a cloned one.
  if (
    operation === 'clone' &&
    activeBackend?.supports_cloning === false &&
    !hasPresets(activeBackend)
  )
    return 'cloning';
  if (activeModelId && catalogue.isLoading) return 'loading';
  return modelMissing(catalogue.data, activeModelId) ? 'engine' : null;
}

/** The catalogue lists this model and it is not fully on disk. */
function modelMissing(
  data: ModelCatalogueResponse | undefined,
  repoId: string | null | undefined,
): boolean {
  const model = repoId ? data?.models?.find((item) => item.repo_id === repoId) : undefined;
  return Boolean(model && (!model.installed || model.incomplete));
}

/** Speaks with its own preset voices and cannot clone: the Clone page renders a preset instead. */
export function isPresetOnly(
  backend:
    | { supports_cloning?: boolean | null; preset_voices?: string[] | null }
    | null
    | undefined,
): boolean {
  return backend?.supports_cloning === false && hasPresets(backend);
}

function hasPresets(backend: { preset_voices?: string[] | null } | null | undefined): boolean {
  return Boolean(backend?.preset_voices?.length);
}
