import { useEffect, useRef, useState } from 'react';
import { apiJson } from '@/lib/api/client';

export interface DescriptionResult {
  attrs: Record<string, string>;
  matched: unknown[];
  unmatched: string[];
}

const DEBOUNCE_MS = 450;

/**
 * Maps a typed voice description onto voice-design details through
 * `POST /design/describe`. `apply` receives the mapped details together with
 * the (trimmed) description they belong to, so a caller can discard a mapping
 * whose text has since changed.
 */
export function useDescription(
  apply: (attrs: Record<string, string>, description: string) => void,
) {
  const applyRef = useRef(apply);
  applyRef.current = apply;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false);
  const [unmatched, setUnmatched] = useState<string[]>([]);
  const [matched, setMatched] = useState(true);
  const [failed, setFailed] = useState(false);

  const stop = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    controller.current?.abort();
    controller.current = null;
  };

  const cancel = () => {
    stop();
    setPending(false);
  };

  const describe = (description: string, immediate = false) => {
    stop();
    setFailed(false);
    setUnmatched([]);
    setMatched(true);
    const text = description.trim();
    if (!text) {
      setPending(false);
      if (immediate) applyRef.current({}, '');
      return;
    }
    setPending(true);
    const request = new AbortController();
    controller.current = request;
    timer.current = setTimeout(
      () => {
        timer.current = null;
        apiJson<DescriptionResult>('/design/describe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ description: text }),
          signal: request.signal,
        })
          .then((result) => {
            if (request.signal.aborted) return;
            applyRef.current(result.attrs, text);
            setUnmatched(result.unmatched ?? []);
            setMatched((result.matched ?? []).length > 0);
          })
          .catch(() => {
            if (!request.signal.aborted) setFailed(true);
          })
          .finally(() => {
            if (controller.current === request) {
              controller.current = null;
              setPending(false);
            }
          });
      },
      immediate ? 0 : DEBOUNCE_MS,
    );
  };

  useEffect(() => stop, []);

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
