import { useEffect, useState } from 'react';
import { apiJson } from '@/lib/api/client';

type OnsetResponse = { onsets?: unknown; peaks?: unknown; source?: unknown };

const EMPTY: number[] = [];

export function useDubOnsets(jobId: string | null, active: boolean) {
  const [result, setResult] = useState<{
    jobId: string | null;
    onsets: number[];
    peaks: number[];
    source: string | null;
  }>({ jobId: null, onsets: EMPTY, peaks: EMPTY, source: null });

  useEffect(() => {
    if (!jobId || !active) return;
    const controller = new AbortController();
    apiJson<OnsetResponse>(`/dub/onsets/${encodeURIComponent(jobId)}`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (controller.signal.aborted) return;
        const onsets = Array.isArray(response.onsets)
          ? response.onsets
              .map(Number)
              .filter((value) => Number.isFinite(value) && value >= 0)
              .sort((a, b) => a - b)
          : EMPTY;
        const peaks = Array.isArray(response.peaks)
          ? response.peaks.map(Number).filter((value) => Number.isFinite(value) && value >= 0)
          : EMPTY;
        setResult({
          jobId,
          onsets,
          peaks,
          source: typeof response.source === 'string' ? response.source : null,
        });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && !(error instanceof DOMException && error.name === 'AbortError'))
          setResult({ jobId, onsets: EMPTY, peaks: EMPTY, source: null });
      });
    return () => controller.abort();
  }, [active, jobId]);

  return result.jobId === jobId && active
    ? { onsets: result.onsets, peaks: result.peaks, source: result.source }
    : { onsets: EMPTY, peaks: EMPTY, source: null };
}
