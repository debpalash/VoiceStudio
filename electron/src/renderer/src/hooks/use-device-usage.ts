import { useQuery } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { useBackendStatus } from './use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';

export interface DeviceUsage {
  cpu: number;
  cpu_model: string;
  cpu_physical_cores: number;
  cpu_logical_cores: number;
  cpu_frequency_ghz: number;
  ram: number;
  total_ram: number;
  gpu_name: string;
  gpu_utilization: number | null;
  vram: number;
  total_vram: number;
  gpu_active: boolean;
}

/** Share one local telemetry query; poll only while a usage panel is open. */
export function useDeviceUsage(open: boolean) {
  const backend = useBackendStatus();
  const enabled = open && isBackendReachable(backend.stage);
  return useQuery({
    queryKey: ['sysinfo'],
    enabled,
    queryFn: ({ signal }) => apiJson<DeviceUsage>('/sysinfo', { signal }),
    staleTime: 0,
    refetchInterval: enabled ? 2_000 : false,
    refetchIntervalInBackground: false,
    retry: false,
  });
}
