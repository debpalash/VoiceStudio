import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangleIcon,
  CheckIcon,
  DownloadIcon,
  HardDriveIcon,
  LoaderCircleIcon,
  SparklesIcon,
  type LucideIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { apiJson, describeError } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { InstallProgress } from '@/components/model-install-progress';
import { describeInstallGroup } from '@/lib/model-install-state';
import { TERMINAL_MODEL_INSTALL_STATES, useModelInstallJobs } from '@/hooks/use-model-install-sync';
import {
  performanceTiers,
  usePerformanceProfile,
  type PerformanceTier,
} from '@/hooks/use-performance-profile';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { SettingsSection, SettingsRow } from './settings-layout';
import { familyIcons, type ModelFamily } from './model-family';
import { useModelCatalogue, type CatalogueModel } from './model-catalogue-query';
import { ModelLicense } from './model-license';
import { largestFittingTier, resolvePerformanceModelPack } from './performance-model-packs';
import { modelDiskShortfall } from './model-disk-space';
import { fmtGb } from '@shared/components/settings/models/format';
import { useModelOps } from './model-list/model-ops';
import { describeJob } from './model-list/model-job';
import { ModelActions } from './model-list/model-actions';
import { ModelFailure, ModelGatedAccess } from './model-list/model-notices';

export { SystemRecommendations } from './system-recommendations';

export interface RecommendedModel {
  repo_id: string;
  label: string;
  role: string;
  size_gb: number;
  required: boolean;
  installed: boolean;
}

export interface ModelRecommendations {
  device: { label: string };
  models: RecommendedModel[];
  download_gb_remaining: number;
  total_gb: number;
  all_installed: boolean;
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
  // Preview independently of the runtime profile: packs must be selectable
  // before any applicable engine is installed. Persist only on confirmation.
  const [selectedTier, setSelectedTier] = useState<PerformanceTier | null>(null);
  const profileTier =
    (profile.data?.global === 'auto'
      ? (profile.data.plan?.resolved ?? 'balanced')
      : profile.data?.global) ?? 'balanced';
  // Preselect the largest pack that fits the model disk, never one that cannot.
  const tier =
    selectedTier ??
    largestFittingTier(
      catalogue.data?.models ?? [],
      profileTier,
      catalogue.data?.disk_free_gb,
      catalogue.data?.disk_headroom_gb,
    );
  const pack = resolvePerformanceModelPack(catalogue.data?.models ?? [], tier);
  const packRepos = new Set(pack.models.map((model) => model.repo_id));
  const activeJobs =
    jobs.data?.jobs.filter(
      (job) => packRepos.has(job.repo_id) && !TERMINAL_MODEL_INSTALL_STATES.has(job.state),
    ) ?? [];
  const packView = describeInstallGroup(activeJobs);
  const diskShortfall = pack.missing.length
    ? modelDiskShortfall(
        pack.downloadGb,
        catalogue.data?.disk_free_gb,
        catalogue.data?.disk_headroom_gb,
      )
    : null;
  const lowDisk = diskShortfall !== null;
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
      await profile.setTier({
        tier:
          selectedTier === null && profile.data.global === 'auto' && tier === profileTier
            ? 'auto'
            : tier,
        family: null,
      });
      if (pack.missing.length === 0) {
        toast.success(t('models.pack_ready', { tier: t('performanceProfile.' + tier) }));
        return;
      }
      const results = await Promise.allSettled(
        pack.missing.map((model) =>
          apiJson('/models/install', {
            method: 'POST',
            body: JSON.stringify({ repo_id: model.repo_id }),
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

        <ToggleGroup
          aria-label={t('models.pack_title')}
          value={[tier]}
          onValueChange={(values) => {
            const next = performanceTiers.find((candidate) => candidate === values[0]);
            if (next) setSelectedTier(next);
          }}
          disabled={busy}
          className="grid w-full grid-cols-2 rounded-xl border border-border/60 bg-muted/45 p-1 sm:grid-cols-4"
        >
          {performanceTiers.map((candidate) => (
            <ToggleGroupItem key={candidate} value={candidate}>
              {t('performanceProfile.' + candidate)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

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
                  {/* Name the model even in the compact setup view: a pack can
                      hold two models of one family (two dictation engines). */}
                  <span className="block truncate text-xs font-medium" title={model.label}>
                    {model.label}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {compact && `${t('engineSidebar.' + family)} · `}
                    {t(
                      model.installed ? 'modelMaintenance.installed' : 'modelMaintenance.download',
                    )}{' '}
                    · {fmtGb(model.size_gb)}
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

        {packView && (
          <InstallProgress
            view={packView}
            name={t('models.pack_installing', { count: activeJobs.length })}
          />
        )}

        {diskShortfall && (
          <p role="alert" className="flex items-start gap-2 text-xs text-warning-foreground">
            <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {t('models.pack_low_disk', { ...diskShortfall })}
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

/** First-run model store: every supported model, required and recommended first. */
export function ModelLibrary({ title, icon }: { title?: string; icon?: LucideIcon } = {}) {
  const { t } = useTranslation();
  const catalogue = useModelCatalogue();
  const ops = useModelOps({ trackResidency: false });
  const models = catalogue.data?.models.filter((model) => model.supported !== false);
  const primary = models
    ?.filter((model) => model.required || model.installed || model.curated)
    .sort(
      (a, b) =>
        Number(Boolean(b.required)) - Number(Boolean(a.required)) ||
        Number(Boolean(b.installed)) - Number(Boolean(a.installed)) ||
        Number(Boolean(b.curated)) - Number(Boolean(a.curated)),
    );
  const optional =
    models?.filter((model) => !model.required && !model.installed && !model.curated) ?? [];
  const renderModel = (model: CatalogueModel) => {
    const info = describeJob(model, ops);
    return (
      <SettingsRow
        key={model.repo_id}
        id={'model-' + model.repo_id.replaceAll('/', '-')}
        title={model.label}
        description={model.repo_id}
        variant="card"
      >
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
        <ModelLicense
          repoId={model.repo_id}
          label={model.label}
          info={model.license_info}
          acceptance={model.license_acceptance}
          dependencies={model.license_dependencies}
        />
        <ModelGatedAccess model={model} ops={ops} info={info} />
        <ModelFailure model={model} ops={ops} info={info} />
        <ModelActions model={model} ops={ops} info={info} maintenance={false} />
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
          </>
        )}
        {ops.failed && (
          <p role="alert" className="col-span-full p-4 text-sm text-destructive">
            {t('modelMaintenance.failed')}
          </p>
        )}
      </SettingsSection>
      {ops.confirmDialog}
    </>
  );
}
