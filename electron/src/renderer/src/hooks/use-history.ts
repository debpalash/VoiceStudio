import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { toast } from 'sonner';
import { describeError } from '@/lib/api/client';
import {
  clearHistory,
  deleteHistoryItem,
  listHistory,
  setHistoryStarred,
  type StarredResponse,
} from '@/lib/api/history';
import type { HistoryItem } from '@/lib/api/types';
import { tr } from '@/lib/i18n-text';
import { queryKeys } from '@/lib/query';
import { useBackendStatus } from './use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';

const HISTORY_STALE_MS = 10_000;

export function useHistory(): UseQueryResult<HistoryItem[]> {
  const status = useBackendStatus();
  return useQuery({
    queryKey: queryKeys.history,
    queryFn: listHistory,
    staleTime: HISTORY_STALE_MS,
    enabled: isBackendReachable(status.stage),
  });
}

export function useDeleteHistoryItem(): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteHistoryItem,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.history }),
    onError: (err) => {
      toast.error(tr('clone.history_delete_failed', { message: describeError(err) }));
    },
  });
}

export function useClearHistory(): UseMutationResult<void, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: clearHistory,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.history }),
    onError: (err) => {
      toast.error(tr('clone.history_clear_failed', { message: describeError(err) }));
    },
  });
}

export interface ToggleStarredInput {
  id: string;
  starred: boolean;
}

export function useToggleStarred(): UseMutationResult<StarredResponse, Error, ToggleStarredInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, starred }: ToggleStarredInput) => setHistoryStarred(id, starred),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.history }),
    onError: (err) => {
      toast.error(tr('clone.history_star_failed', { message: describeError(err) }));
    },
  });
}
