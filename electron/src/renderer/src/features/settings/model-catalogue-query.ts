import { useQuery } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import type { ModelLicenseInfo, ModelLicenceAcceptance } from './model-license-contract';

export interface CatalogueModel {
  repo_id: string;
  label: string;
  role: string;
  families?: string[];
  size_gb: number;
  installed: boolean;
  supported: boolean;
  dictation_id?: string;
  required?: boolean;
  curated?: boolean;
  incomplete?: boolean;
  size_on_disk_bytes?: number;
  note?: string;
  gated?: boolean;
  requires_hf_token?: boolean;
  access_url?: string;
  prerequisite_repo_id?: string;
  prerequisite_access_url?: string;
  failure_topic?: string;
  /** Versioned disclosure; absence is unverified, never a permission grant. */
  license_info?: ModelLicenseInfo;
  license_acceptance?: ModelLicenceAcceptance;
}

export interface ModelCatalogueResponse {
  target?: string;
  models: CatalogueModel[];
  total_installed_bytes?: number;
  disk_free_gb?: number;
  /** Free space the backend keeps on top of every model download. */
  disk_headroom_gb?: number;
}

export function useModelCatalogue() {
  const status = useBackendStatus();
  return useQuery({
    queryKey: ['model-catalogue'],
    queryFn: () => apiJson<ModelCatalogueResponse>('/models'),
    staleTime: 30_000,
    enabled: isBackendReachable(status.stage),
    refetchInterval: (query) =>
      query.state.data?.target && query.state.data.target !== 'local' ? 5_000 : false,
  });
}
