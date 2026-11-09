import { CheckIcon, ChevronDownIcon } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Spinner } from '@/components/ui/spinner';
import { cn } from '@/lib/utils';
import type { BackendStatus } from '../../../preload/index.d';

export const SETUP_PHASES = ['checking', 'downloading_uv', 'installing_deps', 'verifying'] as const;

interface SetupProgressCardProps {
  status: BackendStatus;
  seconds: number;
  label: string;
  cancelling: boolean;
  onCancel: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3)
    return `${(bytes / 1024 ** 3).toFixed(bytes < 10 * 1024 ** 3 ? 1 : 0)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.max(0, Math.round(bytes / 1024))} KB`;
}

function formatEta(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

/** Phase labels end in an ellipsis for the running step; done and pending steps read as plain items. */
function settledLabel(label: string): string {
  return label.replace(/(?:…|\.\.\.)$/, '');
}

/** The first-run runtime install: one download bar, a step checklist and the raw log on demand. */
export function SetupProgressCard({
  status,
  seconds,
  label,
  cancelling,
  onCancel,
}: SetupProgressCardProps) {
  const { t } = useTranslation();
  const [logOpen, setLogOpen] = useState(false);
  const phaseIndex = status.setupPhase ? SETUP_PHASES.indexOf(status.setupPhase) : 0;
  const installingDeps = status.setupPhase === 'installing_deps';
  const progress = installingDeps ? status.setupProgress : undefined;
  const downloadedBytes = progress?.downloadedBytes ?? 0;
  // Main clears this when a later download starts, even after a "Prepared" count.
  const downloadsComplete = Boolean(progress?.downloadsComplete);
  const downloadPercent = progress?.totalBytes
    ? downloadsComplete
      ? 100
      : Math.min(100, (downloadedBytes / progress.totalBytes) * 100)
    : 0;
  const dependencyCount =
    progress?.installedPackages ?? progress?.preparedPackages ?? progress?.resolvedPackages;
  const transferLive =
    progress?.transferUpdatedAt !== undefined && Date.now() - progress.transferUpdatedAt < 15_000;
  const transferStats = [
    transferLive && progress?.bytesPerSecond
      ? t('bootstrap.download_rate', { rate: formatBytes(progress.bytesPerSecond) })
      : null,
    transferLive && progress?.etaSeconds !== undefined
      ? t('bootstrap.eta', { time: formatEta(progress.etaSeconds) })
      : null,
  ].filter(Boolean);
  const activity = progress?.activePackage
    ? { text: progress.activePackage, mono: true }
    : installingDeps && downloadsComplete
      ? { text: t('bootstrap.downloads_complete'), mono: false }
      : status.logTail.length > 0
        ? { text: status.logTail.at(-1) as string, mono: true }
        : null;

  return (
    <section className="w-full overflow-hidden rounded-2xl border border-border/60 bg-card/60 text-left shadow-sm backdrop-blur-xl">
      {progress?.totalBytes ? (
        <div className="space-y-2 border-b border-border/50 px-4 py-3.5">
          <div
            className="h-2 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-label={label}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(downloadPercent)}
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-300"
              style={{ width: `${downloadPercent}%` }}
            />
          </div>
          {!downloadsComplete ? (
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 font-mono text-xs tabular-nums text-muted-foreground">
              <span className="whitespace-nowrap">
                <span className="text-foreground">{Math.floor(downloadPercent)}%</span>
                {' · '}
                {formatBytes(downloadedBytes)} / ~{formatBytes(progress.totalBytes)}
              </span>
              {transferStats.length > 0 ? (
                <span className="whitespace-nowrap">{transferStats.join(' · ')}</span>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      <ol className="space-y-0.5 p-2" aria-label={label}>
        {SETUP_PHASES.map((phase, index) => {
          const complete = index < phaseIndex;
          const current = index === phaseIndex;
          const phaseLabel = t(`bootstrap.${phase}`);
          return (
            <li
              key={phase}
              aria-current={current ? 'step' : undefined}
              className={cn('flex gap-3 rounded-lg px-2.5 py-2', current && 'bg-muted/50')}
            >
              <span className="flex size-5 shrink-0 items-center justify-center" aria-hidden="true">
                {complete ? (
                  <span className="flex size-5 items-center justify-center rounded-full bg-primary/15 text-primary">
                    <CheckIcon className="size-3" strokeWidth={3} />
                  </span>
                ) : current ? (
                  <Spinner className="size-4 text-primary" />
                ) : (
                  <span className="size-2 rounded-full bg-muted-foreground/30" />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <p
                  className={cn(
                    'text-sm leading-5',
                    current ? 'font-medium text-foreground' : 'text-muted-foreground',
                    !complete && !current && 'text-muted-foreground/70',
                  )}
                >
                  {current ? phaseLabel : settledLabel(phaseLabel)}
                </p>
                {current && activity ? (
                  <p
                    className={cn(
                      'mt-0.5 truncate text-xs text-muted-foreground',
                      activity.mono && 'font-mono text-[11px]',
                    )}
                    title={activity.text}
                    aria-live="polite"
                  >
                    {activity.text}
                  </p>
                ) : null}
                {current && dependencyCount ? (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {t('bootstrap.dependencies_resolved', { count: dependencyCount })}
                  </p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
      <Collapsible
        open={logOpen}
        onOpenChange={setLogOpen}
        className="border-t border-border/50 bg-muted/15"
      >
        <div className="flex items-center gap-3 py-1.5 pr-2 pl-2.5">
          <CollapsibleTrigger
            disabled={status.logTail.length === 0}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50"
          >
            <ChevronDownIcon
              className={cn('size-3.5 transition-transform', logOpen && 'rotate-180')}
              aria-hidden="true"
            />
            {t('backend.details')}
          </CollapsibleTrigger>
          <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
            {t('backend.elapsed', { seconds })}
          </span>
          <Button size="sm" variant="ghost" disabled={cancelling} onClick={onCancel}>
            {t('common.cancel')}
          </Button>
        </div>
        <CollapsibleContent>
          <pre
            className="studio-scrollbar max-h-56 overflow-auto border-t border-border/50 bg-background/40 px-4 py-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-muted-foreground"
            aria-label={t('backend.log_label')}
          >
            {status.logTail.join('\n')}
          </pre>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}
