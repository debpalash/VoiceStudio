import { Link } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangleIcon,
  CheckIcon,
  ChevronDownIcon,
  DownloadIcon,
  HardDriveIcon,
  MemoryStickIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  SearchIcon,
  SparklesIcon,
  Trash2Icon,
  type LucideIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { apiJson, describeError } from '@/lib/api/client';
import { Button, buttonVariants } from '@/components/ui/button';
import { ExternalLink } from '@/components/external-link';
import { Input } from '@/components/ui/input';
import { ConfirmDialog } from '@/features/clone/confirm-dialog';
import { engineFamilyState, useEngines } from '@/hooks/use-engines';
import {
  modelInstallJobTarget,
  TERMINAL_MODEL_INSTALL_STATES,
  useModelInstallJobs,
  type ModelInstallJob,
} from '@/hooks/use-model-install-sync';
import { usePerformanceProfile } from '@/hooks/use-performance-profile';
import { PerformanceProfile } from '@/components/performance-profile';
import { SettingsSection, SettingsRow } from './settings-layout';
import { familyIcons, type ModelFamily } from './model-family';
import { useModelCatalogue, type CatalogueModel } from './model-catalogue-query';
import { resolvePerformanceModelPack } from './performance-model-packs';
import { fmtBytes } from '../../../../../../frontend/src/components/settings/models/format';
import {
  engineSelectionFeedback,
  type EngineSelectionResult,
} from '@/lib/engine-selection-feedback';

interface RecommendedModel {
  repo_id: string;
  label: string;
  role: string;
  size_gb: number;
  required: boolean;
  installed: boolean;
}

interface ModelRecommendations {
  target?: string;
  device: { label: string };
  models: RecommendedModel[];
  download_gb_remaining: number;
  total_gb: number;
  all_installed: boolean;
}

interface LoadedModel {
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

interface GatedAccessStatus {
  repo_id: string;
  token_present: boolean;
  ready: boolean;
  repositories: {
    repo_id: string;
    access: 'granted' | 'required' | 'token_missing' | 'unavailable';
  }[];
}

type DestructiveAction = {
  kind: 'delete' | 'reinstall';
  model: CatalogueModel;
};

function modelPath(repoId: string) {
  return repoId.split('/').map(encodeURIComponent).join('/');
}

function packModelFamily(model: CatalogueModel): ModelFamily {
  if (model.dictation_id) return 'dictation';
  const declared = model.families?.find((family) =>
    ['tts', 'asr', 'dictation', 'diarisation', 'translation', 'llm'].includes(family),
  );
  if (declared) return declared as ModelFamily;
  const role = model.role.toLowerCase();
  return role === 'translation' ? 'translation' : role === 'tts' ? 'tts' : 'asr';
}

export function PerformanceModelPacks({ compact = false }: { compact?: boolean } = {}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const catalogue = useModelCatalogue();
  const jobs = useModelInstallJobs();
  const profile = usePerformanceProfile();
  const recommendations = useQuery({
    queryKey: ['model-recommendations'],
    queryFn: () => apiJson<ModelRecommendations>('/setup/recommendations'),
    staleTime: 30_000,
  });
  const [starting, setStarting] = useState(false);
  const tier = profile.data?.global ?? 'balanced';
  const pack = resolvePerformanceModelPack(catalogue.data?.models ?? [], tier);
  const installTarget = catalogue.data?.target ?? 'local';
  const packRepos = new Set(pack.models.map((model) => model.repo_id));
  const activeJobs =
    jobs.data?.jobs.filter(
      (job) =>
        packRepos.has(job.repo_id) &&
        modelInstallJobTarget(job) === installTarget &&
        !TERMINAL_MODEL_INSTALL_STATES.has(job.state),
    ) ?? [];
  const progressBytes = activeJobs.reduce((total, job) => total + (job.bytes_done ?? 0), 0);
  const progressTotal = activeJobs.reduce((total, job) => total + (job.total_bytes ?? 0), 0);
  const progress = progressTotal > 0 ? Math.round((progressBytes / progressTotal) * 100) : null;
  const diskFree = catalogue.data?.disk_free_gb;
  const lowDisk = diskFree != null && pack.downloadGb + 10 > diskFree;
  const busy = starting || profile.isSaving || activeJobs.length > 0;

  const refresh = () =>
    Promise.all(
      ['model-install-jobs', 'model-catalogue', 'model-recommendations', 'performance-profile'].map(
        (key) => client.invalidateQueries({ queryKey: [key] }),
      ),
    );

  const installPack = async () => {
    if (!profile.data || lowDisk) return;
    setStarting(true);
    try {
      // Persist the requested policy before starting downloads. The backend
      // reconciles this profile after each successful model install, making
      // the pack active without another selection or an app restart.
      await profile.setTier({ tier, family: null });
      if (pack.missing.length === 0) {
        toast.success(t('models.pack_ready', { tier: t('performanceProfile.' + tier) }));
        return;
      }
      const results = await Promise.allSettled(
        pack.missing.map((model) =>
          apiJson('/models/install', {
            method: 'POST',
            body: JSON.stringify({ repo_id: model.repo_id, target: installTarget }),
          }),
        ),
      );
      const failed = results.find((result) => result.status === 'rejected');
      const started = results.filter((result) => result.status === 'fulfilled').length;
      if (started > 0) toast.success(t('models.started_downloading', { count: started }));
      if (failed?.status === 'rejected') toast.error(describeError(failed.reason));
      await refresh();
    } catch (error) {
      toast.error(t('models.install_failed', { message: describeError(error) }));
    } finally {
      setStarting(false);
    }
  };

  if (!catalogue.data || !profile.data)
    return (
      <div role="status" className="space-y-3 p-4">
        <p>{t(catalogue.isError || profile.isError ? 'common.error' : 'common.loading')}</p>
        {(catalogue.isError || profile.isError) && (
          <Button variant="outline" onClick={() => void refresh()}>
            {t('common.retry')}
          </Button>
        )}
      </div>
    );
  if (pack.models.length === 0) return null;

  return (
    <SettingsSection icon={SparklesIcon} title={t('models.pack_title')}>
      <div className="space-y-4 p-4">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <p className="text-sm font-medium">
              {t('models.pack_for', {
                device: recommendations.data?.device.label ?? t('common.loading'),
              })}
            </p>
            <p className="max-w-3xl text-xs leading-relaxed text-muted-foreground">
              {t('models.pack_desc')}
            </p>
          </div>
          <span className="inline-flex items-center gap-1.5 text-xs tabular-nums text-muted-foreground">
            <HardDriveIcon className="size-3.5" aria-hidden="true" />
            {t('models.pack_download', { size: pack.downloadGb.toFixed(1) })}
          </span>
        </div>

        <PerformanceProfile variant="settings" />

        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,15rem),1fr))] gap-2">
          {pack.models.map((model) => {
            const family = packModelFamily(model);
            const Icon = familyIcons[family];
            const active = activeJobs.some((job) => job.repo_id === model.repo_id);
            return (
              <div
                key={model.repo_id}
                className="flex min-w-0 items-center gap-2.5 rounded-xl border border-border/55 bg-background/25 px-3 py-2.5"
              >
                <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-muted/55 text-muted-foreground">
                  <Icon className="size-3.5" aria-hidden="true" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium" title={model.label}>
                    {compact ? t('engineSidebar.' + family) : model.label}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {t(
                      model.installed ? 'modelMaintenance.installed' : 'modelMaintenance.download',
                    )}{' '}
                    · {model.size_gb} GB
                  </span>
                </span>
                {active ? (
                  <LoaderCircleIcon className="size-3.5 shrink-0 animate-spin text-primary" />
                ) : model.installed ? (
                  <CheckIcon className="size-3.5 shrink-0 text-primary" />
                ) : (
                  <span className="size-2 shrink-0 rounded-full border border-muted-foreground/60" />
                )}
              </div>
            );
          })}
        </div>

        {activeJobs.length > 0 && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
              <span>{t('models.pack_installing', { count: activeJobs.length })}</span>
              {progress !== null && <span className="tabular-nums">{progress}%</span>}
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-300"
                style={{ width: `${progress ?? 8}%` }}
              />
            </div>
          </div>
        )}

        {lowDisk && (
          <p role="alert" className="flex items-start gap-2 text-xs text-warning-foreground">
            <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {t('models.reco_low_disk', { need: pack.downloadGb.toFixed(1), free: diskFree })}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/45 pt-3">
          <p className="text-xs text-muted-foreground">
            {t('models.pack_total', { count: pack.models.length, size: pack.totalGb.toFixed(1) })}
          </p>
          <Button disabled={busy || lowDisk} onClick={() => void installPack()}>
            {busy ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : pack.missing.length ? (
              <DownloadIcon />
            ) : (
              <CheckIcon />
            )}
            {t(pack.missing.length ? 'models.pack_install' : 'models.pack_use', {
              tier: t('performanceProfile.' + tier),
              size: pack.downloadGb.toFixed(1),
            })}
          </Button>
        </div>
      </div>
    </SettingsSection>
  );
}

export function SystemRecommendations() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const catalogue = useModelCatalogue();
  const engines = useEngines();
  const recommendations = useQuery({
    queryKey: ['model-recommendations'],
    queryFn: () => apiJson<ModelRecommendations>('/setup/recommendations'),
    staleTime: 30_000,
  });
  const jobs = useModelInstallJobs();
  const data = recommendations.data;
  const installTarget = data?.target ?? 'local';
  const [starting, setStarting] = useState<Set<string>>(new Set());

  if (recommendations.isError || !data) return null;

  const activeDownloads = new Set(
    jobs.data?.jobs
      .filter(
        (job) =>
          modelInstallJobTarget(job) === installTarget &&
          !TERMINAL_MODEL_INSTALL_STATES.has(job.state),
      )
      .map((job) => job.repo_id) ?? [],
  );
  for (const repo of starting) activeDownloads.add(repo);
  const selectedModels = new Set(
    [
      engineFamilyState(engines.data, 'tts')?.active_model,
      engineFamilyState(engines.data, 'asr')?.active_model,
    ].filter((model): model is string => Boolean(model)),
  );
  const missing = data.models.filter((model) => !model.installed);
  const requiredMissing = missing.filter((model) => model.required);
  const diskFree = catalogue.data?.disk_free_gb;
  const refresh = async () => {
    await Promise.all(
      [
        'model-recommendations',
        'model-catalogue',
        'model-install-jobs',
        'setup-status',
        'engines',
        'translation-engines',
        'diarisation-status',
        'sidebar-diarisation',
      ].map((key) => client.invalidateQueries({ queryKey: [key] })),
    );
  };
  const install = async (models: RecommendedModel[]) => {
    const requested = models.filter(
      (model) => !model.installed && !activeDownloads.has(model.repo_id),
    );
    if (requested.length === 0) {
      toast.success(t('models.recommended_installed'));
      return;
    }
    setStarting((current) => new Set([...current, ...requested.map((model) => model.repo_id)]));
    try {
      await Promise.all(
        requested.map((model) =>
          apiJson('/models/install', {
            method: 'POST',
            body: JSON.stringify({
              repo_id: model.repo_id,
              target: data.target ?? 'local',
            }),
          }),
        ),
      );
      toast.success(t('models.started_downloading', { count: requested.length }));
      await refresh();
    } catch (error) {
      toast.error(t('models.install_failed', { message: describeError(error) }));
    } finally {
      setStarting((current) => {
        const next = new Set(current);
        requested.forEach((model) => next.delete(model.repo_id));
        return next;
      });
    }
  };
  const anyActive = activeDownloads.size > 0;
  const requiredGb = requiredMissing.reduce((sum, model) => sum + model.size_gb, 0);
  const lowDisk = diskFree != null && data.download_gb_remaining > diskFree;

  return (
    <SettingsSection
      icon={SparklesIcon}
      title={t('models.reco_for', { device: data.device.label })}
    >
      <div className="space-y-3 px-4 py-4">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5 tabular-nums">
              <HardDriveIcon aria-hidden="true" className="size-3.5" />
              {diskFree == null
                ? t('models.all_size', { size: data.total_gb })
                : t('models.reco_disk_free', { free: diskFree })}
            </span>
          </div>
          {!data.all_installed && (
            <div className="flex flex-wrap items-center gap-2">
              {requiredMissing.length > 0 && (
                <Button
                  size="sm"
                  variant="default"
                  disabled={anyActive}
                  onClick={() => void install(requiredMissing)}
                >
                  <DownloadIcon />
                  {t('models.required_size', { size: requiredGb.toFixed(1) })}
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                disabled={anyActive}
                title={t('models.download_all_remaining_title')}
                onClick={() => void install(missing)}
              >
                {t('models.all_size', { size: data.download_gb_remaining })}
              </Button>
            </div>
          )}
        </div>
        {lowDisk && (
          <p
            role="alert"
            className="flex items-start gap-2 text-xs leading-relaxed text-warning-foreground"
          >
            <AlertTriangleIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
            {t('models.reco_low_disk', {
              need: data.download_gb_remaining,
              free: diskFree,
            })}
          </p>
        )}
        {data.all_installed ? (
          <p className="flex items-center gap-2 text-sm text-foreground/80">
            <CheckIcon aria-hidden="true" className="size-4 text-primary" />
            {t('models.reco_installed_for', { device: data.device.label })}
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {data.models.map((model) => {
              const busy = activeDownloads.has(model.repo_id);
              const selected = selectedModels.has(model.repo_id);
              return (
                <div
                  key={model.repo_id}
                  className="flex min-w-[min(100%,24rem)] flex-1 basis-[24rem] items-center gap-2 rounded-lg border border-border/50 bg-background/25 px-3 py-2 transition-[border-color,background-color] hover:border-border hover:bg-background/40"
                >
                  {busy ? (
                    <RefreshCwIcon
                      aria-hidden="true"
                      className="size-3.5 shrink-0 animate-spin text-primary"
                    />
                  ) : model.installed ? (
                    <CheckIcon aria-hidden="true" className="size-3.5 shrink-0 text-primary" />
                  ) : (
                    <span
                      aria-hidden="true"
                      className="size-3.5 shrink-0 rounded-full border border-border"
                    />
                  )}
                  <span className="min-w-0 flex-1 truncate text-sm" title={model.label}>
                    {model.label}
                  </span>
                  {(selected || model.installed || busy) && (
                    <span
                      className={
                        selected
                          ? 'rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary'
                          : 'rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground'
                      }
                    >
                      {t(
                        selected
                          ? 'modelSettings.selected'
                          : busy
                            ? 'modelMaintenance.downloading'
                            : 'modelMaintenance.installed',
                      )}
                    </span>
                  )}
                  {model.required && (
                    <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-primary">
                      {t('models.req_tag')}
                    </span>
                  )}
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    {model.size_gb} GB
                  </span>
                  {!model.installed && (
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      disabled={busy || starting.size > 0}
                      title={t('models.reco_install_one', {
                        label: model.label,
                      })}
                      aria-label={t('models.reco_install_one', {
                        label: model.label,
                      })}
                      onClick={() => void install([model])}
                    >
                      {busy ? <RefreshCwIcon className="animate-spin" /> : <DownloadIcon />}
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
export function ModelLibrary({
  family,
  setup = false,
  title,
  icon,
}: {
  family?: ModelFamily;
  setup?: boolean;
  title?: string;
  icon?: LucideIcon;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const catalogue = useModelCatalogue();
  const installTarget = catalogue.data?.target ?? 'local';
  const isRemoteCatalogue = installTarget !== 'local';
  const engineQuery = useEngines();
  const dictationPrefs = useQuery({
    queryKey: ['model-library-dictation-prefs'],
    enabled: setup || family === 'dictation',
    queryFn: () => apiJson<{ enabled: boolean; model_id: string }>('/dictation/prefs'),
  });
  const diarisation = useQuery({
    queryKey: ['diarisation-status'],
    enabled: family === 'diarisation',
    queryFn: () => apiJson<{ active: string }>('/engines/diarisation'),
  });
  const [pending, setPending] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [dismissedFailures, setDismissedFailures] = useState<Set<string>>(new Set());
  const [checkingAccess, setCheckingAccess] = useState<string | null>(null);
  const [accessStatus, setAccessStatus] = useState<Record<string, GatedAccessStatus>>({});
  const [query, setQuery] = useState('');
  const [confirmation, setConfirmation] = useState<DestructiveAction | null>(null);
  const jobs = useModelInstallJobs<
    ModelInstallJob & {
      rate?: number;
      eta_seconds?: number | null;
      files_done?: number;
      files_total?: number;
      docs_topic?: string;
      failed_at?: number;
      retry_after_seconds?: number;
      phase?: string;
    }
  >();
  const loaded = useQuery({
    queryKey: ['loaded-models'],
    queryFn: () => apiJson<LoadedModelsResponse>('/model/loaded'),
    enabled: !setup,
    staleTime: 5_000,
    refetchInterval: 15_000,
  });
  const models = catalogue.data?.models.filter((model) => {
    if (setup) return model.supported !== false;
    if (family === 'dictation') return Boolean(model.dictation_id);
    if (family === 'diarisation')
      return (
        ['diarisation', 'diarization'].includes(model.role.toLowerCase()) ||
        model.families?.includes('diarisation')
      );
    return model.role.toLowerCase() === family && !model.dictation_id;
  });
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleModels = models?.filter(
    (model) =>
      !normalizedQuery ||
      [model.label, model.repo_id, model.note, model.role].some((value) =>
        value?.toLocaleLowerCase().includes(normalizedQuery),
      ),
  );
  const primary = setup
    ? visibleModels
        ?.filter((model) => model.required || model.installed || model.curated)
        .sort(
          (a, b) =>
            Number(Boolean(b.required)) - Number(Boolean(a.required)) ||
            Number(Boolean(b.installed)) - Number(Boolean(a.installed)) ||
            Number(Boolean(b.curated)) - Number(Boolean(a.curated)),
        )
    : visibleModels?.filter((model) => model.supported !== false);
  const optional = setup
    ? (visibleModels?.filter((model) => !model.required && !model.installed && !model.curated) ??
      [])
    : [];
  const incompatible = setup
    ? []
    : (visibleModels?.filter((model) => model.supported === false) ?? []);
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['model-install-jobs'] }),
      client.invalidateQueries({ queryKey: ['model-catalogue'] }),
      client.invalidateQueries({ queryKey: ['loaded-models'] }),
      client.invalidateQueries({ queryKey: ['engines'] }),
      client.invalidateQueries({ queryKey: ['sidebar-model-status'] }),
      client.invalidateQueries({ queryKey: ['translation-engines'] }),
      client.invalidateQueries({ queryKey: ['settings-dictation'] }),
      client.invalidateQueries({ queryKey: ['sidebar-dictation'] }),
      client.invalidateQueries({ queryKey: ['diarisation-status'] }),
      client.invalidateQueries({ queryKey: ['sidebar-diarisation'] }),
      client.invalidateQueries({ queryKey: ['performance-profile'] }),
    ]);
  };
  const installRequest = (repo: string) =>
    apiJson('/models/install', {
      method: 'POST',
      body: JSON.stringify({ repo_id: repo, target: installTarget }),
    });
  const action = async (repo: string, cancel = false) => {
    setPending(repo);
    setFailed(false);
    setDismissedFailures((current) => {
      const next = new Set(current);
      next.delete(repo);
      return next;
    });
    try {
      if (cancel) {
        await apiJson('/models/install/cancel', {
          method: 'POST',
          body: JSON.stringify({ repo_id: repo, target: installTarget }),
        });
      } else {
        await installRequest(repo);
      }
      await refresh();
    } catch (error) {
      setFailed(true);
      toast.error(describeError(error));
    } finally {
      setPending(null);
    }
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
    family === 'diarisation'
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
    if (result.success === false)
      throw new Error(result.reason || t('modelMaintenance.unloadFailed'));
  };
  const unloadForRemoval = async (model: CatalogueModel) => {
    const resident = residentFor(model);
    if (!resident) return;
    if (resident.unloadable) {
      await unloadEntry(resident);
      return;
    }
    // The co-loaded WhisperX entry is released with the owning TTS model.
    const owner =
      resident.id === 'asr' ? loaded.data?.models.find((entry) => entry.id === 'tts') : null;
    if (owner?.unloadable) await unloadEntry(owner);
  };
  const runDestructiveAction = async ({ kind, model }: DestructiveAction) => {
    setPending(model.repo_id);
    setFailed(false);
    try {
      await unloadForRemoval(model);
      await apiJson(`/models/${modelPath(model.repo_id)}`, {
        method: 'DELETE',
      });
      if (kind === 'reinstall') await installRequest(model.repo_id);
      await refresh();
      toast.success(
        t(kind === 'delete' ? 'modelMaintenance.deleted' : 'modelMaintenance.reinstalling', {
          repoId: model.repo_id,
        }),
      );
    } catch (error) {
      setFailed(true);
      toast.error(describeError(error));
    } finally {
      setPending(null);
    }
  };
  const unload = async (model: CatalogueModel) => {
    const resident = residentFor(model);
    if (!resident) return;
    setPending(model.repo_id);
    setFailed(false);
    try {
      await unloadEntry(resident);
      await Promise.all([
        client.invalidateQueries({ queryKey: ['loaded-models'] }),
        client.invalidateQueries({ queryKey: ['engines'] }),
        client.invalidateQueries({ queryKey: ['sidebar-model-status'] }),
      ]);
      toast.success(t('modelMaintenance.unloaded'));
    } catch (error) {
      setFailed(true);
      toast.error(t('modelMaintenance.unloadFailed', { message: describeError(error) }));
    } finally {
      setPending(null);
    }
  };
  const selectableAsrModel = (repoId: string) =>
    repoId.startsWith('Systran/faster-') || repoId === 'deepdml/faster-whisper-large-v3-turbo-ct2';
  const selectAsrModel = async (model: CatalogueModel) => {
    setPending(model.repo_id);
    setFailed(false);
    try {
      const current = engineQuery.data?.asr?.active;
      const backendId = current === 'faster-whisper-isolated' ? current : 'faster-whisper';
      const result = await apiJson<EngineSelectionResult>('/engines/select', {
        method: 'POST',
        body: JSON.stringify({
          family: 'asr',
          backend_id: backendId,
          model_id: model.repo_id,
        }),
      });
      await Promise.all([
        client.invalidateQueries({ queryKey: ['engines'] }),
        client.invalidateQueries({ queryKey: ['performance-profile'] }),
      ]);
      const feedback = engineSelectionFeedback(result, 'asr');
      if (feedback.tone === 'warning') toast.warning(t(feedback.key, feedback.values));
      else toast.success(t(feedback.key, feedback.values));
    } catch (error) {
      setFailed(true);
      toast.error(describeError(error));
    } finally {
      setPending(null);
    }
  };
  const selectDictationModel = async (model: CatalogueModel) => {
    if (!model.dictation_id) return;
    setPending(model.repo_id);
    setFailed(false);
    try {
      await apiJson('/dictation/prefs', {
        method: 'POST',
        body: JSON.stringify({ model_id: model.dictation_id, enabled: true }),
      });
      await Promise.all([
        client.invalidateQueries({
          queryKey: ['model-library-dictation-prefs'],
        }),
        client.invalidateQueries({ queryKey: ['settings-dictation'] }),
        client.invalidateQueries({ queryKey: ['sidebar-dictation'] }),
        client.invalidateQueries({ queryKey: ['dictation-shortcut-prefs'] }),
        client.invalidateQueries({ queryKey: ['transcription-readiness'] }),
        client.invalidateQueries({ queryKey: ['performance-profile'] }),
      ]);
      const feedback = engineSelectionFeedback({ active: model.dictation_id }, 'dictation');
      toast.success(t(feedback.key, feedback.values));
    } catch (error) {
      setFailed(true);
      toast.error(describeError(error));
    } finally {
      setPending(null);
    }
  };
  const renderModel = (model: CatalogueModel) => {
    const job = jobs.data?.jobs.find(
      (job) => job.repo_id === model.repo_id && modelInstallJobTarget(job) === installTarget,
    );
    const downloading = Boolean(job && !TERMINAL_MODEL_INSTALL_STATES.has(job.state));
    const cancelling = job?.state === 'cancelling' || job?.phase === 'cancelling';
    const failedJob = job?.state === 'failed' && !dismissedFailures.has(model.repo_id);
    const access = accessStatus[model.repo_id];
    const gatedFailure =
      failedJob &&
      ['HF_AUTH_FAILED', 'PYANNOTE_LICENSE_REQUIRED', 'POCKETTTS_GATED_WEIGHTS'].includes(
        job?.docs_topic ?? '',
      );
    const retryAfter = Math.max(0, Math.ceil(job?.retry_after_seconds ?? 0));
    const recovery = (() => {
      switch (job?.docs_topic) {
        case 'DISK_SPACE_LOW':
        case 'AUDIO_IO_FAILED':
        case 'OS_INVALID_ARGUMENT':
          return {
            to: '/settings/storage' as const,
            label: 'settings.storage',
          };
        case 'HF_MIRROR_UNREACHABLE':
          return {
            to: '/settings/models' as const,
            label: 'models.mirror_title',
          };
        case 'HF_AUTH_FAILED':
        case 'PYANNOTE_LICENSE_REQUIRED':
        case 'POCKETTTS_GATED_WEIGHTS':
          return {
            to: '/settings/credentials' as const,
            label: 'settings.credentials',
          };
        case 'SOCKS_PROXY_SUPPORT_MISSING':
        case 'SSL_HANDSHAKE_FAILURE':
        case 'TLS_CONNECTION_DROPPED':
          return {
            to: '/settings/network' as const,
            label: 'settings.network',
          };
        case 'GPU_OOM':
        case 'COMPUTE_TYPE_UNSUPPORTED':
        case 'WINDOWS_PAGING_FILE_TOO_SMALL':
          return {
            to: '/settings/performance' as const,
            label: 'settings.perf_title',
          };
        default:
          return null;
      }
    })();
    const pct = job?.total_bytes
      ? Math.min(100, Math.round(((job.bytes_done ?? 0) / job.total_bytes) * 100))
      : null;
    const resident = residentFor(model);
    const isAsrModel = model.role.toLowerCase() === 'asr' && !model.dictation_id;
    const selectableAsr = isAsrModel && selectableAsrModel(model.repo_id);
    const checkingAsrSelection = selectableAsr && !engineQuery.data;
    const activeAsrModel = isAsrModel && engineQuery.data?.asr?.active_model === model.repo_id;
    const engineFamily = model.role.toLowerCase();
    const activeEngineModel =
      family === engineFamily &&
      (engineFamily === 'tts' || engineFamily === 'asr' || engineFamily === 'llm') &&
      engineQuery.data?.[engineFamily]?.active_model === model.repo_id;
    const activeDictationModel =
      Boolean(model.dictation_id) &&
      dictationPrefs.data?.enabled === true &&
      dictationPrefs.data.model_id === model.dictation_id;
    const activeDiarisationModel =
      family === 'diarisation' &&
      ((diarisation.data?.active === 'pyannote' &&
        model.repo_id === 'pyannote/speaker-diarization-3.1') ||
        (diarisation.data?.active === 'audiocpp-sortformer' &&
          model.families?.includes('diarisation')));
    const eta = (() => {
      const seconds = job?.eta_seconds;
      if (!seconds || seconds <= 0) return '';
      if (seconds < 60) return `~${Math.ceil(seconds)}s`;
      if (seconds < 3600) return `~${Math.ceil(seconds / 60)}m`;
      return `~${(seconds / 3600).toFixed(1)}h`;
    })();
    return (
      <SettingsRow
        key={model.repo_id}
        id={'model-' + model.repo_id.replaceAll('/', '-')}
        title={model.label}
        description={model.repo_id}
        variant="card"
        active={Boolean(
          activeEngineModel || activeDictationModel || activeDiarisationModel || resident,
        )}
      >
        {setup && (
          <span
            className="text-xs text-muted-foreground"
            title={
              model.curated && !model.required ? t('firstrun.chip_recommended_title') : undefined
            }
          >
            {t(
              model.required
                ? 'firstrun.chip_required'
                : model.curated
                  ? 'firstrun.chip_recommended'
                  : 'firstrun.chip_optional',
            )}
          </span>
        )}
        {!setup && model.curated && (
          <span
            className="rounded-full bg-primary/10 px-2 py-1 text-xs text-primary"
            title={t('firstrun.chip_recommended_title')}
          >
            {t('firstrun.chip_recommended')}
          </span>
        )}
        {!activeAsrModel &&
          (activeEngineModel || activeDictationModel || activeDiarisationModel) && (
            <span className="flex items-center gap-1 rounded-full bg-primary/10 px-2 py-1 text-xs text-primary">
              <CheckIcon className="size-3.5" />
              {t('modelSettings.selected')}
            </span>
          )}
        {(model.gated || gatedFailure) && !model.installed && (
          <details className="group/access order-last basis-full rounded-lg border border-warning/20 bg-warning/5 text-xs @2xl:max-w-2xl">
            <summary
              role={gatedFailure || access?.ready === false ? 'alert' : 'status'}
              className="flex min-h-8 cursor-pointer list-none items-center gap-1.5 rounded-lg px-3 py-2 font-medium text-foreground outline-none marker:hidden hover:bg-warning/5 focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden"
            >
              {access?.ready && <CheckIcon className="size-3.5 text-emerald-500" />}
              <span className="min-w-0 flex-1 truncate">
                {t(
                  access?.ready
                    ? 'modelMaintenance.gatedAccessReady'
                    : access
                      ? 'modelMaintenance.gatedAccessStillRequired'
                      : 'modelMaintenance.gatedAccessRequired',
                )}
              </span>
              <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground transition-transform group-open/access:rotate-180 motion-reduce:transition-none" />
            </summary>
            <div className="border-t border-warning/15 px-3 py-2.5">
              <div className="flex flex-wrap gap-1.5">
                {model.access_url && (
                  <ExternalLink href={model.access_url}>
                    {t('modelMaintenance.requestModelAccess')}
                  </ExternalLink>
                )}
                {model.prerequisite_access_url && (
                  <ExternalLink href={model.prerequisite_access_url}>
                    {t('modelMaintenance.acceptDependencyTerms')}
                  </ExternalLink>
                )}
                {model.requires_hf_token && (
                  <Link
                    to="/settings/credentials"
                    className={buttonVariants({
                      variant: 'outline',
                      size: 'sm',
                    })}
                  >
                    <WrenchIcon />
                    {t('modelMaintenance.addHfToken')}
                  </Link>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={checkingAccess === model.repo_id}
                  onClick={() => void checkAccess(model)}
                >
                  {checkingAccess === model.repo_id ? (
                    <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
                  ) : access?.ready ? (
                    <CheckIcon />
                  ) : (
                    <RefreshCwIcon />
                  )}
                  {t('modelMaintenance.checkAccess')}
                </Button>
                {gatedFailure && (
                  <Button
                    size="sm"
                    variant="default"
                    disabled={Boolean(pending) || retryAfter > 0}
                    onClick={() => void action(model.repo_id)}
                  >
                    <RefreshCwIcon />
                    {retryAfter > 0
                      ? t('modelMaintenance.retryAfter', {
                          seconds: retryAfter,
                        })
                      : t('common.retry')}
                  </Button>
                )}
              </div>
              {gatedFailure && job?.error && (
                <details className="mt-2 text-muted-foreground">
                  <summary className="cursor-pointer">{t('common.details')}</summary>
                  <p className="mt-1 break-words">{job.error}</p>
                </details>
              )}
            </div>
          </details>
        )}
        {failedJob && !gatedFailure && (
          <details className="basis-full rounded-lg border border-destructive/15 bg-destructive/5 px-3 py-2 text-xs @2xl:max-w-xl">
            <summary className="cursor-pointer font-medium text-destructive">
              {t('modelMaintenance.failed')}
            </summary>
            <p role="alert" className="mt-2 break-words text-muted-foreground">
              {job?.error || t('modelMaintenance.failed')}
            </p>
            <div className="mt-2 flex flex-wrap gap-1">
              <Button
                size="xs"
                variant="outline"
                disabled={Boolean(pending) || retryAfter > 0}
                onClick={() => void action(model.repo_id)}
              >
                <RefreshCwIcon />
                {retryAfter > 0
                  ? t('modelMaintenance.retryAfter', { seconds: retryAfter })
                  : t('common.retry')}
              </Button>
              {recovery && (
                <Link to={recovery.to} className={buttonVariants({ variant: 'ghost', size: 'xs' })}>
                  <WrenchIcon />
                  {t(recovery.label)}
                </Link>
              )}
              <Button
                size="xs"
                variant="ghost"
                onClick={() =>
                  setDismissedFailures((current) => new Set([...current, model.repo_id]))
                }
              >
                <XIcon />
                {t('common.dismiss')}
              </Button>
            </div>
          </details>
        )}
        <span className="text-xs tabular-nums text-muted-foreground">
          {model.incomplete && model.size_on_disk_bytes
            ? fmtBytes(model.size_on_disk_bytes)
            : `${model.size_gb} GB`}
        </span>
        {downloading ? (
          <>
            <div className="min-w-48 flex-1 space-y-1.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs tabular-nums">
                <span role="status">
                  {t('modelMaintenance.downloading')}
                  {pct === null ? '' : ' ' + pct + '%'}
                </span>
                {job?.total_bytes ? (
                  <span className="text-muted-foreground">
                    {fmtBytes(job.bytes_done ?? 0)} / {fmtBytes(job.total_bytes)}
                  </span>
                ) : null}
                {job?.rate && job.rate > 0 ? (
                  <span className="text-muted-foreground">{fmtBytes(job.rate)}/s</span>
                ) : null}
                {eta && <span className="text-muted-foreground">{eta}</span>}
              </div>
              <div
                role="progressbar"
                aria-label={t('modelMaintenance.downloading')}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={pct ?? undefined}
                className="h-1.5 overflow-hidden rounded-full bg-muted"
              >
                <div
                  className="h-full rounded-full bg-primary transition-[width] duration-300"
                  style={{ width: `${pct ?? 8}%` }}
                />
              </div>
            </div>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending === model.repo_id || cancelling}
              onClick={() => void action(model.repo_id, true)}
            >
              {t('common.cancel')}
            </Button>
          </>
        ) : model.installed ? (
          <>
            <span className="flex items-center gap-1 text-xs">
              <CheckIcon className="size-3.5" />
              {t('modelMaintenance.installed')}
            </span>
            {!setup && resident && (
              <span
                className="flex items-center gap-1 rounded-full bg-primary/10 px-2 py-1 text-xs text-primary"
                title={[
                  t('modelMaintenance.inMemoryDescription'),
                  resident.device,
                  resident.vram_mb ? `${resident.vram_mb.toFixed(0)} MB VRAM` : undefined,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              >
                <MemoryStickIcon className="size-3.5" />
                {t('modelMaintenance.inMemory')}
                {resident.device ? ` · ${resident.device}` : ''}
              </span>
            )}
            {!setup && !isRemoteCatalogue && resident?.unloadable && (
              <Button
                size="sm"
                variant="outline"
                disabled={pending === model.repo_id}
                onClick={() => void unload(model)}
              >
                {t('modelMaintenance.unload')}
              </Button>
            )}
            {selectableAsr && (
              <Button
                size="sm"
                className="min-w-24"
                variant={activeAsrModel ? 'secondary' : 'outline'}
                disabled={Boolean(pending) || checkingAsrSelection || activeAsrModel}
                aria-pressed={activeAsrModel}
                onClick={() => void selectAsrModel(model)}
              >
                {checkingAsrSelection ? (
                  <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
                ) : activeAsrModel ? (
                  <CheckIcon />
                ) : null}
                {t(
                  checkingAsrSelection
                    ? 'common.loading'
                    : activeAsrModel
                      ? 'modelSettings.selected'
                      : 'modelSettings.select',
                )}
              </Button>
            )}
            {model.dictation_id && (
              <Button
                size="sm"
                variant={activeDictationModel ? 'secondary' : 'outline'}
                disabled={Boolean(pending) || activeDictationModel}
                aria-pressed={activeDictationModel}
                onClick={() => void selectDictationModel(model)}
              >
                {activeDictationModel ? <CheckIcon /> : null}
                {t(activeDictationModel ? 'modelSettings.selected' : 'modelSettings.select')}
              </Button>
            )}
            {!setup && !isRemoteCatalogue && (
              <>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  disabled={Boolean(pending)}
                  title={t('modelMaintenance.reinstall')}
                  aria-label={t('modelMaintenance.reinstall')}
                  onClick={() => setConfirmation({ kind: 'reinstall', model })}
                >
                  <RefreshCwIcon />
                </Button>
                <Button
                  size="icon-sm"
                  variant="destructive"
                  disabled={Boolean(pending)}
                  title={t('modelMaintenance.delete')}
                  aria-label={t('modelMaintenance.delete')}
                  onClick={() => setConfirmation({ kind: 'delete', model })}
                >
                  <Trash2Icon />
                </Button>
              </>
            )}
          </>
        ) : model.incomplete ? (
          <>
            <span
              className="flex items-center gap-1 text-xs text-warning-foreground"
              title={t('modelMaintenance.repairDescription')}
            >
              <WrenchIcon className="size-3.5" />
              {t('modelMaintenance.incomplete')}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={Boolean(pending) || !model.supported}
              onClick={() => void action(model.repo_id)}
            >
              <WrenchIcon />
              {t('modelMaintenance.repair')}
            </Button>
            {!setup && (
              <Button
                size="icon-sm"
                variant="destructive"
                disabled={Boolean(pending)}
                title={t('modelMaintenance.delete')}
                aria-label={t('modelMaintenance.delete')}
                onClick={() => setConfirmation({ kind: 'delete', model })}
              >
                <Trash2Icon />
              </Button>
            )}
          </>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={Boolean(pending) || !model.supported}
            onClick={() => void action(model.repo_id)}
          >
            <DownloadIcon />
            {t(model.supported ? 'modelMaintenance.download' : 'modelSettings.unavailable')}
          </Button>
        )}
      </SettingsRow>
    );
  };
  return (
    <>
      <SettingsSection
        title={title ?? t('modelSettings.models')}
        icon={icon}
        contentVariant="cards"
      >
        {!setup && (
          <div className="glass-panel col-span-full flex items-center gap-2 rounded-xl border border-border/60 bg-card/40 px-4 py-3 shadow-xs/5">
            <SearchIcon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
            <Input
              type="search"
              aria-label={t('preferences.search')}
              placeholder={t('preferences.search')}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setQuery('');
              }}
              className="h-8 min-w-0 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0 dark:bg-transparent"
            />
          </div>
        )}
        {catalogue.isError ? (
          <Button variant="ghost" onClick={() => void catalogue.refetch()}>
            {t('backend.retry')}
          </Button>
        ) : !models ? (
          <p className="p-4 text-sm">{t('preferences.loading')}</p>
        ) : (
          <>
            {primary?.map(renderModel)}
            {optional.length > 0 && (
              <details className="group/models col-span-full">
                <summary className="glass-panel cursor-pointer rounded-xl border border-border/60 bg-card/40 px-4 py-3 text-sm text-muted-foreground shadow-xs/5 focus-visible:outline-ring">
                  {t('firstrun.lib_show_all', { count: optional.length })}
                </summary>
                <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),1fr))] gap-3">
                  {optional.map(renderModel)}
                </div>
              </details>
            )}
            {incompatible.length > 0 && (
              <details className="group/models col-span-full">
                <summary className="glass-panel cursor-pointer rounded-xl border border-border/60 bg-card/40 px-4 py-3 text-sm text-muted-foreground shadow-xs/5 focus-visible:outline-ring">
                  {t('modelSettings.unavailable')} ({incompatible.length})
                </summary>
                <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),1fr))] gap-3">
                  {incompatible.map(renderModel)}
                </div>
              </details>
            )}
            {primary?.length === 0 && optional.length === 0 && incompatible.length === 0 && (
              <p role="status" className="col-span-full p-4 text-sm text-muted-foreground">
                {t('preferences.no_matches')}
              </p>
            )}
          </>
        )}
        {failed && (
          <p role="alert" className="col-span-full p-4 text-sm text-destructive">
            {t('modelMaintenance.failed')}
          </p>
        )}
      </SettingsSection>
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
          if (confirmation) await runDestructiveAction(confirmation);
        }}
      />
    </>
  );
}
