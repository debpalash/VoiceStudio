import { useEffect, useRef, useState } from 'react';
import { apiJson } from '@/lib/api/client';
interface DescriptionResult {
  attrs: Record<string, string>;
  matched: unknown[];
  unmatched: string[];
}
/**
 * Manual edits, new text and navigation always supersede a queued mapper
 * response. `apply` also receives the description the mapping belongs to, so
 * callers can drop one that no longer matches.
 */
export function useDescription(
  apply: (attrs: Record<string, string>, description: string) => void,
) {
  const applyRef = useRef(apply);
  applyRef.current = apply;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const active = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false);
  const [unmatched, setUnmatched] = useState<string[]>([]);
  const [matched, setMatched] = useState(true);
  const [failed, setFailed] = useState(false);
  const cancel = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    active.current?.abort();
    active.current = null;
    setPending(false);
  };
  const describe = (description: string, immediate = false) => {
    cancel();
    setFailed(false);
    setUnmatched([]);
    setMatched(true);
    const normalized = description.trim();
    if (!normalized) {
      if (immediate) applyRef.current({}, '');
      return;
    }
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    timer.current = setTimeout(
      () => {
        timer.current = null;
        void apiJson<DescriptionResult>('/design/describe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ description: normalized }),
          signal: controller.signal,
        })
          .then((result) => {
            if (controller.signal.aborted) return;
            applyRef.current(result.attrs, normalized);
            setUnmatched(result.unmatched ?? []);
            setMatched(Boolean(result.matched?.length));
          })
          .catch(() => {
            if (!controller.signal.aborted) setFailed(true);
          })
          .finally(() => {
            if (active.current === controller) {
              active.current = null;
              setPending(false);
            }
          });
      },
      immediate ? 0 : 450,
    );
  };
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      active.current?.abort();
    },
    [],
  );
  return {
    describe: (description: string) => describe(description),
    reset: (description: string) => describe(description, true),
    cancel,
    pending,
    unmatched,
    matched,
    failed,
  };
}
