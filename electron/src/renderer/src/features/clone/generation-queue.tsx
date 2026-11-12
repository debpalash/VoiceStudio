import {
  BanIcon,
  CircleAlertIcon,
  CircleCheckIcon,
  ClockIcon,
  ListOrderedIcon,
  LoaderCircleIcon,
  XIcon,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import {
  useGenerateClone,
  type GenerationQueueItem,
  type GenerationQueueStatus,
} from '@/hooks/use-generate';
import { cn } from '@/lib/utils';

const STATUS_ICON: Record<GenerationQueueStatus, LucideIcon> = {
  queued: ClockIcon,
  running: LoaderCircleIcon,
  done: CircleCheckIcon,
  failed: CircleAlertIcon,
  cancelled: BanIcon,
};

const STATUS_TONE: Record<GenerationQueueStatus, string> = {
  queued: 'text-muted-foreground',
  running: 'text-primary animate-spin motion-reduce:animate-none',
  done: 'text-emerald-600 dark:text-emerald-400',
  failed: 'text-destructive',
  cancelled: 'text-muted-foreground',
};

function QueueRow({
  item,
  position,
  onRemove,
}: {
  item: GenerationQueueItem;
  /** 1-based place in line for a waiting take. */
  position: number | null;
  onRemove: (id: string) => void;
}) {
  const { t } = useTranslation();
  const Icon = STATUS_ICON[item.status];
  const statusText = t(`generationQueue.status.${item.status}`);
  return (
    <li
      data-status={item.status}
      className={cn(
        'flex items-start gap-2.5 rounded-md px-2 py-1.5',
        item.status === 'running' && 'bg-primary/5',
      )}
    >
      <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', STATUS_TONE[item.status])} />
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            'truncate text-sm',
            item.status === 'cancelled' && 'text-muted-foreground line-through',
          )}
          title={item.label}
        >
          {item.label}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          <span className="sr-only">{statusText}. </span>
          {[position != null ? t('generationQueue.position', { position }) : statusText, item.voice]
            .filter(Boolean)
            .join(' · ')}
        </p>
        {item.status === 'failed' && item.error && (
          <p className="mt-0.5 line-clamp-2 text-xs text-destructive">{item.error}</p>
        )}
      </div>
      {item.status === 'queued' && (
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          aria-label={t('generationQueue.remove')}
          title={t('generationQueue.remove')}
          onClick={() => onRemove(item.id)}
        >
          <XIcon />
        </Button>
      )}
    </li>
  );
}

/** The running take, the takes waiting behind it, and recent results. */
export function GenerationQueue({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { queue, queuedCount, isGenerating, removeQueued, clearFinished, cancelAll } =
    useGenerateClone();
  // A lone take that started right away needs no list: the button shows it.
  const worthShowing = queue.length > 1 || queuedCount > 0;
  if (!worthShowing) return null;
  const finished = queue.filter((item) => item.status !== 'queued' && item.status !== 'running');
  let waiting = 0;
  return (
    <section
      aria-label={t('generationQueue.title')}
      className={cn('rounded-lg bg-background/50 p-2 ring-1 ring-border/50', className)}
    >
      <header className="flex items-center justify-between gap-2 px-2 pb-1">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          <ListOrderedIcon aria-hidden="true" className="size-4 text-muted-foreground" />
          {t('generationQueue.title')}
          <span className="font-normal text-muted-foreground" aria-live="polite">
            {t('generationQueue.waiting', { count: queuedCount })}
          </span>
        </h3>
        <div className="flex items-center gap-1">
          {finished.length > 0 && (
            <Button variant="ghost" size="sm" onClick={clearFinished}>
              {t('generationQueue.clear_finished')}
            </Button>
          )}
          {(queuedCount > 0 || isGenerating) && (
            <Button variant="ghost" size="sm" className="text-destructive" onClick={cancelAll}>
              {t('generationQueue.cancel_all')}
            </Button>
          )}
        </div>
      </header>
      <ol className="max-h-56 space-y-0.5 overflow-y-auto overscroll-contain">
        {queue.map((item) => (
          <QueueRow
            key={item.id}
            item={item}
            position={item.status === 'queued' ? (waiting += 1) : null}
            onRemove={removeQueued}
          />
        ))}
      </ol>
    </section>
  );
}
