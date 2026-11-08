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
  /** Engine ids that load this checkpoint (models.yaml `engines:`). */
  engines?: string[];
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
  /** Other gated repos this row's engines also need accepted (#3855). */
  license_dependencies?: ModelLicenceAcceptance[];
}

export interface ModelCatalogueResponse {
  models: CatalogueModel[];
  total_installed_bytes?: number;
  disk_free_gb?: number;
  /** Free space the backend keeps on top of every model download. */
  disk_headroom_gb?: number;
}

/** How often a screen waiting on a missing model re-reads the catalogue. */
export const CATALOGUE_POLL_WHILE_WAITING_MS = 15_000;

export function useModelCatalogue({
  pollWhile,
}: {
  /** Keep re-reading while this holds, e.g. while a gate waits on a download. */
  pollWhile?: (data: ModelCatalogueResponse | undefined) => boolean;
} = {}) {
  const status = useBackendStatus();
  return useQuery({
    queryKey: ['model-catalogue'],
    queryFn: () => apiJson<ModelCatalogueResponse>('/models'),
    staleTime: 30_000,
    enabled: isBackendReachable(status.stage),
    refetchInterval: pollWhile
      ? (query) => (pollWhile(query.state.data) ? CATALOGUE_POLL_WHILE_WAITING_MS : false)
      : undefined,
  });
}
