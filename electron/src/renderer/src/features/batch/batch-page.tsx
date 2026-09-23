import {
  ActivityIcon,
  AlertCircleIcon,
  CheckCircle2Icon,
  CircleIcon,
  Clock3Icon,
  DownloadIcon,
  FilmIcon,
  LanguagesIcon,
  LayersIcon,
  ListVideoIcon,
  RefreshCwIcon,
  SquareIcon,
  Trash2Icon,
  Volume2Icon,
  WandSparklesIcon,
} from 'lucide-react';
import { SecondarySidebar } from '@/components/workspace-sidebar';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { PipelineFailure } from '@/components/pipeline-failure';
import { EngineNotice } from '@/components/engine-notice';
import { WatchFolder } from './watch-folder';
import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { UploadIcon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { LanguagePicker } from '@/features/clone/language-picker';
import { Switch } from '@/components/ui/switch';
import { useProfiles } from '@/hooks/use-profiles';
import { useTtsReadiness } from '@/hooks/use-tts-readiness';
import { getBridge } from '@/components/bridge';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { saveExport } from '@/lib/export-history';
import { LANG_CODES } from '../../../../../../frontend/src/utils/languages';
import { PRESETS } from '../../../../../../frontend/src/utils/constants';
import type { BatchJob } from '../../../../../../frontend/src/api/batch-types';
import { generationFailureMessage } from '../../../../../../frontend/src/utils/generationFailureMessage';
import { enqueueVideos } from './enqueue';
import { useTranslationEngines } from '@/features/settings/translation-settings';
const languageOptions = LANG_CODES.map((item) => item.label);
export function BatchPage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const profiles = useProfiles();
  const ttsBlocker = useTtsReadiness('batch');
  const translationEngines = useTranslationEngines();
  const translationEngine = translationEngines.data?.engines.find(
    (engine) => engine.id === translationEngines.data?.active,
  );
  const input = useRef<HTMLInputElement>(null);
  const uploading = useRef(false);
  const [files, setFiles] = useState<File[]>([]);
  const [langs, setLangs] = useState(['Spanish']);
  const [voice, setVoice] = useState('');
  const [preserve, setPreserve] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState('active');
  const [deleting, setDeleting] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const addFiles = (selected: File[]) =>
    setFiles((current) => [
      ...current,
      ...selected.filter(
        (file) => file.type.startsWith('video/') || /\.(mkv|mov|mp4|webm)$/i.test(file.name),
      ),
    ]);
  const jobs = useQuery({
    queryKey: ['batch-jobs', tab],
    queryFn: ({ signal }) =>
      apiJson<BatchJob[]>(
        '/batch/jobs?' +
          new URLSearchParams({
            status: tab === 'failed' ? 'retryable' : tab,
            limit: '100',
          }),
        { signal },
      ),
    refetchInterval: tab === 'done' ? false : 3000,
  });
  const submit = async () => {
    if (uploading.current || ttsBlocker !== null || !files.length || !langs.length) return;
    uploading.current = true;
    setBusy(true);
    setError(null);
    let firstFailure: string | null = null;
    try {
      const failed = await enqueueVideos(
        files,
        langs.map((label) => LANG_CODES.find((item) => item.label === label)!.code),
        voice,
        preserve,
        (file, cause) => {
          firstFailure ||= t('batch.enqueue_failed', {
            name: file.name,
            message: describeError(cause),
          });
        },
      );
      setFiles(failed);
      setError(firstFailure);
      setTab('active');
      await client.invalidateQueries({ queryKey: ['batch-jobs'] });
    } finally {
      uploading.current = false;
      setBusy(false);
    }
  };
  const act = async (job: BatchJob, remove: boolean) => {
    if (acting) return;
    setActing(job.id);
    setError(null);
    try {
      await apiJson('/batch/jobs/' + encodeURIComponent(job.id) + (remove ? '' : '/cancel'), {
        method: remove ? 'DELETE' : 'POST',
      });
      setDeleting(null);
      await client.invalidateQueries({ queryKey: ['batch-jobs'] });
    } catch (cause) {
      setError(
        t(remove ? 'batch.delete_failed' : 'batch.cancel_failed', {
          message: describeError(cause),
        }),
      );
    } finally {
      setActing(null);
    }
  };
  const recover = async (job: BatchJob) => {
    if (acting) return;
    setActing(job.id);
    setError(null);
    try {
      if (job.setup_required?.kind === 'argos_packs') {
        await apiJson('/engines/translation/argos/packs/install', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source_lang: job.setup_required.source_lang,
            target_langs: job.setup_required.target_langs,
          }),
        });
      }
      await apiJson('/batch/jobs/' + encodeURIComponent(job.id) + '/retry', { method: 'POST' });
      setTab('active');
      await client.invalidateQueries({ queryKey: ['batch-jobs'] });
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setActing(null);
    }
  };
  const download = async (job: BatchJob, lang: string) => {
    setError(null);
    const url = apiPath(
      '/batch/download/' + encodeURIComponent(job.id) + '/' + encodeURIComponent(lang),
    );
    try {
      const bridge = getBridge();
      if (bridge) await saveExport(url, job.filename.replace(/\.[^.]+$/, '') + '_' + lang + '.mp4');
      else {
        const link = document.createElement('a');
        link.href = url;
        link.download = '';
        link.click();
      }
    } catch (cause) {
      setError(t('clone.download_failed', { message: describeError(cause) }));
    }
  };
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('nav.batch_dub')}</h1>
      </WorkspaceHeader>
      <div className="flex min-h-0 flex-1 @max-[40rem]:flex-col">
        <SecondarySidebar
          title={t('nav.batch_dub')}
          icon={LayersIcon}
          size="wide"
          variant="controls"
          className="space-y-3"
        >
          <input
            ref={input}
            type="file"
            accept="video/*,.mkv,.mov,.webm"
            multiple
            className="hidden"
            aria-label={t('batch.file_input_label')}
            onChange={(event) => {
              const selected = Array.from(event.target.files || []);
              event.target.value = '';
              addFiles(selected);
            }}
          />
          <section
            className="space-y-3 rounded-xl border border-border/60 bg-muted/20 p-3"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              if (!busy) addFiles(Array.from(event.dataTransfer.files));
            }}
          >
            <Button className="w-full" disabled={busy} onClick={() => input.current?.click()}>
              <UploadIcon />
              {t('batch.add_videos')}
            </Button>
            <p className="text-center text-xs text-muted-foreground">{t('batch.drop_hint')}</p>
            {files.map((file, index) => (
              <div
                key={file.name + index}
                className="flex items-center gap-2 rounded-lg bg-background/40 px-2 py-1.5 text-xs"
              >
                <ListVideoIcon className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">{file.name}</span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {t('batch.file_size_mb', {
                    size: (file.size / 1024 / 1024).toFixed(1),
                  })}
                </span>
                <Button
                  size="icon-xs"
                  variant="ghost"
                  aria-label={t('batch.delete') + ': ' + file.name}
                  disabled={busy}
                  onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}
                >
                  <XIcon />
                </Button>
              </div>
            ))}
            {files.length > 0 && (
              <p className="text-xs tabular-nums text-muted-foreground">
                {t('batch.estimate', {
                  videos: files.length,
                  langs: langs.length,
                  jobs: files.length * langs.length,
                })}
              </p>
            )}
          </section>
          <section className="space-y-3 rounded-xl border border-border/60 bg-muted/20 p-3">
            <h2 className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <LanguagesIcon className="size-4" />
              {t('batch.target_languages')}
            </h2>
            <LanguagePicker
              options={languageOptions}
              value={langs.at(-1) || 'Spanish'}
              disabled={busy}
              onValueChange={(value) =>
                setLangs((current) => (current.includes(value) ? current : [...current, value]))
              }
            />
            <div className="flex flex-wrap gap-1">
              {langs.map((lang) => (
                <Button
                  key={lang}
                  variant="secondary"
                  size="xs"
                  disabled={busy}
                  onClick={() => setLangs((current) => current.filter((item) => item !== lang))}
                >
                  {lang}
                  <XIcon />
                </Button>
              ))}
            </div>
            <div className="flex items-center gap-2 border-t border-border/50 pt-3 text-xs">
              <LanguagesIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="text-muted-foreground">{t('dub.engine_label')}</span>
              <span className="ml-auto max-w-[12rem] truncate font-medium">
                {translationEngine?.display_name || t('common.loading')}
              </span>
            </div>
            {translationEngines.data &&
              !(translationEngine?.ready ?? translationEngine?.installed) && (
                <PipelineFailure
                  fallback={
                    translationEngine?.availability_reason || t('modelSettings.unavailable')
                  }
                />
              )}
          </section>
          <section className="space-y-3 rounded-xl border border-border/60 bg-muted/20 p-3">
            <details className="text-xs">
              <summary className="cursor-pointer font-medium">{t('batch.voice_kicker')}</summary>
              <div className="mt-2 flex flex-wrap gap-1">
                <Button
                  size="xs"
                  variant={!voice ? 'secondary' : 'ghost'}
                  disabled={busy}
                  onClick={() => setVoice('')}
                >
                  {t('batch.default_option')}
                </Button>
                {profiles.data?.map((profile) => (
                  <Button
                    key={profile.id}
                    size="xs"
                    variant={voice === profile.id ? 'secondary' : 'ghost'}
                    disabled={busy}
                    onClick={() => setVoice(profile.id)}
                  >
                    {profile.name}
                  </Button>
                ))}
              </div>
              <p className="mt-3 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                <WandSparklesIcon className="size-3.5" />
                {t('batch.presets')}
              </p>
              <div className="mt-1 flex flex-wrap gap-1">
                {PRESETS.map((preset) => (
                  <Button
                    key={preset.id}
                    size="xs"
                    variant={voice === `preset:${preset.id}` ? 'secondary' : 'ghost'}
                    disabled={busy}
                    onClick={() => setVoice(`preset:${preset.id}`)}
                  >
                    {t(`clone.preset_${preset.id}`, {
                      defaultValue: preset.name,
                    })}
                  </Button>
                ))}
              </div>
            </details>
            <div className="flex items-center justify-between gap-3 text-xs leading-5">
              <span className="flex items-center gap-2">
                <Volume2Icon className="size-4 shrink-0 text-muted-foreground" />
                {t('batch.preserve_bg')}
              </span>
              <Switch
                aria-label={t('batch.preserve_bg')}
                checked={preserve}
                disabled={busy}
                onCheckedChange={setPreserve}
              />
            </div>
          </section>
          <Button
            className="w-full"
            disabled={
              busy ||
              ttsBlocker !== null ||
              !files.length ||
              !langs.length ||
              Boolean(
                translationEngines.data &&
                !(translationEngine?.ready ?? translationEngine?.installed),
              )
            }
            onClick={() => void submit()}
          >
            {t(busy ? 'common.loading' : 'batch.add_to_queue')}
          </Button>
          <EngineNotice operation="batch" />
          <WatchFolder
            langs={langs.map((label) => LANG_CODES.find((item) => item.label === label)!.code)}
            voiceId={voice}
            preserveBg={preserve}
            disabled={ttsBlocker !== null}
            onAdded={() => {
              setTab('active');
              void client.invalidateQueries({ queryKey: ['batch-jobs'] });
            }}
          />
        </SecondarySidebar>
        <section className="min-w-0 flex-1 overflow-y-auto p-6">
          <div className="mb-5 flex items-center gap-3">
            <div className="inline-flex gap-1 rounded-xl border border-border/50 bg-muted/20 p-1">
              {['active', 'done', 'failed'].map((value) => (
                <Button
                  key={value}
                  variant={tab === value ? 'secondary' : 'ghost'}
                  onClick={() => setTab(value)}
                >
                  {value === 'active' ? (
                    <ActivityIcon />
                  ) : value === 'done' ? (
                    <CheckCircle2Icon />
                  ) : (
                    <AlertCircleIcon />
                  )}
                  {t('batch.' + (value === 'done' ? 'completed' : value))}
                </Button>
              ))}
            </div>
            <Button
              className="ml-auto"
              variant="ghost"
              size="sm"
              disabled={jobs.isFetching}
              onClick={() => void jobs.refetch()}
            >
              <RefreshCwIcon className={jobs.isFetching ? 'animate-spin' : ''} />
              {t('batch.refresh')}
            </Button>
          </div>
          {(error || jobs.isError) && (
            <PipelineFailure
              className="mb-4"
              fallback={error || describeError(jobs.error)}
              action={
                jobs.isError ? (
                  <Button size="xs" variant="ghost" onClick={() => void jobs.refetch()}>
                    {t('common.retry')}
                  </Button>
                ) : undefined
              }
              onDismiss={error ? () => setError(null) : undefined}
            />
          )}
          {jobs.isPending && <p role="status">{t('common.loading')}</p>}
          {jobs.data?.length === 0 && (
            <div className="flex min-h-[28rem] flex-col items-center justify-center text-center">
              <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <LayersIcon className="size-6" />
              </div>
              <h2 className="text-sm font-semibold">
                {t('batch.no_' + (tab === 'done' ? 'completed' : tab))}
              </h2>
              {tab === 'active' && (
                <>
                  <p className="mt-2 text-sm text-muted-foreground">{t('batch.drop_hint')}</p>
                  <Button className="mt-5" onClick={() => input.current?.click()}>
                    <UploadIcon />
                    {t('batch.add_videos')}
                  </Button>
                </>
              )}
            </div>
          )}
          <div className="space-y-3">
            {jobs.data?.map((job) => (
              <BatchJobCard
                key={job.id}
                job={job}
                acting={acting}
                deleting={deleting}
                setDeleting={setDeleting}
                act={act}
                recover={recover}
                download={download}
              />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

const statusAppearance = {
  queued: { icon: CircleIcon, className: 'text-muted-foreground' },
  running: { icon: ActivityIcon, className: 'text-primary' },
  done: { icon: CheckCircle2Icon, className: 'text-emerald-500' },
  failed: { icon: AlertCircleIcon, className: 'text-destructive' },
  cancelled: { icon: SquareIcon, className: 'text-amber-500' },
} satisfies Record<BatchJob['status'], { icon: typeof CircleIcon; className: string }>;

function BatchJobCard({
  job,
  acting,
  deleting,
  setDeleting,
  act,
  recover,
  download,
}: {
  job: BatchJob;
  acting: string | null;
  deleting: string | null;
  setDeleting(value: string | null): void;
  act(job: BatchJob, remove: boolean): Promise<void>;
  recover(job: BatchJob): Promise<void>;
  download(job: BatchJob, lang: string): Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const appearance = statusAppearance[job.status];
  const StatusIcon = appearance.icon;
  const duration =
    job.finished_at && job.started_at ? Math.max(0, job.finished_at - job.started_at) : null;
  const created = formatTimestamp(job.created_at, i18n.resolvedLanguage);

  return (
    <article className="rounded-xl border border-border/50 bg-card/20 p-4 transition-colors hover:border-border">
      <div className="flex items-center gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted/35 text-muted-foreground">
          <FilmIcon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-medium">{job.filename}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className={`inline-flex items-center gap-1.5 ${appearance.className}`}>
              <StatusIcon
                className={job.status === 'running' ? 'size-3 animate-pulse' : 'size-3'}
              />
              {t('batch.status_' + job.status)}
            </span>
            <span aria-hidden="true">·</span>
            <span className="inline-flex items-center gap-1.5 tabular-nums">
              <Clock3Icon className="size-3" />
              {created}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-1">
          {job.langs.map((lang) => (
            <span
              key={lang}
              className="rounded-md border border-border/50 bg-muted/25 px-2 py-1 text-[11px] font-medium uppercase text-muted-foreground"
            >
              {lang}
            </span>
          ))}
        </div>
      </div>
      {job.progress && (
        <div role="status" className="mt-4 space-y-2 text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <p className="font-medium text-foreground">
              {t(
                'batch.stage_' + (job.progress.stage === 'done' ? 'complete' : job.progress.stage),
              )}{' '}
              {job.progress.current_lang}
            </p>
            {job.progress.current_segment != null && job.progress.total_segments && (
              <span>
                {t('batch.seg', {
                  current: job.progress.current_segment,
                  total: job.progress.total_segments,
                })}
              </span>
            )}
            <span className="ml-auto tabular-nums">{job.progress.percent}%</span>
          </div>
          <progress
            className="h-1.5 w-full accent-primary"
            max={100}
            value={Math.min(100, Math.max(0, job.progress.percent))}
          />
        </div>
      )}
      {duration != null && (
        <p className="mt-3 text-xs tabular-nums text-muted-foreground">
          {t('batch.completed_in', { duration: formatDuration(duration) })}
        </p>
      )}
      {job.error && generationFailureMessage(job, t) && (
        <p role="alert" className="mt-3 text-xs text-destructive">
          {generationFailureMessage(job, t)}
        </p>
      )}
      {job.error && (
        <details className="mt-3 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          <summary>{t('common.more_info')}</summary>
          <p className="mt-2 break-words">{job.error}</p>
        </details>
      )}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        {job.status === 'done' &&
          Object.keys(job.outputs || {}).map((lang) => (
            <Button key={lang} variant="ghost" size="sm" onClick={() => void download(job, lang)}>
              <DownloadIcon />
              {t('dub.export')} {lang}
            </Button>
          ))}
        {['failed', 'cancelled'].includes(job.status) && (
          <Button
            variant={job.setup_required ? 'default' : 'ghost'}
            size="sm"
            disabled={Boolean(acting) || job.retry_ready === false}
            onClick={() => void recover(job)}
          >
            {job.setup_required ? <LanguagesIcon /> : <RefreshCwIcon />}
            {t(
              job.retry_ready === false
                ? 'common.loading'
                : job.setup_required
                  ? 'modelMaintenance.install'
                  : 'common.retry',
            )}
          </Button>
        )}
        {['queued', 'running'].includes(job.status) ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={Boolean(acting)}
            onClick={() => void act(job, false)}
          >
            <SquareIcon />
            {t('batch.cancel')}
          </Button>
        ) : deleting === job.id ? (
          <>
            <Button
              variant="destructive"
              size="sm"
              disabled={Boolean(acting)}
              onClick={() => void act(job, true)}
            >
              <Trash2Icon />
              {t('batch.delete')}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDeleting(null)}>
              {t('common.cancel')}
            </Button>
          </>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setDeleting(job.id)}>
            <Trash2Icon />
            {t('batch.delete')}
          </Button>
        )}
      </div>
    </article>
  );
}

function formatTimestamp(value: number, locale?: string) {
  const date = new Date(value < 1e12 ? value * 1000 : value);
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function formatDuration(seconds: number) {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  if (minutes < 60) return `${minutes}m ${remainder}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
