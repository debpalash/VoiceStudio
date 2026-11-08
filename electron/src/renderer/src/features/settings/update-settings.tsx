import { splitRoundedMinutes } from '@shared/utils/timeFormat';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CheckIcon,
  DownloadIcon,
  RefreshCwIcon,
  RotateCwIcon,
  ShieldCheckIcon,
  SparklesIcon,
  ChevronDownIcon,
  Clock3Icon,
  XIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { InlineMarkdown, MarkdownLite } from '@/components/markdown-lite';
import { appVersion, getBridge } from '@/components/bridge';
import { dubSession, flushDubDraft, useDubSession } from '@/features/dub/dub-session';
import { hasActiveAppWork, useAppActivityCount } from '@/lib/app-activity';
import { useBackendStatus, isBackendReachable } from '@/hooks/use-backend-status';
import { apiJson, describeError } from '@/lib/api/client';
import { formatBytes, updateErrorKey } from '@/lib/update-format';
import { SettingsSection } from './settings-layout';
import type { UpdateChannel, UpdateReleaseInfo, UpdateState } from '../../../../preload/index.d';

// Sections must settle (data, empty or error) rather than load forever when
// the backend or GitHub is slow or down.
const LOAD_TIMEOUT_MS = 12_000;

function withTimeout<T>(promise: Promise<T>, ms = LOAD_TIMEOUT_MS): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

const UPDATE_CHANNELS = ['stable', 'beta'] as const;

const initial: UpdateState = {
  status: 'unsupported',
  currentVersion: appVersion(),
  progress: 0,
};

interface ModelInstallJobs {
  jobs: { state: string }[];
}

interface ChangelogRelease {
  version: string;
  date?: string;
  intro?: string;
  sections?: { title?: string; bullets: string[] }[];
}

interface ChangelogResponse {
  available: boolean;
  releases: ChangelogRelease[];
}

interface BackupState {
  available: boolean;
  latest?: { path: string; created_at: number; size_bytes: number } | null;
}

const serverWorkActive = (models?: ModelInstallJobs, batches?: unknown[]) =>
  Boolean(
    models?.jobs.some((job) =>
      ['queued', 'downloading', 'running', 'installing'].includes(job.state),
    ) || batches?.length,
  );

// No reason means no desktop bridge: the browser UI, which never self-updates.
const unsupportedMessageKey = (reason: UpdateState['unsupportedReason']) =>
  reason === 'development'
    ? 'settings.updater_development'
    : reason === 'package-manager'
      ? 'settings.updater_package_manager'
      : reason === 'store'
        ? 'settings.updater_store'
        : 'settings.updater_desktop';

function formatEta(seconds: number): string {
  const { minutes, seconds: remainder } = splitRoundedMinutes(seconds);
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

export function UpdateSettings() {
  const { t } = useTranslation();
  const updates = getBridge()?.updates;
  const canListReleases = typeof updates?.listReleases === 'function';
  const dub = useDubSession();
  const activityCount = useAppActivityCount();
  const [state, setState] = useState(initial);
  const [action, setAction] = useState(false);
  const channelGroup = useRef<HTMLDivElement>(null);
  const refocusChannel = useRef(false);
  const [expandedRelease, setExpandedRelease] = useState<string | null | undefined>(undefined);
  const watchServerWork = state.status === 'available' || state.status === 'downloaded';
  const modelInstalls = useQuery({
    queryKey: ['model-install-jobs'],
    queryFn: () => apiJson<ModelInstallJobs>('/models/install/status'),
    enabled: watchServerWork,
    refetchInterval: 2_000,
  });
  const batchJobs = useQuery({
    queryKey: ['batch-jobs', 'active'],
    queryFn: () => apiJson<unknown[]>('/batch/jobs?status=active&limit=1'),
    enabled: watchServerWork,
    refetchInterval: 3_000,
  });
  const changelog = useQuery({
    queryKey: ['changelog', 5],
    queryFn: ({ signal }) =>
      apiJson<ChangelogResponse>('/api/settings/changelog?limit_versions=5', {
        signal,
      }),
    staleTime: 300_000,
  });
  const backendUp = isBackendReachable(useBackendStatus().stage);
  const backup = useQuery({
    queryKey: ['db-backup'],
    queryFn: () => withTimeout(apiJson<BackupState>('/api/settings/db-backup')),
    enabled: backendUp,
    retry: 1,
    retryDelay: 1_000,
    staleTime: 300_000,
  });
  const releaseHistory = useQuery({
    queryKey: ['desktop-release-history'],
    queryFn: () => withTimeout(updates!.listReleases()),
    enabled: canListReleases,
    retry: 1,
    retryDelay: 1_000,
    staleTime: 15 * 60_000,
  });
  const releaseRows = (releaseHistory.data || [])
    // Betas are tagged prereleases; only beta users are offered them.
    .filter((release) => state.channel === 'beta' || !release.prerelease)
    .slice()
    .sort((left, right) => right.date.localeCompare(left.date))
    .map((release) => ({
      ...release,
      current: release.version === state.currentVersion,
    }));
  const dubBusy = (value: typeof dub) =>
    Boolean(value.recovery || value.batchProgress) ||
    !['idle', 'editing', 'done'].includes(value.phase);
  const busy =
    activityCount > 0 || dubBusy(dub) || serverWorkActive(modelInstalls.data, batchJobs.data);

  useEffect(() => {
    if (!updates) return;
    let active = true;
    void updates
      .getState()
      .then((value) => active && setState(value))
      .catch((error) => {
        if (active) setState({ ...initial, status: 'error', error: describeError(error) });
      });
    const unsubscribe = updates.onState(setState);
    return () => {
      active = false;
      unsubscribe();
    };
  }, [updates]);

  // Controls are disabled while a switch runs, which drops keyboard focus.
  useEffect(() => {
    if (action || !refocusChannel.current) return;
    refocusChannel.current = false;
    channelGroup.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  }, [action, state.channel]);

  const run = async (operation: () => Promise<UpdateState>) => {
    setAction(true);
    try {
      setState(await operation());
    } catch {
      toast.error(t('update.failed'));
    } finally {
      setAction(false);
    }
  };
  const switchChannel = (channel: UpdateChannel) =>
    updates &&
    void run(async () => {
      const next = await updates.setChannel(channel);
      return next.channel === channel ? updates.check() : next;
    });
  const restart = async () => {
    setAction(true);
    try {
      const [models, batches] = await Promise.all([modelInstalls.refetch(), batchJobs.refetch()]);
      if (
        hasActiveAppWork() ||
        dubBusy(dubSession.state) ||
        serverWorkActive(models.data, batches.data)
      ) {
        toast.info(t('update.busy'));
        return;
      }
      flushDubDraft();
      await updates?.install();
    } catch {
      toast.error(t('update.failed'));
    } finally {
      setAction(false);
    }
  };
  const statusIcon =
    state.status === 'error' ? (
      <AlertTriangleIcon className="size-5 text-destructive" />
    ) : state.status === 'checking' || state.status === 'downloading' ? (
      <RefreshCwIcon className="size-5 animate-spin text-primary motion-reduce:animate-none" />
    ) : state.status === 'available' ? (
      <DownloadIcon className="size-5 text-primary" />
    ) : state.status === 'downloaded' ? (
      <RotateCwIcon className="size-5 text-primary" />
    ) : (
      <CheckCircle2Icon className="size-5 text-success" />
    );
  const downloadDetail =
    state.status === 'downloading' && state.totalBytes
      ? t('update.download_detail', {
          downloaded: formatBytes(state.transferredBytes || 0),
          total: formatBytes(state.totalBytes),
          speed: state.bytesPerSecond ? formatBytes(state.bytesPerSecond) : '—',
          eta: state.etaSeconds === undefined ? '—' : formatEta(state.etaSeconds),
        })
      : null;

  return (
    <>
      <SettingsSection icon={RefreshCwIcon} title={t('updates.tab')}>
        <div className="relative flex flex-col gap-4 overflow-hidden px-4 py-4 @2xl:flex-row @2xl:items-center">
          <div
            className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-linear-to-b from-primary/7 to-transparent"
            aria-hidden="true"
          />
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <span className="relative mt-0.5 shrink-0" aria-hidden="true">
              {statusIcon}
            </span>
            <div className="relative min-w-0 flex-1">
              <p className="text-sm font-medium">
                {state.status === 'available' || state.status === 'downloaded'
                  ? state.status === 'downloaded'
                    ? t('update.ready', { version: state.availableVersion || '' })
                    : t('update.available', { version: state.availableVersion || '' })
                  : state.status === 'unsupported'
                    ? t(unsupportedMessageKey(state.unsupportedReason))
                    : state.status === 'checking'
                      ? t('update.checking')
                      : state.status === 'downloading'
                        ? t('update.downloading', {
                            pct: Math.round(state.progress),
                          })
                        : state.status === 'error'
                          ? t(updateErrorKey(state.error))
                          : t('updates.up_to_date', {
                              version: state.currentVersion,
                            })}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {t('about.version')} {state.currentVersion}
                {state.availableVersion && ` → ${state.availableVersion}`}
              </p>
              {state.status === 'downloading' && (
                <>
                  <div
                    role="progressbar"
                    aria-label={t('update.downloading', {
                      pct: Math.round(state.progress),
                    })}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(state.progress)}
                    className="mt-3 h-1.5 w-full max-w-lg overflow-hidden rounded-full bg-muted"
                  >
                    <div
                      className="h-full rounded-full bg-primary transition-[width] duration-300"
                      style={{ width: `${state.progress}%` }}
                    />
                  </div>
                  {downloadDetail && (
                    <p className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground tabular-nums">
                      <Clock3Icon className="size-3" aria-hidden="true" />
                      {downloadDetail}
                    </p>
                  )}
                </>
              )}
              {state.status === 'error' && state.error && (
                <details className="mt-2 text-xs text-muted-foreground">
                  <summary className="cursor-pointer">{t('common.more_info')}</summary>
                  <p className="mt-1 break-words">{state.error}</p>
                </details>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {(state.status === 'idle' || state.status === 'error') && (
              <Button
                size="sm"
                variant="outline"
                disabled={action}
                onClick={() => updates && void run(() => updates.check())}
              >
                <RefreshCwIcon />
                {t(state.status === 'error' ? 'update.retry' : 'updates.check_now')}
              </Button>
            )}
            {state.status === 'error' && (
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t('update.dismiss')}
                disabled={action}
                onClick={() => updates && void run(() => updates.dismiss())}
              >
                <XIcon />
              </Button>
            )}
            {state.status === 'available' && (
              <Button
                size="sm"
                disabled={action}
                title={t('update.download_hint')}
                onClick={() => updates && void run(() => updates.download())}
              >
                <DownloadIcon />
                {t('update.download')}
              </Button>
            )}
            {state.status === 'downloaded' && (
              <Button
                size="sm"
                disabled={action || busy}
                title={busy ? t('update.busy') : undefined}
                onClick={() => void restart()}
              >
                <RotateCwIcon />
                {t('update.restart')}
              </Button>
            )}
          </div>
        </div>
        {state.channel && (
          <div className="flex flex-col gap-2 px-4 py-3 @2xl:flex-row @2xl:items-center">
            <div className="min-w-0 flex-1">
              <p id="update-channel-label" className="text-sm font-medium">
                {t('update.channel')}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t('update.channel_current', { channel: t(`update.channel_${state.channel}`) })}
              </p>
              {state.channel === 'beta' && (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t('update.channel_beta_hint')}
                </p>
              )}
            </div>
            <div
              ref={channelGroup}
              role="radiogroup"
              aria-labelledby="update-channel-label"
              className="inline-flex shrink-0 rounded-lg border border-border bg-muted/40 p-0.5"
            >
              {UPDATE_CHANNELS.map((channel, index) => {
                const selected = state.channel === channel;
                const locked =
                  action || ['checking', 'downloading', 'downloaded'].includes(state.status);
                return (
                  <button
                    key={channel}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    tabIndex={selected ? 0 : -1}
                    disabled={locked}
                    className={cn(
                      'inline-flex min-w-20 items-center justify-center gap-1.5 rounded-md px-3 py-1 text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60',
                      selected
                        ? 'bg-primary text-primary-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground',
                    )}
                    onClick={() => !selected && switchChannel(channel)}
                    onKeyDown={(event) => {
                      const step =
                        event.key === 'ArrowRight' || event.key === 'ArrowDown'
                          ? 1
                          : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
                            ? -1
                            : 0;
                      if (!step || locked) return;
                      event.preventDefault();
                      const next =
                        UPDATE_CHANNELS[
                          (index + step + UPDATE_CHANNELS.length) % UPDATE_CHANNELS.length
                        ];
                      refocusChannel.current = true;
                      switchChannel(next);
                    }}
                    data-channel={channel}
                  >
                    {selected && <CheckIcon className="size-3.5" aria-hidden="true" />}
                    {t(`update.channel_${channel}`)}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <div className="flex items-center gap-2 px-4 py-2.5 text-[11px] text-muted-foreground">
          <ShieldCheckIcon className="size-3.5 text-success" aria-hidden="true" />
          {t('update.safety')}
        </div>
      </SettingsSection>

      {state.notes && (
        <SettingsSection
          icon={DownloadIcon}
          title={t('updates.notes_for', {
            version: state.availableVersion || '',
          })}
        >
          <MarkdownLite
            text={state.notes}
            className="px-4 py-4 text-sm leading-6 text-muted-foreground"
          />
        </SettingsSection>
      )}

      <SettingsSection icon={ShieldCheckIcon} title={t('updates.backup_line')}>
        {backup.isError && backendUp ? (
          <div className="flex items-center justify-between gap-3 px-4 py-3 text-sm text-muted-foreground">
            <span>{t('updates.backup_load_error')}</span>
            <Button size="sm" variant="ghost" onClick={() => void backup.refetch()}>
              <RefreshCwIcon />
              {t('updates.retry_load')}
            </Button>
          </div>
        ) : (
          <div className="px-4 py-3 text-sm text-muted-foreground">
            {!backendUp
              ? t('updates.backup_needs_runtime')
              : backup.isPending
                ? t('common.loading')
                : backup.data?.available && backup.data.latest
                  ? t('updates.backup_latest', {
                      when: new Date(backup.data.latest.created_at * 1000).toLocaleString(),
                    })
                  : t('updates.backup_none')}
          </div>
        )}
      </SettingsSection>

      {changelog.data?.available && changelog.data.releases.length > 0 && (
        <SettingsSection icon={SparklesIcon} title={t('update.whats_new')}>
          <div className="divide-y divide-border/50">
            {changelog.data.releases.map((release, index) => {
              const open =
                expandedRelease === undefined ? index === 0 : expandedRelease === release.version;
              return (
                <details key={release.version} className="group px-4 py-3" open={open}>
                  <summary
                    className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium"
                    onClick={(event) => {
                      event.preventDefault();
                      setExpandedRelease(open ? null : release.version);
                    }}
                  >
                    <ChevronDownIcon className="size-4 -rotate-90 text-muted-foreground transition-transform group-open:rotate-0" />
                    <span>v{release.version}</span>
                    {release.date && (
                      <time className="ml-auto text-xs font-normal text-muted-foreground">
                        {release.date}
                      </time>
                    )}
                  </summary>
                  <div className="mt-3 space-y-4 pl-6 text-xs leading-5 text-muted-foreground">
                    {release.intro && <MarkdownLite text={release.intro} />}
                    {release.sections?.map((section, sectionIndex) => (
                      <section key={`${section.title || 'notes'}-${sectionIndex}`}>
                        {section.title && (
                          <h3 className="mb-1.5 font-medium text-foreground">{section.title}</h3>
                        )}
                        <ul className="list-disc space-y-1.5 pl-4">
                          {section.bullets.map((bullet, bulletIndex) => (
                            <li key={bulletIndex}>
                              <InlineMarkdown text={bullet} />
                            </li>
                          ))}
                        </ul>
                      </section>
                    ))}
                  </div>
                </details>
              );
            })}
          </div>
        </SettingsSection>
      )}

      {canListReleases && (
        <SettingsSection icon={Clock3Icon} title={t('updates.releases')}>
          {releaseHistory.isPending ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">{t('updates.loading')}</p>
          ) : releaseHistory.isError ? (
            <div className="flex items-center justify-between gap-3 px-4 py-3 text-sm text-muted-foreground">
              <span>{t('updates.load_error')}</span>
              <Button size="sm" variant="ghost" onClick={() => void releaseHistory.refetch()}>
                <RefreshCwIcon />
                {t('updates.retry_load')}
              </Button>
            </div>
          ) : releaseRows.length === 0 ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">{t('updates.none')}</p>
          ) : (
            <div className="divide-y divide-border/50">
              {releaseRows.map((release: UpdateReleaseInfo & { current: boolean }) => (
                <details key={`${release.version}-${release.date}`} className="group px-4 py-3">
                  <summary className="flex cursor-pointer list-none items-center gap-2 text-sm outline-none">
                    <ChevronDownIcon className="size-4 -rotate-90 text-muted-foreground transition-transform group-open:rotate-0" />
                    <span className="font-medium">v{release.version}</span>
                    {release.current && (
                      <span className="rounded-full bg-primary/12 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                        {t('updates.current')}
                      </span>
                    )}
                    <time className="ml-auto text-xs text-muted-foreground">
                      {release.date ? new Date(release.date).toLocaleDateString() : ''}
                    </time>
                  </summary>
                  <div className="mt-2 space-y-2 pl-6 text-xs leading-5 text-muted-foreground">
                    {release.name && release.name !== `v${release.version}` && (
                      <p className="font-medium text-foreground">{release.name}</p>
                    )}
                    {release.notes && <MarkdownLite text={release.notes} />}
                  </div>
                </details>
              ))}
            </div>
          )}
        </SettingsSection>
      )}
    </>
  );
}
