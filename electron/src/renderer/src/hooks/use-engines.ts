import { useQuery } from '@tanstack/react-query';
import { getEngines } from '@/lib/api/engines';
import type { EngineBackend, EngineFamilyState, EnginesResponse } from '@/lib/api/types';
import { queryKeys } from '@/lib/query';
import { useBackendStatus } from './use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';

const ENGINES_STALE_MS = 30_000;
// While the selected TTS engine is unusable, poll: the user may be installing
// it in Settings and the composer should unlock without a restart.
const ENGINES_POLL_WHILE_UNREADY_MS = 15_000;

export interface UseEnginesResult {
  data?: EnginesResponse;
  activeTts: EngineBackend | null;
  activeTtsReady: boolean;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  retry: () => void;
}

export function engineFamilyState(
  data: EnginesResponse | undefined,
  family: keyof EnginesResponse,
): EngineFamilyState | undefined {
  const state = (data as Partial<EnginesResponse> | undefined)?.[family];
  return state && Array.isArray(state.backends) ? state : undefined;
}

export function activeTtsBackend(data: EnginesResponse | undefined): EngineBackend | null {
  const tts = engineFamilyState(data, 'tts');
  if (!tts) return null;
  const active = tts.active;
  return (active && tts.backends.find((backend) => backend.id === active)) || null;
}

export function activeTtsReady(data: EnginesResponse | undefined): boolean {
  return activeTtsBackend(data)?.available === true;
}

export function useEngines(): UseEnginesResult {
  const status = useBackendStatus();
  const query = useQuery({
    queryKey: queryKeys.engines,
    queryFn: getEngines,
    staleTime: ENGINES_STALE_MS,
    enabled: isBackendReachable(status.stage),
    refetchInterval: (query) =>
      activeTtsReady(query.state.data) ? false : ENGINES_POLL_WHILE_UNREADY_MS,
  });
  return {
    data: query.data,
    activeTts: activeTtsBackend(query.data),
    activeTtsReady: activeTtsReady(query.data),
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    retry: () => {
      void query.refetch();
    },
  };
}
