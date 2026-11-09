import type { EnginesResponse } from '@/lib/api/types';

export interface SidebarModelStatus {
  status: 'idle' | 'loading' | 'ready';
  checkpoint?: string | null;
  loaded_at?: string | null;
  sub_stage?: string | null;
  detail?: string | null;
  error?: string | null;
}

export type RuntimeHealth = 'checking' | 'unavailable' | 'loading' | 'ready';

export function sidebarToolState(state: string, online: boolean) {
  if (!online) return 'modelMaintenance.offline';
  if (state === 'engineRuntime.idle') return 'modelSettings.available';
  if (state === 'modelSettings.unavailable') return 'sidebarTools.checkSetup';
  return state;
}

export function resolveRuntimeHealth(
  engines: EnginesResponse | undefined,
  enginesLoading: boolean,
  enginesError: boolean,
  model: SidebarModelStatus | undefined,
  modelLoading = false,
  modelError = false,
): RuntimeHealth {
  if (enginesLoading && !engines) return 'checking';
  if (enginesError || !engines) return 'unavailable';
  const tts = (engines as Partial<EnginesResponse>).tts;
  if (!tts || !Array.isArray(tts.backends)) return 'unavailable';
  const selected = tts.backends.find((engine) => engine.id === tts.active);
  if (!selected?.available) return 'unavailable';
  if (selected.id === 'omnivoice' && modelError) return 'unavailable';
  if (selected.id === 'omnivoice' && modelLoading) return 'checking';
  if (selected.id === 'omnivoice' && model?.status === 'loading') return 'loading';
  if (selected.id === 'omnivoice' && (model?.sub_stage === 'error' || model?.error))
    return 'unavailable';
  return 'ready';
}

/** Loaded-model rows that hold a speech-to-text model in memory. */
const RESIDENT_ASR_IDS = new Set(['asr', 'capture-asr', 'transcription-asr']);

export function isResidentAsrModel(id: string) {
  return RESIDENT_ASR_IDS.has(id);
}
