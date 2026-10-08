import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
  type QueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { toast } from 'sonner';
import { describeError } from '@/lib/api/client';
import {
  createCloneProfile,
  deleteProfile,
  listProfiles,
  replaceProfileAudio,
  type CreateCloneProfileInput,
  type ReplaceProfileAudioInput,
} from '@/lib/api/profiles';
import type { Profile } from '@/lib/api/types';
import { tr } from '@/lib/i18n-text';
import { queryKeys } from '@/lib/query';
import {
  cloneSettingsStore,
  patchCloneSettings,
  setCloneSetting,
} from '@/lib/store/clone-settings';
import { readDraft, writeDraft } from '@/features/design/design-draft';
import { useBackendStatus } from './use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';

const PROFILES_STALE_MS = 30_000;
const DELETION_CONFIRMATION_TIMEOUT_MS = 30_000;
const deletionConfirmations = new WeakMap<QueryClient, Promise<void>>();

export function useProfiles(): UseQueryResult<Profile[]> {
  const status = useBackendStatus();
  return useQuery({
    queryKey: queryKeys.profiles,
    queryFn: listProfiles,
    staleTime: PROFILES_STALE_MS,
    enabled: isBackendReachable(status.stage),
  });
}

export function useCreateCloneProfile(): UseMutationResult<
  Profile,
  Error,
  CreateCloneProfileInput
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createCloneProfile,
    onSuccess: async (created) => {
      toast.success(tr('clone.saved_profile'));
      queryClient.setQueryData<Profile[]>(queryKeys.profiles, (old) => [
        created,
        ...(old ?? []).filter((p) => p.id !== created.id),
      ]);
      await queryClient.invalidateQueries({ queryKey: queryKeys.profiles });
    },
    onError: (err) => {
      toast.error(tr('clone.save_failed', { message: describeError(err) }));
    },
  });
}

/**
 * Replace a saved clone's reference clip. Errors are left to the caller (the
 * profile editor shows them inline and keeps the chosen clip for retry).
 */
export function useReplaceProfileAudio(): UseMutationResult<
  Profile,
  Error,
  ReplaceProfileAudioInput & { id: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...input }) => replaceProfileAudio(id, input),
    onSuccess: async (updated) => {
      queryClient.setQueryData<Profile[]>(queryKeys.profiles, (old) =>
        old?.map((profile) => (profile.id === updated.id ? updated : profile)),
      );
      // The composer's transcript belongs to the selected voice's clip; keep it
      // matched to the new reference instead of the replaced one.
      if (cloneSettingsStore.state.selectedProfileId === updated.id) {
        patchCloneSettings({ refText: updated.ref_text ?? '' });
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.profiles });
    },
  });
}

export function useDeleteProfile(): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient();
  const forgetProfile = (id: string) => {
    // The form must not keep pointing at a voice that no longer exists.
    if (cloneSettingsStore.state.selectedProfileId === id) {
      setCloneSetting('selectedProfileId', null);
    }
    const designDraft = readDraft();
    if (designDraft.profileId === id) {
      writeDraft({ ...designDraft, profileId: null });
    }
  };
  return useMutation({
    mutationFn: deleteProfile,
    onSuccess: (_result, id) => {
      forgetProfile(id);
      toast.success(tr('clone.profile_deleted'));
      void queryClient.invalidateQueries({ queryKey: queryKeys.profiles });
    },
    onError: (err, id) => {
      toast.error(tr('clone.delete_profile_failed', { message: describeError(err) }));
      // Let the deletion error settle even if confirmation is paused offline.
      // The managed profiles query resumes when the backend becomes ready.
      const originalQuery = queryClient
        .getQueryCache()
        .build(
          queryClient,
          queryClient.defaultQueryOptions({ queryKey: queryKeys.profiles as QueryKey }),
        );
      const previous = deletionConfirmations.get(queryClient) ?? Promise.resolve();
      // A later batch deletion must not cancel an earlier confirmation.
      const confirmation = previous.then(async () => {
        try {
          // Discard queued checks from a cleared/replaced query-client cache.
          if (
            queryClient.getQueryCache().find({ queryKey: queryKeys.profiles }) !== originalQuery
          ) {
            return;
          }
          // Asset cleanup can fail after the profile deletion has committed.
          // Confirm absence before clearing state; a rollback or unreachable
          // backend must preserve the user's selected voice.
          await queryClient.cancelQueries({ queryKey: queryKeys.profiles });
          let timer: ReturnType<typeof setTimeout> | undefined;
          const clearTimer = () => {
            if (timer !== undefined) clearTimeout(timer);
            timer = undefined;
          };
          const trackRequest = () => {
            if (originalQuery.state.fetchStatus !== 'fetching') {
              clearTimer();
            } else if (timer === undefined) {
              timer = setTimeout(() => {
                // Cancel this lifecycle only; a cleared cache may contain a
                // replacement profiles query. Unknown absence keeps selections.
                if (
                  queryClient.getQueryCache().find({ queryKey: queryKeys.profiles }) ===
                    originalQuery &&
                  originalQuery.state.fetchStatus === 'fetching'
                ) {
                  void originalQuery.cancel();
                }
              }, DELETION_CONFIRMATION_TIMEOUT_MS);
            }
          };
          const unsubscribe = queryClient.getQueryCache().subscribe((event) => {
            if (event.query === originalQuery) trackRequest();
          });
          try {
            // Bound stalled requests without expiring a check paused offline.
            const request = queryClient.fetchQuery({
              queryKey: queryKeys.profiles,
              queryFn: listProfiles,
              staleTime: 0,
            });
            // Joining an existing refresh emits no new fetching transition.
            trackRequest();
            const profiles = await request;
            if (!profiles.some((profile) => profile.id === id)) forgetProfile(id);
          } finally {
            unsubscribe();
            clearTimer();
          }
        } catch {
          // The original error is already shown; absence is still unconfirmed.
        }
      });
      deletionConfirmations.set(queryClient, confirmation);
      void confirmation.then(() => {
        if (deletionConfirmations.get(queryClient) === confirmation) {
          deletionConfirmations.delete(queryClient);
        }
      });
    },
  });
}
