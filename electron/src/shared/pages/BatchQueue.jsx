import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Activity,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  Square,
  Circle,
  Trash2,
  Download,
  XCircle,
  Film,
  Globe,
} from 'lucide-react';
import { Panel, Button, Badge, Tabs } from '../ui';
import {
  listBatchJobs,
  getBatchJob,
  cancelBatchJob,
  deleteBatchJob,
  enqueueBatchJob,
} from '../api/batch';
import { API } from '../api/client';
import BatchAddDialog from '../components/BatchAddDialog';
import WatchFolderBar from '../components/WatchFolderBar';
import toast from 'react-hot-toast';
import { toastErrorWithReport } from '../utils/errorToast';
import { asrMissingPayload, toastAsrModelMissing } from '../utils/asrModelMissing';
import { recordValueMoment } from '../utils/donationMoments';
import { absoluteTime, timeAgo } from '../utils/relativeTime';

/**
 * BatchQueue — UI for the /batch/* dubbing pipeline.
 *
 * Tabs: Active · Done · Failed. Polls every 3s for active jobs.
 * Shows real-time progress (extract → transcribe → translate → generate → mix).
 */

const STATUS_TONE = {
  queued: { tone: 'neutral', icon: Circle, label: 'batch.status_queued' },
  running: { tone: 'brand', icon: Activity, label: 'batch.status_running' },
  done: { tone: 'success', icon: CheckCircle, label: 'batch.status_done' },
  failed: { tone: 'danger', icon: AlertCircle, label: 'batch.status_failed' },
  cancelled: { tone: 'warn', icon: Square, label: 'batch.status_cancelled' },
};

// Per-status card border accent (was .batch-queue__card--{status} in CSS).
const CARD_BORDER = {
  running: 'border-transparent',
  failed: 'border-transparent',
};

const STAGE_LABELS = {
  extract: 'batch.stage_extract',
  transcribe: 'batch.stage_transcribe',
  translate: 'batch.stage_translate',
  generate: 'batch.stage_generate',
  mix: 'batch.stage_mix',
  done: 'batch.stage_complete',
};

export default function BatchQueue({ onBack }) {
  const { t } = useTranslation();
  const TABS = useMemo(
    () => [
      { id: 'active', label: t('batch.active'), icon: Activity },
      { id: 'done', label: t('batch.completed'), icon: CheckCircle },
      { id: 'failed', label: t('batch.failed'), icon: AlertCircle },
    ],
    [t],
  );
  const [tab, setTab] = useState('active');
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [addOpen, setAddOpen] = useState(false);

  // Settings the watch-folder ingest reuses: the last Add-to-queue submission,
  // or the dialog's own defaults before any manual enqueue this session.
  const lastSettingsRef = useRef({
    langs: [{ lang: 'Spanish', code: 'es' }],
    voiceId: '',
    preserveBg: true,
  });

  // Ids last seen queued/running. The 'active' filter excludes finished jobs
  // server-side, so a job VANISHING from the active list is the completion
  // signal — resolve its final status to tell done apart from failed/cancelled.
  const activeIdsRef = useRef(new Set());

  const resolveFinishedJob = useCallback(async (id) => {
    try {
      const job = await getBatchJob(id);
      // Success-only donation moment — a whole batch dub job finishing is a
      // real deliverable. Failed/cancelled jobs never count.
      if (job?.status === 'done') recordValueMoment('batch');
    } catch {
      /* job deleted or backend unreachable — not a completion */
    }
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const statusParam = tab === 'active' ? 'active' : tab;
      const next = await listBatchJobs(statusParam, 100);
      setJobs(next);
      if (statusParam === 'active') {
        const nextIds = new Set(next.map((j) => j.id));
        for (const id of activeIdsRef.current) {
          if (!nextIds.has(id)) resolveFinishedJob(id);
        }
        activeIdsRef.current = nextIds;
      }
    } catch (e) {
      console.warn('batch queue load failed', e);
    } finally {
      setLoading(false);
    }
  }, [tab, resolveFinishedJob]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Poll active tab every 3s for live progress
  useEffect(() => {
    if (tab !== 'active') return;
    const iv = setInterval(reload, 3000);
    return () => clearInterval(iv);
  }, [tab, reload]);

  const handleEnqueue = useCallback(
    async (files, settings) => {
      lastSettingsRef.current = settings;
      const langCodes = settings.langs.map((l) => l.code);
      let success = 0;
      const successfulFiles = new Set();
      for (const file of files) {
        try {
          await enqueueBatchJob(
            file,
            langCodes,
            settings.voiceId || undefined,
            settings.preserveBg,
          );
          success++;
          successfulFiles.add(file);
        } catch (e) {
          const missing = asrMissingPayload(e);
          if (missing) {
            // Typed 409: no ASR model installed → one download CTA, then stop
            // (every remaining file would fail the same preflight).
            toastAsrModelMissing(missing);
            break;
          }
          toastErrorWithReport(
            t('batch.enqueue_failed', { name: file.name, message: e.message }),
            e,
          );
        }
      }
      if (success > 0) {
        toast.success(t('batch.enqueued', { count: success }));
        setTab('active');
        reload();
      }
      return successfulFiles;
    },
    [t, reload],
  );

  // Watch-folder arrivals go through the exact same enqueue path (File
  // uploads to POST /batch/enqueue) with the last-used / default settings.
  const handleWatchIngest = useCallback(
    (files) => handleEnqueue(files, lastSettingsRef.current),
    [handleEnqueue],
  );

  const handleCancel = useCallback(
    async (id) => {
      try {
        await cancelBatchJob(id);
        toast.success(t('batch.job_cancelled'));
        reload();
      } catch (e) {
        toastErrorWithReport(t('batch.cancel_failed', { message: e.message }), e);
      }
    },
    [t, reload],
  );

  const handleDelete = useCallback(
    async (id) => {
      try {
        await deleteBatchJob(id);
        toast.success(t('batch.job_deleted'));
        reload();
      } catch (e) {
        toastErrorWithReport(t('batch.delete_failed', { message: e.message }), e);
      }
    },
    [t, reload],
  );

  return (
    <div className="batch-queue flex flex-1 flex-col gap-[var(--space-4)] min-h-0 overflow-y-auto px-[var(--space-6)] py-[var(--space-5)]">
      <div className="batch-queue__bar flex shrink-0 items-center gap-[var(--space-4)]">
        {onBack && (
          <Button variant="ghost" size="sm" onClick={onBack}>
            {t('batch.back')}
          </Button>
        )}
        <div
          role="heading"
          aria-level={1}
          className="m-0 inline-flex items-center gap-[var(--space-3)] [font-family:var(--font-display)] [font-size:var(--text-xl)] [font-weight:var(--weight-bold)] text-fg"
        >
          <Activity size={15} /> {t('batch.title')}
        </div>
        <div className="batch-queue__bar-spacer flex-1" />
        <WatchFolderBar onIngest={handleWatchIngest} />
        <Button
          variant="subtle"
          size="sm"
          onClick={reload}
          loading={loading}
          leading={<RefreshCw size={11} />}
        >
          {t('batch.refresh')}
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={() => setAddOpen(true)}
          leading={<PlusIcon size={11} />}
        >
          {t('batch.add_videos')}
        </Button>
      </div>

      <Tabs items={TABS} value={tab} onChange={setTab} className="batch-queue__tabs shrink-0" />

      {jobs.length === 0 && !loading && (
        <Panel variant="flat" padding="lg" className="batch-queue__empty text-center text-fg-muted">
          <div>
            <p>
              {tab === 'active' && t('batch.no_active')}
              {tab === 'done' && t('batch.no_completed')}
              {tab === 'failed' && t('batch.no_failed')}
            </p>
            <p className="batch-queue__empty-sub text-[var(--text-sm)] text-fg-subtle">
              {tab === 'active' && t('batch.drop_hint')}
              {tab === 'done' && 'Nothing has completed recently.'}
              {tab === 'failed' && 'No failed jobs — enjoy the silence.'}
            </p>
          </div>
        </Panel>
      )}

      <div className="batch-queue__list flex flex-1 flex-col gap-[var(--space-3)] min-h-0">
        {jobs.map((j) => (
          <JobCard key={j.id} job={j} onCancel={handleCancel} onDelete={handleDelete} t={t} />
        ))}
      </div>

      <BatchAddDialog open={addOpen} onClose={() => setAddOpen(false)} onEnqueue={handleEnqueue} />
    </div>
  );
}

function PlusIcon({ size }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function JobCard({ job, onCancel, onDelete, t }) {
  const st = STATUS_TONE[job.status] || STATUS_TONE.queued;
  const StIcon = st.icon;

  const ageLabel = timeAgo(job.created_at);

  const duration =
    job.finished_at && job.started_at ? Math.max(0, job.finished_at - job.started_at) : null;

  const progress = job.progress;
  const stageLabelKey = progress ? STAGE_LABELS[progress.stage] || progress.stage : null;
  const stageLabel = stageLabelKey ? t(stageLabelKey) : null;
  const pct = progress?.percent ?? 0;

  return (
    <Panel
      variant="flat"
      padding="md"
      className={`[transition:border-color_var(--dur-fast)] ${CARD_BORDER[job.status] || ''}`}
    >
      <div className="batch-queue__card-head flex flex-wrap items-center gap-[var(--space-3)] mb-[var(--space-2)]">
        <Badge tone={st.tone} dot>
          <StIcon size={10} /> {t(st.label)}
        </Badge>
        <span className="batch-queue__card-filename inline-flex items-center gap-[var(--space-2)] font-mono text-[var(--text-xs)] font-semibold text-fg">
          <Film size={10} /> {job.filename}
        </span>
        <span className="batch-queue__card-spacer flex-1" />
        <span
          className="batch-queue__card-age text-[var(--text-xs)] text-fg-subtle [font-variant-numeric:tabular-nums]"
          title={absoluteTime(job.created_at)}
        >
          {ageLabel}
        </span>
      </div>

      {/* Languages */}
      <div className="batch-queue__card-langs flex items-center gap-[var(--space-2)] mb-[var(--space-3)] text-[var(--text-xs)] text-fg-muted">
        <Globe size={9} />
        {job.langs.map((l) => (
          <span
            key={l}
            className="batch-queue__card-lang rounded-sm [border:1px_solid_var(--color-border)] bg-bg-elev-2 px-[6px] py-[1px] font-mono text-[10px] uppercase tracking-[0.05em]"
          >
            {l}
          </span>
        ))}
      </div>

      {/* Progress bar for running jobs */}
      {job.status === 'running' && progress && (
        <div className="batch-queue__progress mt-[var(--space-2)] mb-[var(--space-3)]">
          <div className="batch-queue__progress-bar relative h-[6px] overflow-hidden rounded-[3px] bg-bg-elev-2">
            <div
              className="batch-queue__progress-fill"
              style={{ width: `${Math.min(100, pct)}%` }}
            />
          </div>
          <div className="batch-queue__progress-info flex items-center gap-[var(--space-3)] mt-[var(--space-2)] text-[var(--text-xs)] text-fg-muted">
            <span className="batch-queue__progress-stage font-semibold text-fg">{stageLabel}</span>
            {progress.current_lang && (
              <span className="batch-queue__progress-lang rounded-sm [border:1px_solid_var(--color-border)] bg-bg-elev-2 px-[4px] font-mono text-[10px] uppercase">
                {progress.current_lang}
              </span>
            )}
            {progress.current_segment != null && progress.total_segments && (
              <span className="batch-queue__progress-segs [font-variant-numeric:tabular-nums]">
                {t('batch.seg', {
                  current: progress.current_segment,
                  total: progress.total_segments,
                })}
              </span>
            )}
            <span className="batch-queue__progress-pct ml-auto font-mono [font-variant-numeric:tabular-nums] font-semibold text-fg">
              {pct}%
            </span>
          </div>
        </div>
      )}

      {/* Duration for completed jobs */}
      {duration != null && (
        <div className="batch-queue__card-meta mt-[var(--space-1)] text-[var(--text-xs)] text-fg-muted">
          {t('batch.completed_in', { duration: formatDuration(duration) })}
        </div>
      )}

      {/* Error display */}
      {job.error && (
        <div className="batch-queue__card-error flex items-start gap-[var(--space-2)] mt-[var(--space-3)] rounded-sm [border:1px_solid_rgba(251,73,52,0.25)] bg-[rgba(251,73,52,0.06)] p-[var(--space-3)] text-[var(--text-sm)] leading-[1.4] text-danger">
          <AlertCircle size={11} /> {job.error}
        </div>
      )}

      {/* Output downloads for done jobs */}
      {job.status === 'done' && job.outputs && Object.keys(job.outputs).length > 0 && (
        <div className="batch-queue__card-outputs flex flex-wrap gap-[var(--space-2)] mt-[var(--space-3)]">
          {Object.entries(job.outputs).map(([lang]) => (
            <a
              key={lang}
              className="batch-queue__card-dl inline-flex items-center gap-[var(--space-2)] rounded-sm [border:1px_solid_var(--color-border)] bg-bg-elev-2 px-[10px] py-[3px] font-mono text-[var(--text-xs)] uppercase text-fg no-underline [transition:all_var(--dur-fast)] hover:bg-bg-elev-3 hover:[border-color:rgba(211,134,155,0.5)] hover:text-[#f3a5b6]"
              href={`${API}/batch/download/${job.id}/${lang}`}
              download
            >
              <Download size={10} /> {lang}
            </a>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="batch-queue__card-actions flex justify-end gap-[var(--space-2)] mt-[var(--space-3)]">
        {(job.status === 'queued' || job.status === 'running') && (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => onCancel(job.id)}
            leading={<XCircle size={10} />}
          >
            {t('batch.cancel')}
          </Button>
        )}
        {(job.status === 'done' || job.status === 'failed' || job.status === 'cancelled') && (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => onDelete(job.id)}
            leading={<Trash2 size={10} />}
          >
            {t('batch.delete')}
          </Button>
        )}
      </div>
    </Panel>
  );
}

function formatDuration(secs) {
  if (secs < 60) return `${secs.toFixed(1)}s`;
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
