import {
  BanIcon,
  CircleAlertIcon,
  CircleCheckIcon,
  ClockIcon,
  LoaderCircleIcon,
  XIcon,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import type { GenerationQueueItem, GenerationQueueStatus } from '@/hooks/use-generate';
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

/** One take in the generation queue: status, script start, voice and any error. */
export function QueueRow({
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
