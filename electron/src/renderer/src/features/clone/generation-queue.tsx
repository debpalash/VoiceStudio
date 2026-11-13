import { ListOrderedIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useGenerateClone } from '@/hooks/use-generate';
import { cn } from '@/lib/utils';
import { QueueRow } from './generation-queue-row';

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
