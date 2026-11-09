import { useEffect, useRef } from 'react';
import type { HistoryItem } from '@/lib/api/types';
import { useHistory } from '@/hooks/use-history';
import { useLatestOutput } from '@/lib/store/output';
import { openTake, useSelectedTake } from '@/lib/store/takes';

/**
 * The take an open take pane should switch to once a new take of the same
 * kind is in the history, or null to leave it alone.
 */
export function nextOpenTake(
  open: HistoryItem | null,
  rendered: string | null,
  history: readonly HistoryItem[] | undefined,
): HistoryItem | null {
  if (!open || !rendered || open.id === rendered) return null;
  const take = history?.find((item) => item.id === rendered);
  return take && take.mode === open.mode ? take : null;
}

/**
 * Keeps the open take pane on the newest take. Without this the pane went on
 * showing the take it was opened on, so a new render beside it looked as if
 * it had used that old take's language and style.
 */
export function useFollowLatestTake() {
  const open = useSelectedTake();
  const latest = useLatestOutput().result?.id ?? null;
  const history = useHistory().data;
  // Only a render made while this page is mounted moves the pane: an output
  // left over from earlier must not replace a take the user just opened.
  const seen = useRef(latest);
  // The new render, and the take that was open when it landed. Opening a
  // different take before the history catches up cancels the follow, so the
  // user's choice (and any half-filled save form on it) is never replaced.
  const pending = useRef<{ render: string; from: string } | null>(null);
  // The selection the last run saw. A render that lands in the same run as a
  // new selection never follows: the user's pick wins.
  const seenOpen = useRef(open?.id ?? null);
  useEffect(() => {
    const openId = open?.id ?? null;
    const picked = openId !== seenOpen.current;
    seenOpen.current = openId;
    if (latest !== seen.current) {
      seen.current = latest;
      pending.current = latest && openId && !picked ? { render: latest, from: openId } : null;
    }
    if (pending.current && pending.current.from !== open?.id) pending.current = null;
    const next = nextOpenTake(open, pending.current?.render ?? null, history);
    if (next) {
      pending.current = null;
      openTake(next);
    }
  }, [latest, open, history]);
}
