import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import {
  CheckIcon,
  ChevronDownIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { ExternalLink } from '@/components/external-link';
import { INSTALL_NEXT_STEP, RetryButton } from '@/components/model-install-progress';
import type { CatalogueModel } from '../model-catalogue-query';
import type { ModelOps } from './model-ops';
import type { JobInfo } from './model-job';

/** Gated-weights help (request access, accept terms, add a token, re-check). */
export function ModelGatedAccess({
  model,
  ops,
  info,
}: {
  model: CatalogueModel;
  ops: ModelOps;
  info: JobInfo;
}) {
  const { t } = useTranslation();
  if (!(model.gated || info.gatedFailure) || model.installed) return null;
  const access = ops.accessStatus[model.repo_id];
  const checking = ops.checkingAccess === model.repo_id;
  return (
    <details className="group/access order-last basis-full rounded-lg border border-warning/20 bg-warning/5 text-xs @2xl:max-w-2xl">
      <summary
        role={info.gatedFailure || access?.ready === false ? 'alert' : 'status'}
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
              className={buttonVariants({ variant: 'outline', size: 'sm' })}
            >
              <WrenchIcon />
              {t('modelMaintenance.addHfToken')}
            </Link>
          )}
          <Button
            size="sm"
            variant="ghost"
            disabled={checking}
            onClick={() => void ops.checkAccess(model)}
          >
            {checking ? (
              <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
            ) : access?.ready ? (
              <CheckIcon />
            ) : (
              <RefreshCwIcon />
            )}
            {t('modelMaintenance.checkAccess')}
          </Button>
          {info.gatedFailure && (
            <RetryButton
              size="sm"
              variant="default"
              seconds={info.retryAfter}
              disabled={Boolean(ops.pending)}
              onClick={() => void ops.install(model.repo_id)}
            />
          )}
        </div>
        {info.gatedFailure && info.job?.error && (
          <details className="mt-2 text-muted-foreground">
            <summary className="cursor-pointer">{t('common.details')}</summary>
            <p className="mt-1 break-words">{info.job.error}</p>
          </details>
        )}
      </div>
    </details>
  );
}

/** A failed (non-gated) download: reason, retry, where to fix it, dismiss. */
export function ModelFailure({
  model,
  ops,
  info,
}: {
  model: CatalogueModel;
  ops: ModelOps;
  info: JobInfo;
}) {
  const { t } = useTranslation();
  if (!info.failed || info.gatedFailure) return null;
  return (
    <details className="basis-full rounded-lg border border-destructive/15 bg-destructive/5 px-3 py-2 text-xs @2xl:max-w-xl">
      <summary className="cursor-pointer font-medium text-destructive">
        {t('modelInstall.failed')}
      </summary>
      <p role="alert" className="mt-2 break-words text-muted-foreground">
        {info.view?.problem
          ? t(INSTALL_NEXT_STEP[info.view.problem])
          : info.view?.reason || t('modelMaintenance.failed')}
      </p>
      {info.view?.detail && info.view.detail !== info.view.reason && (
        <details className="mt-1 text-muted-foreground">
          <summary className="cursor-pointer">{t('common.details')}</summary>
          <p className="mt-1 break-words whitespace-pre-wrap">{info.view.detail}</p>
        </details>
      )}
      <div className="mt-2 flex flex-wrap gap-1">
        <RetryButton
          seconds={info.retryAfter}
          disabled={Boolean(ops.pending)}
          onClick={() => void ops.install(model.repo_id)}
        />
        {info.recovery && (
          <Link to={info.recovery.to} className={buttonVariants({ variant: 'ghost', size: 'xs' })}>
            <WrenchIcon />
            {t(info.recovery.label)}
          </Link>
        )}
        <Button size="xs" variant="ghost" onClick={() => ops.dismiss(model.repo_id)}>
          <XIcon />
          {t('common.dismiss')}
        </Button>
      </div>
    </details>
  );
}
