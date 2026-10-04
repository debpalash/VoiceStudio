import type { CatalogueModel } from '@/features/settings/model-catalogue-query';

export interface DiarisationStatus {
  active: string;
  model: string;
  installed: boolean;
}

export function isDiarisationReady(
  models: CatalogueModel[] | undefined,
  status: DiarisationStatus | undefined,
): boolean {
  if (!status?.installed) return false;
  const model = models?.find((entry) => entry.repo_id === status.model);
  return Boolean(model && model.supported !== false);
}
