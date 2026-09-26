/**
 * useTimelineOnsets — lazy fetch of the speech-onset list for the timeline
 * editor's snap-to-onset ticks (#280, item 3).
 *
 * Fetches GET /dub/onsets/{job_id} when the editor becomes active and again
 * after a re-transcription (the dub step leaves and re-enters the editing
 * state, toggling `active`). The backend caches per job, so refetches are
 * cheap. Failures degrade silently — the editor simply has no onset ticks.
 */
import { useEffect, useState } from 'react';
import { apiJson } from '../api/client';

const EMPTY = [];

export default function useTimelineOnsets(jobId, active = true) {
  // Keyed by job id so a stale job's onsets are never served for the next
  // one — deriving from the key avoids a synchronous reset-setState in the
  // effect body.
  const [data, setData] = useState({ key: null, onsets: EMPTY, source: null });

  useEffect(() => {
    if (!jobId || !active) return undefined;
    let cancelled = false;
    apiJson(`/dub/onsets/${jobId}`)
      .then((res) => {
        if (cancelled) return;
        setData({
          key: jobId,
          onsets: Array.isArray(res?.onsets) ? res.onsets : EMPTY,
          source: res?.source || null,
        });
      })
      .catch(() => {
        if (!cancelled) setData({ key: jobId, onsets: EMPTY, source: null });
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, active]);

  const fresh = active && data.key === jobId;
  return {
    onsets: fresh ? data.onsets : EMPTY,
    source: fresh ? data.source : null,
  };
}
