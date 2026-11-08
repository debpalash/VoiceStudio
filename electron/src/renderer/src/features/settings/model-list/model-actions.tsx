import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CheckIcon,
  DownloadIcon,
  MemoryStickIcon,
  RefreshCwIcon,
  Trash2Icon,
  TriangleAlertIcon,
  WrenchIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { InstallProgress } from '@/components/model-install-progress';
import { fmtBytes, fmtGb } from '@shared/components/settings/models/format';
import type { CatalogueModel } from '../model-catalogue-query';
import type { ModelOps } from './model-ops';
import type { JobInfo } from './model-job';

export function ModelSize({ model }: { model: CatalogueModel }) {
  return (
    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
      {model.incomplete && model.size_on_disk_bytes
        ? fmtBytes(model.size_on_disk_bytes)
        : fmtGb(model.size_gb)}
    </span>
  );
}

function DownloadProgress({
  model,
  ops,
  info,
}: {
  model: CatalogueModel;
  ops: ModelOps;
  info: JobInfo;
}) {
  if (!info.view) return null;
  return (
    <InstallProgress
      className="min-w-40 flex-1 @2xl:max-w-72"
      view={info.view}
      busy={ops.pending === model.repo_id}
      onCancel={() => void ops.cancel(model.repo_id)}
    />
  );
}

function DeleteButton({ model, ops }: { model: CatalogueModel; ops: ModelOps }) {
  const { t } = useTranslation();
  return (
    <Button
      size="icon-sm"
      variant="ghost"
      className="text-muted-foreground hover:text-destructive"
      disabled={Boolean(ops.pending)}
      title={t('modelMaintenance.delete')}
      aria-label={t('modelMaintenance.delete')}
      onClick={() => ops.requestDelete(model)}
    >
      <Trash2Icon />
    </Button>
  );
}

/**
 * The install lifecycle of one catalogue model: download (with progress and
 * cancel), repair, installed state with unload/reinstall/delete. `select` is
 * the caller's Select control, shown once the model is installed.
 */
export function ModelActions({
  model,
  ops,
  info,
  select,
  maintenance = true,
  showSize = true,
}: {
  model: CatalogueModel;
  ops: ModelOps;
  info: JobInfo;
  select?: ReactNode;
  /** False in first-run setup: no unload, reinstall or delete there. */
  maintenance?: boolean;
  showSize?: boolean;
}) {
  const { t } = useTranslation();
  const resident = maintenance ? ops.residentFor(model) : undefined;
  const busy = Boolean(ops.pending);
  return (
    <>
      {showSize && <ModelSize model={model} />}
      {info.downloading ? (
        <DownloadProgress model={model} ops={ops} info={info} />
      ) : model.installed ? (
        <>
          {(model.license_acceptance?.required && !model.license_acceptance.accepted) ||
          (model.license_dependencies ?? []).some((dependency) => dependency.required && !dependency.accepted) ? (
            // Downloaded but unusable until every licence its engine needs is accepted (#3855).
            <span className="flex items-center gap-1 rounded-full bg-warning/10 px-2 py-0.5 text-xs text-warning">
              <TriangleAlertIcon className="size-3.5" />
              {t('modelLicense.acceptNeeded')}
            </span>
          ) : (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <CheckIcon className="size-3.5 text-primary" />
              {t('modelMaintenance.installed')}
            </span>
          )}
          {resident && (
            <span
              className="flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary"
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
          {resident?.unloadable && (
            <Button
              size="sm"
              variant="outline"
              disabled={ops.pending === model.repo_id}
              onClick={() => void ops.unload(model)}
            >
              {t('modelMaintenance.unload')}
            </Button>
          )}
          {select}
          {maintenance && (
            <>
              <Button
                size="icon-sm"
                variant="ghost"
                className="text-muted-foreground"
                disabled={busy}
                title={t('modelMaintenance.reinstall')}
                aria-label={t('modelMaintenance.reinstall')}
                onClick={() => ops.requestReinstall(model)}
              >
                <RefreshCwIcon />
              </Button>
              <DeleteButton model={model} ops={ops} />
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
            disabled={busy || !model.supported}
            onClick={() => void ops.install(model.repo_id)}
          >
            <WrenchIcon />
            {t('modelMaintenance.repair')}
          </Button>
          {maintenance && <DeleteButton model={model} ops={ops} />}
        </>
      ) : (
        <Button
          size="sm"
          variant="outline"
          disabled={busy || !model.supported}
          onClick={() => void ops.install(model.repo_id)}
        >
          <DownloadIcon />
          {t(model.supported ? 'modelMaintenance.download' : 'modelSettings.unavailable')}
        </Button>
      )}
    </>
  );
}
