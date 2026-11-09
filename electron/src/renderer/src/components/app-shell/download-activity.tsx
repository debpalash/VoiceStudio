import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { DownloadIcon } from 'lucide-react';
import { InstallProgress } from '@/components/model-install-progress';
import { useModelInstallJobs } from '@/hooks/use-model-install-sync';
import { apiJson, describeError } from '@/lib/api/client';
import {
  ACTIVE_INSTALL_STAGES,
  describeInstall,
  type InstallJobLike,
  type InstallView,
} from '@/lib/model-install-state';
import type { ModelCatalogueResponse } from '@/features/settings/model-catalogue-query';

export interface DownloadActivityItem {
  repoId: string;
  name: string;
  view: InstallView;
  key: string;
}

/**
 * Model downloads running anywhere in the app (and recent failures), for the
 * notifications panel, so a download started from a feature prompt stays
 * visible and cancellable after the user moves on.
 */
export function useDownloadActivity() {
  const client = useQueryClient();
  const jobs = useModelInstallJobs<InstallJobLike>();
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<string | null>(null);
  const labels = new Map(
    client
      .getQueryData<ModelCatalogueResponse>(['model-catalogue'])
      ?.models?.map((model) => [model.repo_id, model.label]) ?? [],
  );
  const items: DownloadActivityItem[] = (Array.isArray(jobs.data?.jobs) ? jobs.data.jobs : [])
    .map((job) => ({
      repoId: job.repo_id,
      name: labels.get(job.repo_id) || job.repo_id.split('/').pop() || job.repo_id,
      view: describeInstall(job),
      key: `${job.repo_id}:${job.failed_at ?? ''}`,
    }))
    .filter(
      (item) =>
        ACTIVE_INSTALL_STAGES.has(item.view.stage) ||
        (item.view.stage === 'failed' && !dismissed.has(item.key)),
    );

  const post = async (repoId: string, path: string) => {
    setPending(repoId);
    try {
      await apiJson(path, { method: 'POST', body: JSON.stringify({ repo_id: repoId }) });
    } catch (error) {
      toast.error(describeError(error));
    } finally {
      setPending(null);
      await client.invalidateQueries({ queryKey: ['model-install-jobs'] }).catch(() => undefined);
    }
  };
  return {
    items,
    active: items.filter((item) => item.view.stage !== 'failed').length,
    pending,
    cancel: (repoId: string) => post(repoId, '/models/install/cancel'),
    retry: (repoId: string) => post(repoId, '/models/install'),
    dismiss: (key: string) => setDismissed((current) => new Set([...current, key])),
  };
}

export type DownloadActivity = ReturnType<typeof useDownloadActivity>;

export function DownloadActivityList({ activity }: { activity: DownloadActivity }) {
  const { t } = useTranslation();
  if (!activity.items.length) return null;
  return (
    <section aria-label={t('modelInstall.downloads')} className="space-y-1">
      <h3 className="flex items-center gap-1.5 px-2 pt-1 text-[11px] font-medium text-muted-foreground">
        <DownloadIcon aria-hidden="true" className="size-3.5" />
        {t('modelInstall.downloads')}
      </h3>
      {activity.items.map((item) => (
        <div key={item.key} className="rounded-lg border border-border/60 bg-card/40 px-2.5 py-2">
          <InstallProgress
            view={item.view}
            name={item.name}
            busy={activity.pending === item.repoId}
            onCancel={() => void activity.cancel(item.repoId)}
            onRetry={() => void activity.retry(item.repoId)}
            onDismiss={() => activity.dismiss(item.key)}
          />
        </div>
      ))}
    </section>
  );
}
