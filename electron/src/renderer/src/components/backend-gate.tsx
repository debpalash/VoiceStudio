import { SetupGate } from './setup-gate';
import { ReportBug } from './report-bug';
import { CrashDetails } from './crash-details';
import {
  CheckIcon,
  ChevronDownIcon,
  CircleIcon,
  RotateCcwIcon,
  DownloadIcon,
  FolderOpenIcon,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentFixButton } from '@/components/agent-fix-button';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { ConfirmDialog } from '@/features/clone/confirm-dialog';
import { useBackendStatus } from '@/hooks/use-backend-status';
import i18n, { APP_LANGUAGE_ITEMS, APP_LANGUAGES, setAppLanguage, type AppLocale } from '@/i18n';
import { brandIcon } from '@/lib/brand';
import { cn } from '@/lib/utils';
import type { RuntimeRegion } from '../../../preload/index.d';
import { getBridge } from './bridge';

interface BackendGateProps {
  children: ReactNode;
  repairDock?: ReactNode;
}

const RETRYABLE = new Set(['crashed', 'failed', 'port_in_use']);
const SETUP_PHASES = ['checking', 'downloading_uv', 'installing_deps', 'verifying'] as const;

function useElapsedSeconds(elapsedMs: number, running: boolean): number {
  // The bridge only pushes a status on change; tick locally so the counter moves.
  const [seconds, setSeconds] = useState(() => Math.floor(elapsedMs / 1000));
  useEffect(() => {
    const startedAt = Date.now() - elapsedMs;
    setSeconds(Math.floor(elapsedMs / 1000));
    if (!running) return;
    const id = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, [elapsedMs, running]);
  return seconds;
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
  return minutes > 0
    ? `${minutes}:${String(remainder).padStart(2, '0')}`
    : `0:${String(remainder).padStart(2, '0')}`;
}

/**
 * Holds the page until the local backend answers. The shell (top bar, rail,
 * footer) stays mounted around it, so the splash-to-page swap shifts nothing.
 */
export function BackendGate({ children, repairDock }: BackendGateProps) {
  const { t } = useTranslation();
  const status = useBackendStatus();
  const [reachedReady, setReachedReady] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [choosingLocation, setChoosingLocation] = useState(false);
  const [choosingRegion, setChoosingRegion] = useState(false);
  const [cleanConfirmOpen, setCleanConfirmOpen] = useState(false);
  const setup = status.stage === 'setup_required';
  const regionItems = (['auto', 'global', 'china', 'russia', 'restricted'] as const).map(
    (region) => ({
      value: region,
      label: t(region === 'auto' ? 'bootstrap.auto_detect' : `bootstrap.region_${region}`),
    }),
  );
  const installing = status.stage === 'installing';
  const setupFailed = setup && Boolean(status.message);
  const running = status.stage === 'starting' || status.stage === 'attaching' || installing;
  const seconds = useElapsedSeconds(status.elapsedMs, running);
  const setupPhaseIndex = status.setupPhase ? SETUP_PHASES.indexOf(status.setupPhase) : 0;
  const latestActivity = installing ? status.logTail.at(-1) : undefined;
  const progress = status.setupProgress;
  const downloadedBytes = progress?.downloadedBytes ?? 0;
  const remainingBytes = Math.max(0, (progress?.totalBytes ?? 0) - downloadedBytes);
  const downloadsComplete = Boolean(
    progress?.downloadsComplete || progress?.preparedPackages || progress?.installedPackages,
  );
  const downloadPercent = progress?.totalBytes
    ? downloadsComplete
      ? 100
      : Math.min(100, (downloadedBytes / progress.totalBytes) * 100)
    : 0;
  const dependencyCount =
    progress?.installedPackages ?? progress?.preparedPackages ?? progress?.resolvedPackages;
  const transferLive =
    progress?.transferUpdatedAt !== undefined && Date.now() - progress.transferUpdatedAt < 15_000;

  const recovering = reachedReady && status.stage === 'failed' && status.managed;

  useEffect(() => {
    setRestarting(false);
  }, [status.stage]);
  useEffect(() => {
    if (status.stage === 'ready') setReachedReady(true);
  }, [status.stage]);

  if (status.stage === 'ready')
    return (
      <div className="contents">
        <SetupGate>{children}</SetupGate>
      </div>
    );

  const failed = RETRYABLE.has(status.stage);
  const stageText =
    installing && status.setupPhase
      ? t(`bootstrap.${status.setupPhase}`)
      : setup && status.runtimeInterrupted
        ? t('modelMaintenance.repairDescription')
        : status.stage === 'port_in_use'
          ? t('backend.port_in_use', { port: status.port })
          : t(`backend.${status.stage === 'idle' ? 'starting' : status.stage}`);

  const retry = async () => {
    setRestarting(true);
    try {
      await getBridge()?.backend.restart();
    } catch {
      setRestarting(false);
    }
  };

  const runSetup = async (clean = false) => {
    setRestarting(true);
    try {
      if (clean) await getBridge()?.backend.cleanSetupRuntime();
      else await getBridge()?.backend.setupRuntime();
    } catch {
      /* Main publishes setup failure and logs. */
    } finally {
      setRestarting(false);
    }
  };

  const gate = (
    <div
      className={cn(
        'absolute inset-0 flex flex-col overflow-hidden text-foreground',
        recovering && 'z-30 bg-background/90 backdrop-blur-sm',
      )}
    >
      <header className="workspace-titlebar flex shrink-0 items-center gap-2 border-b border-border/50 px-5">
        <img src={brandIcon} alt="" className="size-6 shrink-0" />
        <span className="whitespace-nowrap text-sm font-medium tracking-tight">
          {t('app.name')}
        </span>
      </header>
      <div
        data-testid="backend-gate-scroll"
        className="studio-scrollbar min-h-0 flex-1 overflow-y-auto p-6"
      >
        <div className="mx-auto flex min-h-full w-full max-w-lg shrink-0 flex-col items-center justify-start gap-4 py-[clamp(1.5rem,8vh,5rem)] text-center">
          {setup ? (
            <DownloadIcon className="size-8 text-primary" aria-hidden="true" />
          ) : failed ? (
            <div
              className="flex size-10 items-center justify-center rounded-full bg-destructive/10 text-lg font-semibold text-destructive"
              aria-hidden="true"
            >
              !
            </div>
          ) : (
            <Spinner className="size-8 text-primary" aria-label={t('common.loading')} />
          )}
          <div className="space-y-1">
            <p className={cn('text-base font-medium', failed && 'text-destructive')}>{stageText}</p>
            {(failed || setup) && status.message ? (
              <p className="text-sm text-muted-foreground">
                {status.message === 'VOICESTUDIO_PROXY_BYPASS_UNSUPPORTED'
                  ? t('backend.proxy_bypass_help')
                  : setup
                    ? t(status.setupIssue ? `backend.setup_${status.setupIssue}` : 'backend.failed')
                    : status.message}
              </p>
            ) : null}
            {running ? (
              <p className="font-mono text-xs text-muted-foreground tabular-nums">
                {t('backend.elapsed', { seconds })}
              </p>
            ) : null}
          </div>
          {installing ? (
            <div className="w-full rounded-2xl border border-border/60 bg-card/55 p-3 shadow-sm backdrop-blur-xl">
              <ol className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-1" aria-label={stageText}>
                {SETUP_PHASES.map((phase, index) => {
                  const complete = index < setupPhaseIndex;
                  const current = index === setupPhaseIndex;
                  return (
                    <li key={phase} className="min-w-0">
                      <div
                        className={cn(
                          'mb-2 h-1 rounded-full bg-border/70 transition-colors',
                          (complete || current) && 'bg-primary',
                        )}
                      />
                      <div className="flex flex-col items-center gap-1 px-1">
                        {complete ? (
                          <CheckIcon className="size-4 text-primary" aria-hidden="true" />
                        ) : current ? (
                          <Spinner className="size-4 text-primary" aria-hidden="true" />
                        ) : (
                          <CircleIcon
                            className="size-3.5 text-muted-foreground/50"
                            aria-hidden="true"
                          />
                        )}
                        <span
                          className={cn(
                            'text-xs leading-snug text-muted-foreground',
                            current && 'font-medium text-foreground',
                          )}
                        >
                          {t(`bootstrap.${phase}`)}
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ol>
              {status.setupPhase === 'installing_deps' && progress ? (
                <div className="mt-4 border-t border-border/50 pt-3 text-left">
                  {progress.totalBytes ? (
                    <>
                      <div
                        className="h-1.5 overflow-hidden rounded-full bg-muted"
                        role="progressbar"
                        aria-label={stageText}
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
                        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px] tabular-nums text-muted-foreground sm:grid-cols-4">
                          <span>
                            {formatBytes(downloadedBytes)} / ~{formatBytes(progress.totalBytes)}
                          </span>
                          <span>
                            {t('bootstrap.remaining_amount', {
                              amount: formatBytes(remainingBytes),
                            })}
                          </span>
                          <span>
                            {transferLive && progress.bytesPerSecond
                              ? t('bootstrap.download_rate', {
                                  rate: formatBytes(progress.bytesPerSecond),
                                })
                              : '—'}
                          </span>
                          <span>
                            {transferLive && progress.etaSeconds !== undefined
                              ? t('bootstrap.eta', { time: formatEta(progress.etaSeconds) })
                              : '—'}
                          </span>
                        </div>
                      ) : null}
                    </>
                  ) : null}
                  {dependencyCount ? (
                    <p className="mt-2 text-xs text-muted-foreground">
                      {t('bootstrap.dependencies_resolved', { count: dependencyCount })}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {status.setupPhase === 'installing_deps' && progress?.activePackage ? (
                <p className="mt-3 flex min-w-0 items-center gap-2 rounded-lg bg-background/45 px-3 py-2 text-left font-mono text-[11px] text-muted-foreground">
                  <Spinner className="size-3 shrink-0 text-primary" aria-hidden="true" />
                  <span className="truncate">{progress.activePackage}</span>
                </p>
              ) : status.setupPhase === 'installing_deps' && downloadsComplete ? (
                <p className="mt-3 flex min-w-0 items-center gap-2 rounded-lg bg-background/45 px-3 py-2 text-left text-xs text-muted-foreground">
                  <Spinner className="size-3 shrink-0 text-primary" aria-hidden="true" />
                  <span>{t('bootstrap.downloads_complete')}</span>
                </p>
              ) : latestActivity ? (
                <p
                  className="mt-3 truncate rounded-lg bg-background/45 px-3 py-2 text-left font-mono text-[11px] text-muted-foreground"
                  title={latestActivity}
                  aria-live="polite"
                >
                  {latestActivity}
                </p>
              ) : null}
            </div>
          ) : null}
          {setup ? (
            <>
              <p className="max-w-sm text-sm text-muted-foreground">{t('backend.setup_hint')}</p>
              <div className="grid w-full grid-cols-2 gap-2">
                <label className="space-y-1 text-left text-xs text-muted-foreground">
                  <span>{t('settings.language')}</span>
                  <Select
                    items={APP_LANGUAGE_ITEMS}
                    value={i18n.resolvedLanguage || i18n.language}
                    onValueChange={(value) => void setAppLanguage(value as AppLocale)}
                  >
                    <SelectTrigger
                      className="w-full bg-muted/20"
                      aria-label={t('settings.language')}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent align="start">
                      {APP_LANGUAGES.map(({ code, label }) => (
                        <SelectItem key={code} value={code}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
                <label className="space-y-1 text-left text-xs text-muted-foreground">
                  <span>{t('firstrun.region_label')}</span>
                  <Select
                    items={regionItems}
                    value={status.runtimeRegion || 'auto'}
                    disabled={choosingRegion || restarting}
                    onValueChange={async (value) => {
                      setChoosingRegion(true);
                      try {
                        await getBridge()?.backend.setRuntimeRegion(value as RuntimeRegion);
                      } finally {
                        setChoosingRegion(false);
                      }
                    }}
                  >
                    <SelectTrigger
                      className="w-full bg-muted/20"
                      aria-label={t('firstrun.region_label')}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent align="end">
                      {regionItems.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
              </div>
              {status.runtimePath && (
                <div className="w-full rounded-xl border border-border/60 bg-muted/20 p-3 text-left">
                  <div className="flex items-start gap-3">
                    <FolderOpenIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{t('firstrun.env_dir')}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {t('firstrun.env_dir_desc')}
                      </p>
                      <p
                        className="mt-2 truncate font-mono text-xs text-muted-foreground"
                        title={status.runtimePath}
                      >
                        {status.runtimePath}
                      </p>
                    </div>
                  </div>
                  <div className="mt-3 flex justify-end gap-2">
                    {status.runtimeCustom && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={choosingLocation || restarting}
                        onClick={() => {
                          void getBridge()
                            ?.backend.useDefaultRuntimeLocation()
                            .catch(() => {
                              /* The setup screen remains retryable if IPC is interrupted. */
                            });
                        }}
                      >
                        {t('firstrun.reset_default')}
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={choosingLocation || restarting}
                      onClick={async () => {
                        setChoosingLocation(true);
                        try {
                          await getBridge()?.backend.chooseRuntimeLocation(t('firstrun.env_dir'));
                        } catch {
                          /* The setup screen remains retryable if IPC is interrupted. */
                        } finally {
                          setChoosingLocation(false);
                        }
                      }}
                    >
                      {t('firstrun.change')}
                    </Button>
                  </div>
                </div>
              )}
              <Button disabled={restarting || choosingLocation} onClick={() => void runSetup()}>
                {status.runtimeInterrupted
                  ? t('common.resume')
                  : setupFailed
                    ? t('backend.retry')
                    : t('backend.setup_required')}
              </Button>
              {setupFailed ? (
                <Button
                  variant="outline"
                  disabled={restarting || choosingLocation}
                  onClick={() => setCleanConfirmOpen(true)}
                >
                  <RotateCcwIcon data-icon="inline-start" />
                  {t('bootstrap.clean_retry')}
                </Button>
              ) : null}
            </>
          ) : null}
          {installing ? (
            <Button variant="outline" onClick={retry}>
              {t('common.cancel')}
            </Button>
          ) : null}
          {running ? (
            <p className="max-w-sm text-xs text-muted-foreground">{t('backend.first_run_hint')}</p>
          ) : null}
          {failed ? (
            <div className="flex flex-wrap justify-center gap-2">
              <Button onClick={retry} disabled={restarting}>
                <RotateCcwIcon data-icon="inline-start" />
                {t('backend.retry')}
              </Button>
              {status.remote && (
                <Button
                  variant="outline"
                  disabled={restarting}
                  onClick={async () => {
                    setRestarting(true);
                    try {
                      await getBridge()?.backend.useLocal();
                    } catch {
                      setRestarting(false);
                    }
                  }}
                >
                  {t('settings.remote_backend_use_local')}
                </Button>
              )}
              <AgentFixButton
                request={`Restore the VoiceStudio local backend. Current stage: ${status.stage}. ${status.message || ''} Inspect Electron status, restart or resume runtime setup as needed, wait until the backend is ready, and verify health. Do not clean reinstall or change user consent.`}
              />
            </div>
          ) : null}
          {setupFailed ? (
            <AgentFixButton
              request={`Resume and repair the interrupted VoiceStudio runtime setup. Current issue: ${status.setupIssue || 'unknown'}. ${status.message || ''} Use the Electron runtime setup control, wait for completion, and verify backend health. Do not clean reinstall or change user consent.`}
            />
          ) : null}
          {status.logTail.length > 0 ? (
            <Collapsible open={logOpen} onOpenChange={setLogOpen} className="w-full">
              <CollapsibleTrigger className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
                <ChevronDownIcon
                  className={cn('size-3.5 transition-transform', logOpen && 'rotate-180')}
                  aria-hidden="true"
                />
                {logOpen ? t('backend.hide_log') : t('backend.show_log')}
              </CollapsibleTrigger>
              <CollapsibleContent>
                <pre
                  className="mt-2 max-h-56 overflow-auto rounded-lg border bg-card p-3 text-left font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-muted-foreground"
                  aria-label={t('backend.log_label')}
                >
                  {status.logTail.join('\n')}
                </pre>
              </CollapsibleContent>
            </Collapsible>
          ) : null}
          {failed && <ReportBug error={status.message} />}
          {failed && <CrashDetails />}
          <ConfirmDialog
            open={cleanConfirmOpen}
            onOpenChange={setCleanConfirmOpen}
            title={t('bootstrap.clean_retry')}
            description={t('bootstrap.clean_retry_confirm')}
            confirmLabel={t('bootstrap.clean_retry')}
            onConfirm={() => runSetup(true)}
          />
        </div>
      </div>
      {!recovering && repairDock}
    </div>
  );
  if (recovering)
    return (
      <div className="contents">
        <SetupGate>{children}</SetupGate>
        {gate}
      </div>
    );
  return gate;
}
