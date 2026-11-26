import { AlertTriangleIcon, ArrowRightIcon, MergeIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import type { DubSession } from './dub-session';
import { dubSegmentIssues } from './dub-issue-state';
import { formatTimestamp } from '@shared/utils/timeFormat';
const formatSegmentTime = (seconds: number) => formatTimestamp(seconds, { decimals: 3 });

export function DubIssuesPanel({
  session,
  disabled,
  onFocus,
  onMerge,
  onRegenerate,
  onListen,
}: {
  session: DubSession;
  disabled: boolean;
  onFocus: (id: string) => void;
  onRegenerate?: (id: string) => void;
  onListen?: (id: string) => void;
  onMerge: (id: string, direction: 'prev' | 'next') => void;
}) {
  const { t } = useTranslation();
  const issues = dubSegmentIssues(session);
  const blocking = issues.some((issue) => issue.severity === 'error');
  if (!issues.length) return null;
  return (
    <section
      role={blocking ? 'alert' : 'status'}
      aria-label={t('dub.issues_title')}
      className={
        'shrink-0 border-b px-4 py-3 ' +
        (blocking ? 'border-destructive/40 bg-destructive/10' : 'border-warning/40 bg-warning/10')
      }
    >
      <details open={blocking}>
        <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium">
          <AlertTriangleIcon className="size-4" />
          {t(blocking ? 'dub.issues_title' : 'dub.issue_warning')} · {issues.length}
        </summary>
        <p className="mt-1 text-xs text-muted-foreground">
          {t(blocking ? 'dub.issues_hint' : 'dub.issue_warning_hint')}
        </p>
        <div className="mt-2 max-h-44 space-y-2 overflow-auto">
          {issues.map(({ segment, index, direction, severity, kind }) => (
            <article
              key={segment.id}
              className={
                'flex flex-wrap items-center gap-2 rounded-md border bg-background/40 px-3 py-2 ' +
                (severity === 'error' ? 'border-destructive/40' : 'border-warning/40')
              }
            >
              <div className="min-w-0 flex-1 text-xs">
                <p
                  className={
                    'font-medium ' + (severity === 'error' ? 'text-destructive' : 'text-warning')
                  }
                >
                  <span>
                    {t(severity === 'error' ? 'dub.issue_blocking' : 'dub.issue_warning')}
                  </span>{' '}
                  ·{t('dub.issue_segment', { number: index + 1 })} ·{' '}
                  {formatSegmentTime(segment.start)}–{formatSegmentTime(segment.end)}
                </p>
                <p className="break-words">«{segment.text}»</p>
                <p className="text-muted-foreground">
                  {kind === 'short'
                    ? t('dub.issue_short', { seconds: (segment.end - segment.start).toFixed(3) })
                    : t('dub.issue_' + kind)}
                </p>
                <p className="mt-1">
                  {session.narration?.enabled && kind === 'timing'
                    ? t('dub.narration_drift', {
                        seconds: (segment.fit_status?.drift_s || 0).toFixed(1),
                      })
                    : t('dub.issue_' + kind + '_solution')}
                </p>
              </div>
              {onListen && (
                <Button
                  size="xs"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => onListen(segment.id)}
                >
                  {t('player.play')}
                </Button>
              )}
              {onRegenerate && kind !== 'short' && (
                <Button
                  size="xs"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => onRegenerate(segment.id)}
                >
                  {t('dub.regen_one')}
                </Button>
              )}
              <Button
                size="xs"
                variant="outline"
                disabled={disabled}
                onClick={() => onFocus(segment.id)}
              >
                <ArrowRightIcon />
                {t('dub.issue_locate')}
              </Button>
              {direction && (
                <Button
                  size="xs"
                  variant="secondary"
                  disabled={disabled}
                  onClick={() => onMerge(segment.id, direction)}
                  title={t('dub.issue_merge_hint')}
                >
                  <MergeIcon />
                  {t(direction === 'next' ? 'dub.issue_merge_next' : 'dub.issue_merge_prev')}
                </Button>
              )}
            </article>
          ))}
        </div>
        {issues.some((issue) => issue.kind === 'short') && (
          <p className="mt-2 text-xs text-muted-foreground">{t('dub.issue_merge_hint')}</p>
        )}
      </details>
    </section>
  );
}
