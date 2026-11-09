import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiJson, describeError } from '@/lib/api/client';
import { ConfirmDialog } from '@/features/clone/confirm-dialog';
import { relaxWhenBackendBusy } from '@/lib/status-polling';
import { useModelInstallJobs, type ModelInstallJob } from '@/hooks/use-model-install-sync';
import type { CatalogueModel } from '../model-catalogue-query';

export interface LoadedModel {
  id: string;
  checkpoint: string;
  device?: string;
  vram_mb?: number;
  unloadable: boolean;
}

interface LoadedModelsResponse {
  models: LoadedModel[];
  count: number;
}

export interface GatedAccessStatus {
  repo_id: string;
  token_present: boolean;
  ready: boolean;
  repositories: {
    repo_id: string;
    access: 'granted' | 'required' | 'token_missing' | 'unavailable';
  }[];
}

export type ModelJob = ModelInstallJob & {
  rate?: number;
  eta_seconds?: number | null;
  files_done?: number;
  files_total?: number;
  docs_topic?: string;
  failed_at?: number;
  retry_after_seconds?: number;
  phase?: string;
};

type DestructiveAction = { kind: 'delete' | 'reinstall'; model: CatalogueModel };

function modelPath(repoId: string) {
  return repoId.split('/').map(encodeURIComponent).join('/');
}

/** Query keys every model install, removal or selection can change. */
export const MODEL_STATE_QUERY_KEYS = [
  'model-install-jobs',
  'model-catalogue',
  'loaded-models',
  'engines',
  'sidebar-model-status',
  'translation-engines',
  'settings-dictation',
  'sidebar-dictation',
  'diarisation-status',
  'sidebar-diarisation',
  'performance-profile',
] as const;

/**
 * Download, cancel, repair, unload, delete and reinstall for catalogue models,
 * shared by the setup model store and every Settings -> Models family list.
 */
export function useModelOps({
  trackResidency = true,
}: {
  /** False for setup and diarisation: those models are never resident. */
  trackResidency?: boolean;
} = {}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const jobs = useModelInstallJobs<ModelJob>();
  const loaded = useQuery({
    queryKey: ['loaded-models'],
    queryFn: () => apiJson<LoadedModelsResponse>('/model/loaded'),
    enabled: trackResidency,
    staleTime: 5_000,
    refetchInterval: () => relaxWhenBackendBusy(15_000),
  });
  const [pending, setPending] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [checkingAccess, setCheckingAccess] = useState<string | null>(null);
  const [accessStatus, setAccessStatus] = useState<Record<string, GatedAccessStatus>>({});
  const [confirmation, setConfirmation] = useState<DestructiveAction | null>(null);

  const refresh = () =>
    Promise.all(MODEL_STATE_QUERY_KEYS.map((key) => client.invalidateQueries({ queryKey: [key] })));
  const installRequest = (repo: string) =>
    apiJson('/models/install', { method: 'POST', body: JSON.stringify({ repo_id: repo }) });
  const run = async (repo: string, task: () => Promise<unknown>) => {
    setPending(repo);
    setFailed(false);
    try {
      await task();
      return true;
    } catch (error) {
      setFailed(true);
      toast.error(describeError(error));
      return false;
    } finally {
      setPending(null);
    }
  };
  const install = (repo: string, cancel = false) => {
    setDismissed((current) => {
      const next = new Set(current);
      next.delete(repo);
      return next;
    });
    return run(repo, async () => {
      if (cancel)
        await apiJson('/models/install/cancel', {
          method: 'POST',
          body: JSON.stringify({ repo_id: repo }),
        });
      else await installRequest(repo);
      await refresh();
    });
  };
  const checkAccess = async (model: CatalogueModel) => {
    setCheckingAccess(model.repo_id);
    try {
      const status = await apiJson<GatedAccessStatus>(
        `/models/access/status?repo_id=${encodeURIComponent(model.repo_id)}`,
      );
      setAccessStatus((current) => ({ ...current, [model.repo_id]: status }));
    } catch (error) {
      toast.error(describeError(error));
    } finally {
      setCheckingAccess(null);
    }
  };
  const residentFor = (model: CatalogueModel) =>
    !trackResidency
      ? undefined
      : loaded.data?.models.find(
          (entry) =>
            entry.checkpoint === model.repo_id ||
            Boolean(model.dictation_id && entry.checkpoint === model.dictation_id),
        );
  const unloadEntry = async (entry: LoadedModel) => {
    const result = await apiJson<{ success?: boolean; reason?: string }>(
      `/model/unload/${encodeURIComponent(entry.id)}`,
      { method: 'POST' },
    );
    if (result.success === false) throw new Error(result.reason || t('common.error'));
  };
  const unloadForRemoval = async (model: CatalogueModel) => {
    const resident = residentFor(model);
    if (!resident) return;
    if (resident.unloadable) return unloadEntry(resident);
    // The co-loaded WhisperX entry is released with the owning TTS model.
    const owner =
      resident.id === 'asr' ? loaded.data?.models.find((entry) => entry.id === 'tts') : null;
    if (owner?.unloadable) await unloadEntry(owner);
  };
  const runDestructive = ({ kind, model }: DestructiveAction) =>
    run(model.repo_id, async () => {
      await unloadForRemoval(model);
      await apiJson(`/models/${modelPath(model.repo_id)}`, { method: 'DELETE' });
      if (kind === 'reinstall') await installRequest(model.repo_id);
      await refresh();
      toast.success(
        t(kind === 'delete' ? 'modelMaintenance.deleted' : 'modelMaintenance.reinstalling', {
          repoId: model.repo_id,
        }),
      );
    });
  const unload = async (model: CatalogueModel) => {
    const resident = residentFor(model);
    if (!resident) return;
    setPending(model.repo_id);
    setFailed(false);
    try {
      await unloadEntry(resident);
      await Promise.all(
        ['loaded-models', 'engines', 'sidebar-model-status'].map((key) =>
          client.invalidateQueries({ queryKey: [key] }),
        ),
      );
      toast.success(t('modelMaintenance.unloaded'));
    } catch (error) {
      setFailed(true);
      toast.error(t('modelMaintenance.unloadFailed', { message: describeError(error) }));
    } finally {
      setPending(null);
    }
  };

  const confirmDialog = (
    <ConfirmDialog
      open={confirmation !== null}
      onOpenChange={(open) => {
        if (!open) setConfirmation(null);
      }}
      title={t(
        confirmation?.kind === 'reinstall'
          ? 'modelMaintenance.reinstallTitle'
          : 'modelMaintenance.deleteTitle',
      )}
      description={t(
        confirmation?.kind === 'reinstall'
          ? 'modelMaintenance.reinstallConfirm'
          : 'modelMaintenance.deleteConfirm',
        { repoId: confirmation?.model.repo_id ?? '' },
      )}
      confirmLabel={t(
        confirmation?.kind === 'reinstall'
          ? 'modelMaintenance.reinstall'
          : 'modelMaintenance.delete',
      )}
      destructive={confirmation?.kind !== 'reinstall'}
      onConfirm={async () => {
        if (confirmation) await runDestructive(confirmation);
      }}
    />
  );

  return {
    jobs: jobs.data?.jobs ?? [],
    pending,
    failed,
    run,
    refresh,
    install,
    cancel: (repo: string) => install(repo, true),
    isDismissed: (repo: string) => dismissed.has(repo),
    dismiss: (repo: string) => setDismissed((current) => new Set([...current, repo])),
    checkingAccess,
    accessStatus,
    checkAccess,
    residentFor,
    unload,
    requestDelete: (model: CatalogueModel) => setConfirmation({ kind: 'delete', model }),
    requestReinstall: (model: CatalogueModel) => setConfirmation({ kind: 'reinstall', model }),
    confirmDialog,
  };
}

export type ModelOps = ReturnType<typeof useModelOps>;
