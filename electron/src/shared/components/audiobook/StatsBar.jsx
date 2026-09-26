import React, { useMemo } from 'react';
import { scriptStats, formatRuntimeClock } from '../../utils/audiobookScript';

/**
 * Live script stats (#1217) — "N chapters · ~M words · ~H:MM est. runtime".
 * Memoized, updates as the user types. Pure `scriptStats` does the counting; the
 * runtime is a clock string (H:MM ≥1h, else M:SS) at ~155 wpm narration pace.
 */
export default function StatsBar({ t, text }) {
  const { chapters, words, runtimeSec } = useMemo(() => scriptStats(text), [text]);
  if (!text || !text.trim()) return null;
  return (
    <p className="muted text-[0.72rem] text-fg-muted m-0" aria-live="polite">
      {t('audiobook.stats', {
        chapters,
        words: words.toLocaleString(),
        runtime: formatRuntimeClock(runtimeSec),
      })}
    </p>
  );
}
