import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';

export interface ModelInstallJob {
  repo_id: string;
  target?: string;
  state: string;
  bytes_done?: number;
  total_bytes?: number;
  error?: string;
}

export interface ModelInstallJobsResponse<T extends ModelInstallJob = ModelInstallJob> {
  jobs: T[];
}

export const TERMINAL_MODEL_INSTALL_STATES = new Set([
  'done',
  'failed',
  'cancelled',
  'install_cancelled',
]);

export const modelInstallJobTarget = (job: ModelInstallJob) => job.target || 'local';

export function modelInstallPollInterval(query: {
  state: { data?: ModelInstallJobsResponse };
}): number | false {
  return query.state.data?.jobs.some((job) => !TERMINAL_MODEL_INSTALL_STATES.has(job.state))
    ? 2_000
    : false;
}

export function useModelInstallJobs<T extends ModelInstallJob = ModelInstallJob>() {
  const backend = useBackendStatus();
  return useQuery({
    queryKey: ['model-install-jobs'],
    queryFn: () => apiJson<ModelInstallJobsResponse<T>>('/models/install/status'),
    enabled: isBackendReachable(backend.stage),
    refetchInterval: modelInstallPollInterval,
  });
}

export function useModelInstallCompletionRefresh(jobs: ModelInstallJobsResponse | undefined): void {
  const client = useQueryClient();
  const previous = useRef<Set<string>>(new Set());
  const signature = [
    ...new Set(
      jobs?.jobs
        .filter((job) => !TERMINAL_MODEL_INSTALL_STATES.has(job.state))
        .map((job) => `${modelInstallJobTarget(job)}\u0000${job.repo_id}`) ?? [],
    ),
  ]
    .sort()
    .join('\u0001');

  useEffect(() => {
    const active = new Set(signature ? signature.split('\u0001') : []);
    const completed = [...previous.current].some((job) => !active.has(job));
    previous.current = active;
    if (!completed) return;
    void Promise.all(
      [
        'model-catalogue',
        'model-recommendations',
        'engines',
        'workers',
        'loaded-models',
        'translation-engines',
        'setup-status',
        'settings-dictation',
        'sidebar-dictation',
        'diarisation-status',
        'sidebar-diarisation',
        'performance-profile',
      ].map((key) => client.invalidateQueries({ queryKey: [key] })),
    ).catch(() => {
      // Query invalidation is opportunistic; mounted queries also poll and
      // realtime model events refresh them after a transient cache failure.
    });
  }, [client, signature]);
}

/** Keep every mounted workflow in sync when a local or remote model install finishes. */
export function ModelInstallSync() {
  const jobs = useModelInstallJobs();
  useModelInstallCompletionRefresh(jobs.data);
  return null;
}
