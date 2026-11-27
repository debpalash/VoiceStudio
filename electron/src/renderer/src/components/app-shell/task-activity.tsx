import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import type { BatchJob } from '@shared/api/batch-types';
import { useGenerationActivity } from '@/hooks/use-generate';
import { apiJson, describeError } from '@/lib/api/client';
import { runRendererTask } from '@/lib/global-error-recovery';
import {
  cancelDub,
  dismissDubError,
  resumeDub,
  useDubCancelling,
  useDubSession,
} from '@/features/dub/dub-session';
import { dubProgressPercent, dubStageKey } from '@/features/dub/dub-task-status';
import { Button } from '@/components/ui/button';
import { longformRoute, useLongformFailure } from '@/lib/longform-activity';

interface TaskAction {
  label: string;
  run: () => void | Promise<void>;
  disabled?: boolean;
}

interface TaskActivity {
  id: string;
  title: string;
  status: string;
  progress?: number | null;
  failed?: boolean;
  error?: string | null;
  actions: TaskAction[];
}

export function useTaskActivity(enabled: boolean, open: boolean) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const navigate = useNavigate();
  const generation = useGenerationActivity();
  const dub = useDubSession();
  const cancellingDub = useDubCancelling();
  const failedRender = useLongformFailure();
  // StatusBar already refreshes the active list. Only fetch retryable jobs
  // when someone opens the panel.
  const activeBatch = useQuery({
    queryKey: ['batch-jobs', 'active'],
    queryFn: ({ signal }) => apiJson<BatchJob[]>('/batch/jobs?status=active&limit=100', { signal }),
    enabled: false,
  });
  const checkBatch = enabled && open;
  const failedBatch = useQuery({
    queryKey: ['batch-jobs', 'failed'],
    queryFn: ({ signal }) =>
      apiJson<BatchJob[]>('/batch/jobs?status=retryable&limit=100', { signal }),
    enabled: checkBatch,
    refetchInterval: checkBatch ? 5_000 : false,
  });
  const batchAction = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: 'cancel' | 'retry' }) => {
      await apiJson('/batch/jobs/' + encodeURIComponent(id) + '/' + action, { method: 'POST' });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ['batch-jobs'] }),
    onError: (error) => toast.error(describeError(error)),
  });
  const rows: TaskActivity[] = [];
  for (const take of generation?.queue ?? []) {
    if (!generation || take.status === 'done' || take.status === 'cancelled') continue;
    const running = take.status === 'running';
    const failed = take.status === 'failed';
    rows.push({
      id: take.id,
      title: take.label,
      status:
        t(take.mode === 'design' ? 'nav.design' : 'nav.clone') +
        ' · ' +
        t(`generationQueue.status.${take.status}`),
      progress: running ? (generation.modelProgress ?? generation.progress) : undefined,
      failed,
      error: take.error,
      actions: [
        failed
          ? { label: t('common.retry'), run: () => generation.retryFailed(take.id) }
          : {
              label: t('common.cancel'),
              run: running ? generation.cancel : () => generation.removeQueued(take.id),
            },
        ...(failed
          ? [{ label: t('generationQueue.clear_finished'), run: generation.clearFinished }]
          : []),
      ],
    });
  }
  const dubActive = !['idle', 'editing', 'done'].includes(dub.phase);
  const dubFailed = Boolean(dub.failedPhase && (dub.error || dub.asrModelMissing));
  if (dubActive || dub.recovery || dubFailed) {
    const phase = dubActive ? dub.phase : (dub.failedPhase ?? dub.recovery ?? dub.phase);
    const canResume = Boolean(dub.recovery && !dub.asrModelMissing && !dubActive);
    rows.push({
      id: 'dub',
      title: dub.filename || t('dubWorkspace.title'),
      status: [t(dubStageKey(dub, phase)), dubFailed ? t('generationQueue.status.failed') : null]
        .filter(Boolean)
        .join(' · '),
      progress: dubActive ? dubProgressPercent(dub) : undefined,
      failed: dubFailed,
      actions: [
        { label: t('nav.dub'), run: () => navigate({ to: '/dub' }) },
        ...(canResume
          ? [
              {
                label: t(dub.recovery === 'transcribing' ? 'common.retry' : 'common.resume'),
                run: resumeDub,
                disabled: cancellingDub,
              },
            ]
          : []),
        ...(dubActive || dub.recovery
          ? [
              {
                label: t('common.cancel'),
                run: cancelDub,
                disabled: cancellingDub || dub.phase === 'importing',
              },
            ]
          : [{ label: t('common.dismiss'), run: dismissDubError }]),
      ],
    });
  }
  if (failedRender) {
    const { mode, title, error, retry, dismiss } = failedRender;
    const workspaceName = t(mode === 'stories' ? 'nav.stories' : 'audiobook.title');
    rows.push({
      id: 'longform-failed',
      title: title || workspaceName,
      status: t('generationQueue.status.failed'),
      failed: true,
      error,
      actions: [
        { label: workspaceName, run: () => navigate({ to: longformRoute(mode) }) },
        ...(retry ? [{ label: t('common.retry'), run: retry }] : []),
        { label: t('common.dismiss'), run: dismiss },
      ],
    });
  }

  const batchJobs = [
    ...(Array.isArray(activeBatch.data) ? activeBatch.data : []),
    ...(Array.isArray(failedBatch.data) ? failedBatch.data : []),
  ];
  const seenJobs = new Set<string>();
  for (const job of batchJobs) {
    if (!enabled || seenJobs.has(job.id)) continue;
    seenJobs.add(job.id);
    const active = job.status === 'running' || job.status === 'queued';
    rows.push({
      id: 'batch-' + job.id,
      title: job.filename,
      status: t('nav.batch_dub') + ' · ' + t(`generationQueue.status.${job.status}`),
      failed: job.status === 'failed',
      error: job.error,
      progress: active && job.status === 'running' ? (job.progress?.percent ?? null) : undefined,
      actions: [
        { label: t('nav.batch_dub'), run: () => navigate({ to: '/batch' }) },
        ...(active || (job.retry_ready !== false && !job.setup_required)
          ? [
              {
                label: t(active ? 'common.cancel' : 'common.retry'),
                run: () => batchAction.mutate({ id: job.id, action: active ? 'cancel' : 'retry' }),
                disabled: batchAction.isPending,
              },
            ]
          : []),
      ],
    });
  }
  if (checkBatch && failedBatch.isError) {
    rows.push({
      id: 'batch-query-error',
      title: t('nav.batch_dub'),
      status: t('common.error'),
      failed: true,
      actions: [
        {
          label: t('common.retry'),
          run: async () => {
            await failedBatch.refetch();
          },
          disabled: failedBatch.isFetching,
        },
      ],
    });
  }
  return { tasks: rows, checkingBatch: checkBatch && failedBatch.isPending };
}

export function TaskActivityList({ tasks }: { tasks: TaskActivity[] }) {
  const { t } = useTranslation();
  if (tasks.length === 0) return null;
  return (
    <section aria-label={t('generationQueue.title')} className="space-y-1">
      {tasks.map(({ id, title, status, progress, failed, error, actions }) => (
        <article key={id} className="rounded-lg border border-border/60 px-3 py-2">
          <p className="truncate text-sm font-medium" title={title}>
            {title}
          </p>
          <p className={failed ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
            {status}
          </p>
          {error && <p className="my-1 line-clamp-2 text-xs text-muted-foreground">{error}</p>}
          {progress !== undefined && (
            <progress
              aria-label={status}
              className="block h-1.5 w-full accent-primary"
              max={100}
              value={
                progress === null || !Number.isFinite(progress)
                  ? undefined
                  : Math.min(100, Math.max(0, progress))
              }
            />
          )}
          <div className="mt-1 flex flex-wrap gap-1">
            {actions.map(({ label, run, disabled }) => (
              <Button
                key={label}
                size="xs"
                variant="ghost"
                disabled={disabled}
                onClick={() => runRendererTask('Task activity action', run)}
              >
                {label}
              </Button>
            ))}
          </div>
        </article>
      ))}
    </section>
  );
}
