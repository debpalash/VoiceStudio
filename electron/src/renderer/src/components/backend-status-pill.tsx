import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Spinner } from '@/components/ui/spinner';
import { cn } from '@/lib/utils';

export type BackendPillState = 'starting' | 'recovering' | 'failed';

/**
 * Counts how many times in a row the engine failed with the same message, so
 * a retry loop reads as one line ("Failed 3 times") instead of a screen that
 * looks new every time. A different message starts over at 1.
 */
export function useFailureRepeats(failed: boolean, message: string | undefined): number {
  const [count, setCount] = useState(0);
  const last = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!failed) return;
    const key = message ?? '';
    setCount((current) => (last.current === key ? current + 1 : 1));
    last.current = key;
  }, [failed, message]);
  return failed ? count : 0;
}

/** Seconds since `active` last became true; 0 while inactive. */
export function useActiveSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!active) {
      setSeconds(0);
      return;
    }
    const startedAt = Date.now();
    const id = window.setInterval(() => setSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return seconds;
}

/** One status line for the whole gate: starting, recovering or failed. */
export function BackendStatusPill({
  state,
  seconds,
  repeats,
}: {
  state: BackendPillState;
  seconds: number;
  repeats: number;
}) {
  const { t } = useTranslation();
  const failed = state === 'failed';
  return (
    <div
      role="status"
      data-testid="backend-status-pill"
      data-state={state}
      className={cn(
        'inline-flex max-w-full items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium',
        failed
          ? 'border-destructive/30 bg-destructive/10 text-destructive'
          : 'border-border/60 bg-muted/30 text-muted-foreground',
      )}
    >
      {failed ? (
        <span className="size-2 shrink-0 rounded-full bg-destructive" aria-hidden="true" />
      ) : (
        <Spinner className="size-3 shrink-0 text-primary" aria-hidden="true" />
      )}
      <span className="truncate">{t(`backend.pill_${state}`)}</span>
      {!failed ? (
        <span className="font-mono tabular-nums">{t('backend.elapsed', { seconds })}</span>
      ) : null}
      {failed && repeats > 1 ? (
        <span className="tabular-nums">{t('backend.pill_repeats', { count: repeats })}</span>
      ) : null}
    </div>
  );
}

/** Slim, non-blocking bar shown while a live engine is busy and not answering. */
export function BackendOfflineBar({ seconds }: { seconds: number }) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      data-testid="backend-offline-bar"
      className="pointer-events-none fixed inset-x-0 bottom-10 z-40 flex justify-center px-4"
    >
      <div className="inline-flex max-w-full items-center gap-2 rounded-full border border-warning/30 bg-card px-3 py-1.5 text-xs text-foreground shadow-md">
        <Spinner className="size-3 shrink-0 text-warning" aria-hidden="true" />
        <span className="truncate">{t('backend.offline_bar')}</span>
        <span className="font-mono tabular-nums text-muted-foreground">
          {t('backend.elapsed', { seconds })}
        </span>
      </div>
    </div>
  );
}
