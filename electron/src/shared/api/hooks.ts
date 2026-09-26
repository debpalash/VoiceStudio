// ── TanStack Query hooks ─────────────────────────────────────────────────
// Central place for all query/mutation hooks. Components import from here
// instead of calling api/* + useEffect + useState manually.
// Deduplication is automatic — two components using useSysinfo() share one
// network request and one cache entry.

import { useMemo } from 'react';
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { useAppStore } from '../store';
import * as systemApi from './system';
import * as setupApi from './setup';
import * as galleryApi from './gallery';
import * as archetypesApi from './archetypes';
import type { ArchetypeFilters } from './archetypes';
import * as communityApi from './community';
import type { CommunityFilters } from './community';
import * as enginesApi from './engines';
import type { AllEnginesResponse, EngineFamily } from './types';

// ── Keys (prevents typos, enables targeted invalidation) ─────────────────
export const queryKeys = {
  sysinfo: ['sysinfo'] as const,
  modelStatus: ['model-status'] as const,
  notifications: ['notifications'] as const,
  systemInfo: ['system-info'] as const,
  systemLogs: (tail?: number) => ['system-logs', tail ?? 300] as const,
  tauriLogs: (tail?: number) => ['tauri-logs', tail ?? 300] as const,
  models: ['models'] as const,
  recommendations: ['recommendations'] as const,
  preflight: ['preflight'] as const,
  engines: ['engines'] as const,
  setupStatus: ['setup-status'] as const,
  galleryVoices: (params?: any) => ['gallery-voices', params] as const,
  galleryCategories: ['gallery-categories'] as const,
  archetypeCategories: ['archetype-categories'] as const,
  archetypes: (filters?: any) => ['archetypes', filters] as const,
  communityItems: (filters?: any) => ['community-items', filters] as const,
  communityManifest: (refresh?: boolean) => ['community-manifest', !!refresh] as const,
};

// ── Polling queries (sysinfo, model status, logs) ────────────────────────

export function useSysinfo(enabled = true) {
  return useQuery({
    queryKey: queryKeys.sysinfo,
    queryFn: systemApi.sysinfo,
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
    retry: Infinity,
    retryDelay: 1_500,
    enabled,
  });
}

export function useModelStatus(enabled = true) {
  return useQuery({
    queryKey: queryKeys.modelStatus,
    queryFn: systemApi.modelStatus,
    // Poll every 2s while model is loading for near-real-time sub-stage
    // updates in the floating pill; 10s when idle/ready to save bandwidth.
    refetchInterval: (query) => {
      const status = query.state?.data?.status;
      return status === 'loading' ? 2_000 : 10_000;
    },
    refetchIntervalInBackground: false,
    retry: Infinity,
    retryDelay: 1_500,
    enabled,
  });
}

// Shared by the header bell (NotificationPanel) and the LogsFooter
// notifications tab — one request, one cache entry.
export function useNotifications(enabled = true) {
  return useQuery({
    queryKey: queryKeys.notifications,
    queryFn: systemApi.systemNotifications,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    retry: Infinity,
    retryDelay: 1_500,
    enabled,
  });
}

/** A system notification the user may hide: info/warn describe standing facts
 * (CPU-only box, low disk) that can be acknowledged; errors describe broken
 * things that stay visible until actually fixed. */
export function isDismissibleNotification(n: { level?: string }): boolean {
  return n?.level === 'info' || n?.level === 'warn';
}

// The bell and the footer tab must agree on what is visible, so the
// dismissed-ids filter lives here, next to the query they share. Dismissals
// persist in the app store (prefsSlice.dismissedNotificationIds); the backend
// keeps re-emitting the note every poll, which is exactly why filtering is
// client-side — see the slice doc for the id-stability contract.
export function useVisibleNotifications(enabled = true) {
  const query = useNotifications(enabled);
  const dismissed = useAppStore((s) => s.dismissedNotificationIds);
  const all = query.data?.notifications;
  // Memoized so the array reference is stable across re-renders while the
  // inputs are unchanged (downstream effect/memo deps stay quiet).
  // A note is hidden only while it is *currently* dismissible: if a stable id
  // ever escalates to error level, an old info/warn dismissal must not hide it.
  const notifications = useMemo(
    () =>
      (all || []).filter(
        (n: { id?: string; level?: string }) =>
          !n.id || !isDismissibleNotification(n) || !dismissed.includes(n.id),
      ),
    [all, dismissed],
  );
  // Omit `data` from the returned query: `data.notifications` is the raw
  // UNFILTERED list, and any consumer reaching for it would silently bypass
  // the dismiss filter. (No consumer uses `data` today.)
  const { data: _data, ...rest } = query;
  return { ...rest, notifications };
}

export function useSystemLogs(tail = 300, enabled = true, refetchInterval = 10_000) {
  return useQuery({
    queryKey: queryKeys.systemLogs(tail),
    queryFn: () => systemApi.systemLogs(tail),
    refetchInterval,
    refetchIntervalInBackground: false,
    enabled,
  });
}

export function useTauriLogs(tail = 300, enabled = true, refetchInterval = 10_000) {
  return useQuery({
    queryKey: queryKeys.tauriLogs(tail),
    queryFn: () => systemApi.systemLogsTauri(tail),
    refetchInterval,
    refetchIntervalInBackground: false,
    enabled,
  });
}

// ── One-shot queries ─────────────────────────────────────────────────────

export function useSystemInfo() {
  return useQuery({
    queryKey: queryKeys.systemInfo,
    queryFn: systemApi.systemInfo,
    staleTime: 60_000,
    retry: Infinity,
    retryDelay: 2_000,
  });
}

export function useModels() {
  return useQuery({
    queryKey: queryKeys.models,
    queryFn: setupApi.listModels,
    staleTime: 30_000,
  });
}

export function useRecommendations() {
  return useQuery({
    queryKey: queryKeys.recommendations,
    queryFn: setupApi.getRecommendations,
    staleTime: 30_000,
  });
}

export function usePreflight() {
  return useQuery({
    queryKey: queryKeys.preflight,
    queryFn: setupApi.preflight,
    staleTime: 60_000,
  });
}

/**
 * The app-wide engine inventory.  Engine selection affects more than the
 * catalogue (for example Audiobook's expressive controls), so every consumer
 * must share this cache rather than take its own one-off snapshot.
 *
 * `queryFn` is injectable for the compatibility-matrix test seam.
 */
export function useEngines(queryFn: () => Promise<AllEnginesResponse> = enginesApi.listEngines) {
  return useQuery({
    queryKey: queryKeys.engines,
    queryFn,
    staleTime: 30_000,
    retry: 1,
  });
}

/** Select an engine and invalidate every view derived from `/engines`. */
export function useSelectEngine() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      family,
      backendId,
      modelId,
    }: {
      family: EngineFamily;
      backendId: string;
      modelId?: string;
    }) => enginesApi.selectEngine(family, backendId, modelId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.engines }),
  });
}

export function useSetupStatus() {
  return useQuery({
    queryKey: queryKeys.setupStatus,
    queryFn: setupApi.setupStatus,
    staleTime: 10_000,
  });
}

export function useGalleryVoices(params?: any) {
  return useQuery({
    queryKey: queryKeys.galleryVoices(params),
    queryFn: () => galleryApi.listGalleryVoices(params),
    staleTime: 30_000,
  });
}

// ── Archetype gallery (designed voices) ──────────────────────────────────
// The catalog is large and static, so cache it hard.
export function useArchetypeCategories() {
  return useQuery({
    queryKey: queryKeys.archetypeCategories,
    queryFn: archetypesApi.listArchetypeCategories,
    staleTime: 5 * 60_000,
  });
}

// `enabled` lets a shared consumer (VoiceSelector) defer the fetch until its
// dropdown actually opens — many pickers can mount (e.g. the Audiobook cast
// list) without any of them hitting the network until used.
export function useArchetypes(filters: ArchetypeFilters = {}, enabled = true) {
  return useQuery({
    queryKey: queryKeys.archetypes(filters),
    queryFn: () => archetypesApi.listArchetypes(filters),
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData, // v5: keep prior page visible while paginating
    enabled,
  });
}

// ── Community gallery (marketplace) ───────────────────────────────────────
export function useCommunityItems(filters: CommunityFilters = {}) {
  return useQuery({
    queryKey: queryKeys.communityItems(filters),
    queryFn: () => communityApi.listCommunityItems(filters),
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  });
}

// ── Mutations ────────────────────────────────────────────────────────────

export function useInstallModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repo_id: string) => setupApi.installModel(repo_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.models });
      qc.invalidateQueries({ queryKey: queryKeys.setupStatus });
      qc.invalidateQueries({ queryKey: queryKeys.recommendations });
      // Some model installs make an engine selectable; refresh every engine
      // indicator rather than leaving a stale unavailable snapshot behind.
      qc.invalidateQueries({ queryKey: queryKeys.engines });
    },
  });
}

export function useDeleteModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repo_id: string) => setupApi.deleteModel(repo_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.models });
      qc.invalidateQueries({ queryKey: queryKeys.setupStatus });
      qc.invalidateQueries({ queryKey: queryKeys.recommendations });
      qc.invalidateQueries({ queryKey: queryKeys.engines });
    },
  });
}
