import { useQuery } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { useBackendStatus } from './use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';

export interface DictationSelectionModel {
  id: string;
  label: string;
  installed: boolean;
}

export function useDictationSelection() {
  const status = useBackendStatus();
  return useQuery({
    queryKey: ['sidebar-dictation'],
    enabled: isBackendReachable(status.stage),
    staleTime: 30_000,
    queryFn: async () => {
      const [prefs, catalogue] = await Promise.all([
        apiJson<{ enabled: boolean; model_id: string }>('/dictation/prefs'),
        apiJson<{
          engine_available: boolean;
          models: DictationSelectionModel[];
        }>('/dictation/models'),
      ]);
      return {
        ...prefs,
        available: catalogue.engine_available,
        model: catalogue.models.find((model) => model.id === prefs.model_id),
      };
    },
  });
}
