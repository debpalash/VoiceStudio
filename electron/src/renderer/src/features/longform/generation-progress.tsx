import { useEffect, useMemo, useState } from 'react';
import {
  CheckIcon,
  CircleIcon,
  LoaderCircleIcon,
  TriangleAlertIcon,
  XIcon,
  ZapIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { AudiobookRenderChapter } from './longform-session';
import { formatDuration, renderStatus, type RenderProgressSnapshot } from './render-eta';

function ChapterStatusIcon({ status }: { status: string }) {
  if (status === 'rendering')
    return <LoaderCircleIcon className="size-3.5 animate-spin motion-reduce:animate-none" />;
  if (status === 'done') return <CheckIcon className="size-3.5" />;
  if (status === 'cached') return <ZapIcon className="size-3.5" />;
  if (status === 'failed') return <XIcon className="size-3.5" />;
  return <CircleIcon className="size-3.5" />;
}

export function GenerationProgress({
  chapters,
  assembling,
  stage = assembling ? 'assembling' : 'rendering',
  progress = null,
  startedAt = null,
  lastActivityAt = null,
}: {
  chapters: AudiobookRenderChapter[];
  assembling: boolean;
  stage?: string;
  progress?: RenderProgressSnapshot | null;
  startedAt?: number | null;
  lastActivityAt?: number | null;
}) {
  const { t } = useTranslation();
  // The clock lives in the session (startedAt), so leaving the page and
  // coming back keeps the real elapsed time instead of restarting at 0:00.
  const [mountedAt] = useState(() => Date.now());
  const [now, setNow] = useState(mountedAt);
  const completed = useMemo(
    () =>
      chapters.filter((chapter) => ['done', 'cached', 'failed'].includes(chapter.status)).length,
    [chapters],
  );
  const total = chapters.length;
  const status = renderStatus({
    stage,
    chapters,
    progress,
    startedAt: startedAt ?? mountedAt,
    lastActivityAt,
    now,
  });
  const percent = Math.round(status.percent);
  const eta = status.etaS;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-xl border border-primary/15 bg-primary/5 p-3 shadow-[inset_0_1px_0_rgb(255_255_255/4%)]"
    >
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="font-medium">
          {assembling
            ? t('audiobook.assembling')
            : t('audiobook.progress_summary', { current: completed, total })}
        </span>
        <span className="shrink-0 font-mono tabular-nums text-muted-foreground">
          {formatDuration(status.elapsedS)}
          {assembling
            ? ''
            : eta != null
              ? ` · ${t('audiobook.eta', { time: formatDuration(eta) })}`
              : progress && percent < 100
                ? ` · ${t('audiobook.eta_estimating')}`
                : ''}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-300 motion-reduce:transition-none"
          style={{ width: `${assembling ? 100 : percent}%` }}
        />
      </div>
      {!assembling && (progress || status.stalledForS != null) && (
        <div className="mt-1.5 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
          <span className="tabular-nums">{percent}%</span>
          {status.rtf != null && (
            <span className="tabular-nums" title={t('audiobook.realtime_hint')}>
              {t('audiobook.realtime', { rate: status.rtf.toFixed(2) })}
            </span>
          )}
        </div>
      )}
      {status.stalledForS != null && (
        <p role="alert" className="mt-2 flex items-start gap-1.5 text-xs text-warning">
          <TriangleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
          <span>{t('audiobook.stalled', { time: formatDuration(status.stalledForS) })}</span>
        </p>
      )}
      {total > 0 && (
        <ol className="mt-2 max-h-36 space-y-0.5 overflow-y-auto">
          {chapters.map((chapter, index) => (
            <li
              key={index}
              title={chapter.status === 'failed' ? chapter.error : undefined}
              className="flex min-w-0 items-center gap-2 py-0.5 text-xs"
            >
              <span
                className={
                  chapter.status === 'failed'
                    ? 'text-destructive'
                    : chapter.status === 'rendering'
                      ? 'text-primary'
                      : 'text-muted-foreground'
                }
              >
                <ChapterStatusIcon status={chapter.status} />
              </span>
              <span
                className={`truncate ${
                  chapter.status === 'pending'
                    ? 'text-muted-foreground/55'
                    : chapter.status === 'rendering'
                      ? 'font-medium'
                      : 'text-muted-foreground'
                }`}
              >
                {chapter.title || t('audiobook.chapter_n', { n: index + 1 })}
              </span>
              {chapter.status === 'cached' && (
                <span className="shrink-0 text-muted-foreground">
                  · {t('audiobook.cached_tag')}
                </span>
              )}
              {chapter.status === 'failed' && (
                <span className="min-w-0 truncate text-destructive/80">
                  · {t('audiobook.failed_tag')}
                  {chapter.error ? `: ${chapter.error}` : ''}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
