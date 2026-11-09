import { Link } from '@tanstack/react-router';
import { AudioLinesIcon, BookOpenTextIcon, ChevronRightIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button, buttonVariants } from '@/components/ui/button';
import {
  longformRoute,
  stopLongformActivity,
  useLongformActivity,
  type LongformActivity,
} from '@/lib/longform-activity';
import { cn } from '@/lib/utils';

export function longformActivityStatus(
  activity: LongformActivity,
  t: (key: string, values?: Record<string, unknown>) => string,
): string {
  if (activity.stage === 'assembling') return t('audiobook.assembling');
  if (activity.stage === 'starting' || !activity.total) return t('common.loading');
  return t('audiobook.progress_summary', {
    current: Math.min(activity.completed + 1, activity.total),
    total: activity.total,
  });
}

/**
 * The running Stories/Audiobook render with a way back to it and a Stop, for
 * anywhere that is not its own page: a render must never become a dead end
 * the user can only wait out.
 */
export function LongformActivityCard({ className }: { className?: string }) {
  const { t } = useTranslation();
  const activity = useLongformActivity();
  if (!activity) return null;
  const Icon = activity.mode === 'stories' ? AudioLinesIcon : BookOpenTextIcon;
  const page = t(activity.mode === 'stories' ? 'nav.stories' : 'audiobook.title');
  const title =
    activity.title || t(activity.mode === 'stories' ? 'stories.untitled' : 'audiobook.untitled');
  return (
    <section
      aria-label={page}
      data-slot="longform-activity"
      className={cn('rounded-lg border border-border/60 bg-card/40 px-2.5 py-2', className)}
    >
      <p className="flex min-w-0 items-center gap-1.5 text-xs font-medium">
        <Icon aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="shrink-0">{page}</span>
        <span className="min-w-0 truncate text-muted-foreground">· {title}</span>
      </p>
      <p role="status" className="mt-1 text-[11px] text-muted-foreground">
        {longformActivityStatus(activity, t)}
      </p>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-300 motion-reduce:transition-none"
          style={{ width: `${activity.stage === 'assembling' ? 100 : activity.percent}%` }}
        />
      </div>
      <div className="mt-2 flex items-center gap-1.5">
        <Link
          to={longformRoute(activity.mode)}
          className={cn(buttonVariants({ variant: 'secondary', size: 'xs' }), 'flex-1')}
        >
          {t('audiobook.show_progress')}
          <ChevronRightIcon aria-hidden="true" />
        </Link>
        <Button variant="outline" size="xs" onClick={stopLongformActivity}>
          {t('common.stop')}
        </Button>
      </div>
    </section>
  );
}
