import { splitRoundedMinutes } from '@shared/utils/timeFormat';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
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
import { MarkdownLite } from '@/components/markdown-lite';
import { appVersion, getBridge } from '@/components/bridge';
import { dubSession, flushDubDraft, useDubSession } from '@/features/dub/dub-session';
import { hasActiveAppWork, useAppActivityCount } from '@/lib/app-activity';
import { apiJson, describeError } from '@/lib/api/client';
import { SettingsSection } from './settings-layout';
import type { UpdateReleaseInfo, UpdateState } from '../../../../preload/index.d';

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

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${Math.max(0, Math.round(bytes))} B`;
}

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
  const backup = useQuery({
    queryKey: ['db-backup'],
    queryFn: ({ signal }) => apiJson<BackupState>('/api/settings/db-backup', { signal }),
    staleTime: 300_000,
  });
  const releaseHistory = useQuery({
    queryKey: ['desktop-release-history'],
    queryFn: () => updates!.listReleases(),
    enabled: canListReleases,
    staleTime: 15 * 60_000,
  });
  const releaseRows = (releaseHistory.data || [])
    // Updates follow tagged releases only, so the history lists those.
    .filter((release) => !release.prerelease)
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
                    ? t('settings.updater_desktop')
                    : state.status === 'checking'
                      ? t('update.checking')
                      : state.status === 'downloading'
                        ? t('update.downloading', {
                            pct: Math.round(state.progress),
                          })
                        : state.status === 'error'
                          ? t('update.failed')
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
        <div className="px-4 py-3 text-sm text-muted-foreground">
          {backup.isPending
            ? t('common.loading')
            : backup.data?.available && backup.data.latest
              ? t('updates.backup_latest', {
                  when: new Date(backup.data.latest.created_at * 1000).toLocaleString(),
                })
              : t('updates.backup_none')}
        </div>
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
                    {release.intro && (
                      <p className="font-medium text-foreground">
                        {release.intro.replaceAll('**', '')}
                      </p>
                    )}
                    {release.sections?.map((section, sectionIndex) => (
                      <section key={`${section.title || 'notes'}-${sectionIndex}`}>
                        {section.title && (
                          <h3 className="mb-1.5 font-medium text-foreground">{section.title}</h3>
                        )}
                        <ul className="list-disc space-y-1.5 pl-4">
                          {section.bullets.map((bullet, bulletIndex) => (
                            <li key={bulletIndex}>{bullet}</li>
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
