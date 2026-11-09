import { useEffect, useId, useState } from 'react';
import { XIcon } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import type { Mode } from './longform-session';

export interface Recovery {
  job_id: string;
  type: string;
  title: string;
  chapters_done: number;
  total_chapters: number;
  created_at?: number | string | null;
}

/** The backend job type each page renders as (`_render_longform_sse(job_type=…)`). */
export const RECOVERY_JOB_TYPE: Record<Mode, string> = {
  stories: 'story',
  audiobook: 'audiobook',
};

const DISMISSED_KEY = 'voicestudio.longform.dismissed-recoveries';

function readDismissed(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(DISMISSED_KEY) ?? '[]');
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

function writeDismissed(ids: readonly string[]) {
  try {
    // Only ids the backend still lists are kept, so the list never grows without bound.
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(ids));
  } catch {
    // Storage can be unavailable; the card then simply hides until the next reload.
  }
}

const projectKey = (job: Recovery) =>
  `${job.title.trim().toLocaleLowerCase()}:${job.total_chapters}`;

const stamp = (job: Recovery) =>
  typeof job.created_at === 'number' ? job.created_at : Date.parse(job.created_at ?? '') || 0;

/**
 * This page's interrupted renders, newest first, one card per project: a render
 * stopped twice leaves two manifests for the same title, and both resume the
 * same work from the shared chapter cache.
 */
export function recoveriesFor(
  mode: Mode,
  jobs: readonly Recovery[] | undefined,
  dismissed: readonly string[] = [],
): Recovery[] {
  const seen = new Set<string>();
  return (jobs ?? [])
    .filter((job) => job.type === RECOVERY_JOB_TYPE[mode] && !dismissed.includes(job.job_id))
    .sort((a, b) => stamp(b) - stamp(a) || b.chapters_done - a.chapters_done)
    .filter((job) => {
      const key = projectKey(job);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

/**
 * Renders that stopped before finishing (the app reloaded or quit, or the user
 * pressed Stop), read from the backend's resume manifests, so a render lost
 * with the page is picked up again next to Generate instead of starting over.
 */
export function RenderRecovery({
  mode,
  active,
  disabled,
  disabledReason,
  onResume,
}: {
  mode: Mode;
  /** A render is running now; its own manifest is not an interrupted render. */
  active: boolean;
  disabled: boolean;
  /** Why Resume is unavailable, shown under it and announced with it. */
  disabledReason?: string;
  onResume: (jobId: string) => void;
}) {
  const { t } = useTranslation();
  const reasonId = useId();
  const [dismissed, setDismissed] = useState(readDismissed);
  const query = useQuery({
    queryKey: ['longform-recovery'],
    queryFn: ({ signal }) => apiJson<{ jobs: Recovery[] }>('/audiobook/jobs', { signal }),
  });
  const { refetch } = query;
  useEffect(() => {
    if (!active) void refetch();
  }, [active, refetch]);
  if (!active && query.isError) {
    return (
      <div
        role="alert"
        className="flex shrink-0 items-center justify-between gap-2 rounded-lg border border-border/60 bg-muted/20 p-2.5 text-xs"
      >
        <p>{t('audiobook.recovery_load_failed')}</p>
        <Button size="sm" variant="outline" onClick={() => void refetch()}>
          {t('common.retry')}
        </Button>
      </div>
    );
  }
  const jobs = active ? [] : recoveriesFor(mode, query.data?.jobs, dismissed);
  if (!jobs.length) return null;
  // Dismissing a card hides every manifest of that project, not just the newest.
  const dismiss = (card: Recovery) => {
    const listed = query.data?.jobs ?? [];
    const same = listed
      .filter((job) => job.type === card.type && projectKey(job) === projectKey(card))
      .map((job) => job.job_id);
    const ids = new Set(listed.map((job) => job.job_id));
    const next = [...new Set([...dismissed.filter((id) => ids.has(id)), ...same])];
    setDismissed(next);
    writeDismissed(next);
  };
  const title = t(mode === 'stories' ? 'stories.recovery_title' : 'audiobook.recovery_title');
  const reason = disabled ? disabledReason : undefined;
  return (
    <div className="max-h-[24vh] shrink-0 space-y-2 overflow-y-auto">
      {jobs.map((job) => (
        <section
          key={job.job_id}
          aria-label={title}
          className="space-y-1 rounded-lg border border-border/60 bg-muted/20 p-2.5 text-xs"
        >
          <div className="flex items-center gap-2">
            <h2 className="min-w-0 flex-1 truncate font-medium">{title}</h2>
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label={t('common.dismiss')}
              title={t('common.dismiss')}
              onClick={() => dismiss(job)}
            >
              <XIcon />
            </Button>
          </div>
          <p className="truncate">
            {job.title || t(mode === 'stories' ? 'stories.untitled' : 'audiobook.untitled')}
          </p>
          <p className="text-muted-foreground">
            {t('audiobook.recovery_progress', {
              done: job.chapters_done,
              total: job.total_chapters,
            })}
          </p>
          <Button
            size="sm"
            variant="outline"
            className="w-full"
            disabled={disabled}
            aria-describedby={reason ? `${reasonId}-${job.job_id}` : undefined}
            title={reason}
            onClick={() => onResume(job.job_id)}
          >
            {t('common.resume')}
          </Button>
          {reason && (
            <p id={`${reasonId}-${job.job_id}`} className="text-muted-foreground">
              {reason}
            </p>
          )}
        </section>
      ))}
    </div>
  );
}
