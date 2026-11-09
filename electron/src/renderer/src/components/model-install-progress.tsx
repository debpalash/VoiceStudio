import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CheckIcon,
  CircleSlashIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  TriangleAlertIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { fmtBytes } from '@shared/components/settings/models/format';
import { formatInstallEta, type InstallView } from '@/lib/model-install-state';

export const INSTALL_NEXT_STEP: Record<NonNullable<InstallView['problem']>, string> = {
  disk: 'modelInstall.nextDisk',
  gated: 'modelInstall.nextGated',
  mirror_gated: 'modelMaintenance.mirrorGatedAccess',
  network: 'modelInstall.nextNetwork',
  memory: 'modelInstall.nextMemory',
};

/** The localised headline for an install stage ("Downloading · 42%"). */
export function useInstallHeadline() {
  const { t } = useTranslation();
  return (view: InstallView) =>
    view.stage === 'downloading' && view.pct !== null
      ? t('modelInstall.downloadingPct', { pct: view.pct })
      : t(`modelInstall.${view.stage}`);
}

/** Bytes, speed and remaining time, only for the parts the backend reported. */
export function InstallFacts({ view, className }: { view: InstallView; className?: string }) {
  const { t, i18n } = useTranslation();
  if (view.stage !== 'downloading') return null;
  const eta = formatInstallEta(view.etaSeconds, i18n?.language);
  const facts = [
    view.totalBytes
      ? t('modelInstall.bytes', {
          done: fmtBytes(view.bytesDone),
          total: fmtBytes(view.totalBytes),
        })
      : view.bytesDone > 0
        ? fmtBytes(view.bytesDone)
        : null,
    view.rate ? t('modelInstall.speed', { rate: fmtBytes(view.rate) }) : null,
    eta ? t('modelInstall.eta', { time: eta }) : null,
  ].filter(Boolean);
  if (!facts.length) return null;
  return (
    <span className={cn('text-muted-foreground tabular-nums', className)}>{facts.join(' · ')}</span>
  );
}

/** Determinate once the size is known; a short pulsing stub before that. */
export function InstallProgressBar({ view, label }: { view: InstallView; label: string }) {
  const indeterminate = view.pct === null || view.stage === 'finishing';
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : (view.pct ?? undefined)}
      aria-valuetext={label}
      className="h-1.5 overflow-hidden rounded-full bg-muted"
    >
      <div
        className={cn(
          'h-full rounded-full transition-[width] duration-300 motion-reduce:transition-none',
          view.stage === 'cancelling' ? 'bg-muted-foreground/50' : 'bg-primary',
          indeterminate && 'animate-pulse motion-reduce:animate-none',
        )}
        style={{ width: `${indeterminate ? (view.stage === 'finishing' ? 100 : 8) : view.pct}%` }}
      />
    </div>
  );
}

/**
 * Seconds left of a retry cooldown, counted down locally: the job list stops
 * polling once nothing is downloading, so the server value would freeze.
 */
export function useRetryCountdown(seconds: number) {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    setLeft(seconds);
    if (seconds <= 0) return;
    const deadline = Date.now() + seconds * 1000;
    const timer = setInterval(() => {
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setLeft(remaining);
      if (!remaining) clearInterval(timer);
    }, 1000);
    return () => clearInterval(timer);
  }, [seconds]);
  return left;
}

/** Retry, disabled with a live "Retry in Ns" while the backend cools down. */
export function RetryButton({
  seconds,
  disabled = false,
  onClick,
  size = 'xs',
  variant = 'outline',
}: {
  seconds: number;
  disabled?: boolean;
  onClick: () => void;
  size?: 'xs' | 'sm';
  variant?: 'outline' | 'default';
}) {
  const { t } = useTranslation();
  const left = useRetryCountdown(seconds);
  return (
    <Button
      type="button"
      size={size}
      variant={variant}
      disabled={disabled || left > 0}
      onClick={onClick}
    >
      <RefreshCwIcon />
      {left > 0 ? t('modelMaintenance.retryAfter', { seconds: left }) : t('common.retry')}
    </Button>
  );
}

function RecoveryButton({ view }: { view: InstallView }) {
  const { t } = useTranslation();
  if (!view.recovery) return null;
  const { to, label } = view.recovery;
  return (
    <Button
      type="button"
      size="xs"
      variant="ghost"
      // Loaded on click so feature prompts never pull the route tree into
      // their own module graph.
      onClick={() => void import('@/router').then(({ router }) => router.navigate({ to }))}
    >
      <WrenchIcon />
      {t(label)}
    </Button>
  );
}

export interface InstallProgressProps {
  view: InstallView;
  /** The model being installed, when the surrounding UI does not name it. */
  name?: string;
  onCancel?: () => void;
  onRetry?: () => void;
  onDismiss?: () => void;
  busy?: boolean;
  className?: string;
}

/**
 * Every download/install state in one block: progress with bytes, speed and
 * ETA while it runs; Cancel; a calm "cancelled" line; and for a failure a
 * one-line reason, the exact next step, Retry and where to fix it.
 */
export function InstallProgress({
  view,
  name,
  onCancel,
  onRetry,
  onDismiss,
  busy = false,
  className,
}: InstallProgressProps) {
  const { t } = useTranslation();
  const headline = useInstallHeadline()(view);
  const title = name ? `${name} · ${headline}` : headline;

  if (view.stage === 'failed') {
    const next = view.problem ? INSTALL_NEXT_STEP[view.problem] : null;
    return (
      <div className={cn('space-y-1.5 text-xs', className)}>
        <p role="alert" className="flex min-w-0 items-start gap-1.5 text-destructive">
          <TriangleAlertIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
          <span className="min-w-0 flex-1 break-words">
            <span className="font-medium" title={next ? (view.detail ?? undefined) : undefined}>
              {title}
            </span>
            {view.reason && !next && (
              <span className="text-muted-foreground" title={view.detail ?? undefined}>
                {' — '}
                {view.reason}
              </span>
            )}
          </span>
        </p>
        {next && <p className="text-muted-foreground">{t(next)}</p>}
        <div className="flex flex-wrap gap-1">
          {onRetry && <RetryButton seconds={view.retryAfter} disabled={busy} onClick={onRetry} />}
          <RecoveryButton view={view} />
          {onDismiss && (
            <Button type="button" size="xs" variant="ghost" onClick={onDismiss}>
              <XIcon />
              {t('common.dismiss')}
            </Button>
          )}
        </div>
      </div>
    );
  }

  if (view.stage === 'cancelled' || view.stage === 'ready') {
    const Icon = view.stage === 'ready' ? CheckIcon : CircleSlashIcon;
    return (
      <div
        role="status"
        className={cn('flex flex-wrap items-center gap-2 text-xs text-muted-foreground', className)}
      >
        <Icon
          aria-hidden="true"
          className={cn('size-3.5 shrink-0', view.stage === 'ready' && 'text-primary')}
        />
        <span className="min-w-0 flex-1">{title}</span>
        {view.stage === 'cancelled' && onRetry && (
          <Button type="button" size="xs" variant="ghost" disabled={busy} onClick={onRetry}>
            <RefreshCwIcon />
            {t('modelInstall.downloadAgain')}
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className={cn('min-w-0 space-y-1.5 text-xs', className)}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        {view.stage !== 'downloading' && (
          <LoaderCircleIcon
            aria-hidden="true"
            className="size-3.5 shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none"
          />
        )}
        <span role="status" className="min-w-0 truncate tabular-nums">
          {title}
        </span>
        <InstallFacts view={view} />
        {onCancel && (
          <Button
            type="button"
            size="xs"
            variant="ghost"
            className="ml-auto"
            disabled={busy || view.stage === 'cancelling'}
            onClick={onCancel}
          >
            <XIcon />
            {t('common.cancel')}
          </Button>
        )}
      </div>
      <InstallProgressBar view={view} label={title} />
    </div>
  );
}
