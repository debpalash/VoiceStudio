import { useTranslation } from 'react-i18next';
import { PhoneIncomingIcon, PhoneOutgoingIcon } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { callTime, type CallRecord } from '@/lib/api/calls';
import { cn } from '@/lib/utils';
import { OutcomeBadge } from './call-parts';
import { callPhase, formatDuration } from './call-state';

export function CallHistory({
  calls,
  loading,
  selectedId,
  onSelect,
}: {
  calls: CallRecord[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (call: CallRecord) => void;
}) {
  const { t, i18n } = useTranslation();
  const format = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'short', timeStyle: 'short' });
  if (loading)
    return (
      <div className="space-y-2 p-3" aria-busy="true">
        {[0, 1, 2].map((key) => (
          <Skeleton key={key} className="h-16 w-full rounded-lg" />
        ))}
      </div>
    );
  if (!calls.length)
    return (
      <p className="px-4 py-8 text-center text-sm text-muted-foreground">
        {t('calls.history_empty')}
      </p>
    );
  return (
    <ul aria-label={t('calls.history')} className="space-y-1 p-2">
      {calls.map((call) => {
        const Icon = call.direction === 'inbound' ? PhoneIncomingIcon : PhoneOutgoingIcon;
        const live = callPhase(call.status) !== 'ended' && !call.ended_at;
        const at = callTime(call.created_at);
        return (
          <li key={call.id}>
            <button
              type="button"
              aria-current={call.id === selectedId ? 'true' : undefined}
              onClick={() => onSelect(call)}
              className={cn(
                'w-full space-y-1 rounded-lg px-3 py-2 text-left outline-none transition-colors hover:bg-accent/55 focus-visible:ring-2 focus-visible:ring-ring',
                call.id === selectedId && 'bg-accent/80 shadow-[inset_2px_0_0_var(--primary)]',
              )}
            >
              <span className="flex min-w-0 items-center gap-2">
                <Icon aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate font-mono text-sm">{call.to_masked}</span>
                {live ? (
                  <span className="inline-flex items-center gap-1 text-xs text-primary">
                    <span
                      aria-hidden="true"
                      className="size-1.5 animate-pulse rounded-full bg-primary motion-reduce:animate-none"
                    />
                    {t('calls.live')}
                  </span>
                ) : (
                  <OutcomeBadge outcome={call.outcome} />
                )}
              </span>
              {call.brief && (
                <span className="line-clamp-2 block text-xs text-muted-foreground">
                  {call.brief}
                </span>
              )}
              <span className="flex gap-2 text-[11px] text-muted-foreground tabular-nums">
                {at !== null && <span>{format.format(at)}</span>}
                {call.duration_s != null && <span>· {formatDuration(call.duration_s)}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
