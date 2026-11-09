import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangleIcon,
  CheckIcon,
  DownloadIcon,
  HardDriveIcon,
  RefreshCwIcon,
  SparklesIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { apiJson, describeError } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { engineFamilyState, useEngines } from '@/hooks/use-engines';
import { TERMINAL_MODEL_INSTALL_STATES, useModelInstallJobs } from '@/hooks/use-model-install-sync';
import { SettingsSection } from './settings-layout';
import { useModelCatalogue } from './model-catalogue-query';
import { modelDiskShortfall } from './model-disk-space';
import { fmtGb } from '@shared/components/settings/models/format';
import type { ModelRecommendations, RecommendedModel } from './model-library';

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
  const [starting, setStarting] = useState<Set<string>>(new Set());

  if (recommendations.isError || !data) return null;

  const activeDownloads = new Set(
    jobs.data?.jobs
      .filter((job) => !TERMINAL_MODEL_INSTALL_STATES.has(job.state))
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
            body: JSON.stringify({ repo_id: model.repo_id }),
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
  const diskShortfall = modelDiskShortfall(
    data.download_gb_remaining,
    diskFree,
    catalogue.data?.disk_headroom_gb,
  );

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
        {diskShortfall && (
          <p
            role="alert"
            className="flex items-start gap-2 text-xs leading-relaxed text-warning-foreground"
          >
            <AlertTriangleIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
            {t('models.reco_low_disk', { ...diskShortfall })}
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
                    {fmtGb(model.size_gb)}
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
