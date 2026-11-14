import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import { tr } from '@/lib/i18n-text';

export interface ModelInstallJob {
  repo_id: string;
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

const OPTIONAL_TEXT_FIELDS = ['error', 'docs_topic', 'phase'] as const;
const OPTIONAL_NUMBER_FIELDS = [
  'bytes_done',
  'total_bytes',
  'rate',
  'eta_seconds',
  'retry_after_seconds',
  'failed_at',
  'files_done',
  'files_total',
] as const;

function isModelInstallJobsResponse(value: unknown): value is ModelInstallJobsResponse {
  if (!value || typeof value !== 'object' || !('jobs' in value) || !Array.isArray(value.jobs))
    return false;
  return value.jobs.every((job: unknown) => {
    if (!job || typeof job !== 'object') return false;
    const row = job as Record<string, unknown>;
    return (
      typeof row.repo_id === 'string' &&
      row.repo_id.trim().length > 0 &&
      typeof row.state === 'string' &&
      row.state.trim().length > 0 &&
      OPTIONAL_TEXT_FIELDS.every((field) => row[field] == null || typeof row[field] === 'string') &&
      OPTIONAL_NUMBER_FIELDS.every(
        (field) =>
          row[field] == null ||
          (typeof row[field] === 'number' && Number.isFinite(row[field]) && row[field] >= 0),
      )
    );
  });
}

function checkedModelInstallJobs<T extends ModelInstallJob>(
  value: unknown,
): ModelInstallJobsResponse<T> {
  if (!isModelInstallJobsResponse(value)) throw new Error(tr('failureCause.bad_response.title'));
  return value as ModelInstallJobsResponse<T>;
}

export function modelInstallPollInterval(query: {
  state: { data?: ModelInstallJobsResponse };
}): number | false {
  return isModelInstallJobsResponse(query.state.data) &&
    query.state.data.jobs.some((job) => !TERMINAL_MODEL_INSTALL_STATES.has(job.state))
    ? 2_000
    : false;
}

export function useModelInstallJobs<T extends ModelInstallJob = ModelInstallJob>() {
  const backend = useBackendStatus();
  return useQuery({
    queryKey: ['model-install-jobs'],
    queryFn: async () =>
      checkedModelInstallJobs<T>(await apiJson<unknown>('/models/install/status')),
    // Validate pre-existing shared cache entries too, before any consumer sees them.
    select: checkedModelInstallJobs<T>,
    enabled: isBackendReachable(backend.stage),
    refetchInterval: modelInstallPollInterval,
  });
}

export function useModelInstallCompletionRefresh(jobs: ModelInstallJobsResponse | undefined): void {
  const client = useQueryClient();
  const previous = useRef<Set<string>>(new Set());
  const signature = !isModelInstallJobsResponse(jobs)
    ? null
    : [
        ...new Set(
          jobs.jobs
            .filter((job) => !TERMINAL_MODEL_INSTALL_STATES.has(job.state))
            .map((job) => job.repo_id),
        ),
      ]
        .sort()
        .join('\u0001');

  useEffect(() => {
    // A failed/malformed poll cannot prove that an active install disappeared.
    if (signature === null) return;
    const active = new Set(signature ? signature.split('\u0001') : []);
    const completed = [...previous.current].some((job) => !active.has(job));
    previous.current = active;
    if (!completed) return;
    void Promise.all(
      [
        'model-catalogue',
        'model-recommendations',
        'engines',
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

/** Keep every mounted workflow in sync when a local model install finishes. */
export function ModelInstallSync() {
  const jobs = useModelInstallJobs();
  useModelInstallCompletionRefresh(jobs.data);
  return null;
}
