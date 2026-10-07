import { useQuery, type QueryClient } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import {
  ACTIVE_STATUS_POLL_MS,
  IDLE_STATUS_POLL_MS,
  computeTargetPollMs,
  relaxWhenBackendBusy,
} from '@/lib/status-polling';

export interface ComputeTarget {
  id: string;
  label: string;
  endpoint: string;
  connected: boolean;
  available: boolean;
  detail: string;
  is_local?: boolean;
  status: 'ready' | 'busy' | 'offline';
  latency_ms: number;
  active_tasks: number;
  max_tasks: number;
  cpu_percent: number | null;
  free_memory_bytes: number | null;
  system_memory_bytes: number;
  cpu_count: number;
  gpu_name: string;
  gpu_memory_bytes: number;
  gpu_utilization_percent: number | null;
}

export interface ComputeTargetState {
  target: string;
  op: string;
  active: {
    remote: boolean;
    worker_id?: string;
    label: string;
    reason: string;
  };
  remote_operations: string[];
  targets: ComputeTarget[];
}

export interface ComputeRuntimeCapability {
  engine: string;
  model_id: string;
  display_name?: string;
  repo_ids?: string[];
  backend?: string;
  supported: boolean;
  installed: boolean;
  downloaded: boolean;
  resident: boolean;
}

export interface ComputeRuntimeStatus {
  target: string;
  remote: boolean;
  label: string;
  reason: string;
  models: ComputeRuntimeCapability[];
}

export function useComputeTarget(enabled = true, op = '') {
  return useQuery({
    queryKey: ['workers', 'target', op],
    queryFn: ({ signal }) =>
      apiJson<ComputeTargetState>(`/workers/target${op ? `?op=${encodeURIComponent(op)}` : ''}`, {
        signal,
      }),
    enabled,
    refetchInterval: (query) =>
      computeTargetPollMs(
        query.state.data?.targets.find((item) => item.id === query.state.data?.target)
          ?.active_tasks,
      ),
    refetchIntervalInBackground: false,
    retry: false,
  });
}

export function useComputeRuntime(
  target: string | undefined,
  engine: string | undefined,
  op = 'tts',
  enabled = true,
  busy = false,
) {
  return useQuery({
    queryKey: ['workers', 'runtime', target, op, engine],
    queryFn: ({ signal }) =>
      apiJson<ComputeRuntimeStatus>(
        `/workers/runtime?op=${encodeURIComponent(op)}&engine=${encodeURIComponent(engine ?? '')}`,
        { signal },
      ),
    enabled: enabled && Boolean(target && engine),
    refetchInterval: () => relaxWhenBackendBusy(busy ? ACTIVE_STATUS_POLL_MS : IDLE_STATUS_POLL_MS),
    refetchIntervalInBackground: false,
    retry: false,
  });
}

export async function selectComputeTarget(client: QueryClient, target: string) {
  const next = await apiJson<ComputeTargetState>('/workers/target', {
    method: 'POST',
    body: JSON.stringify({ target }),
  });
  client.setQueryData(['workers', 'target', ''], next);
  await Promise.all([
    client.invalidateQueries({ queryKey: ['workers'] }),
    client.invalidateQueries({ queryKey: ['model-catalogue'] }),
    client.invalidateQueries({ queryKey: ['model-recommendations'] }),
    client.invalidateQueries({ queryKey: ['model-install-jobs'] }),
    client.invalidateQueries({ queryKey: ['engines'] }),
    client.invalidateQueries({ queryKey: ['loaded-models'] }),
    client.invalidateQueries({ queryKey: ['performance-profile'] }),
  ]);
  return next;
}
