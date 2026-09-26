import { useEffect, useMemo, useState, useId } from 'react';
import { Check, AlertCircle, X, ChevronDown, ChevronUp } from 'lucide-react';
import { Badge } from '../../ui';
import DubFailureNotice from './DubFailureNotice';
import TrackManager from './TrackManager';

// How long a translate/pipeline error banner lingers before it self-clears.
// Long enough to read a short message; the × and corrective-action clears are
// the primary escape hatches — this is the belt-and-suspenders timeout.
const ERROR_AUTOCLEAR_MS = 12000;

export default function DubFooter({
  t,
  dubStep,
  dubTracks,
  dubLangCode,
  incrementalPlan,
  dubError,
  dubFailure,
  onDismissError,
  exportTracks,
  setExportTracks,
  dubSegments,
  translateQuality,
  hideTracks = false,
}) {
  const [errorCollapsed, setErrorCollapsed] = useState(false);
  const [dismissedError, setDismissedError] = useState(null);
  const errorDetailsId = useId();
  // Dismiss one occurrence, so retrying can report the same failure again.
  useEffect(() => {
    setDismissedError(null);
  }, [dubError]);
  // Auto-clear the error banner after a grace period so it can't get stuck
  // forever (issue: "TRANSLATION FAILED banner never goes away"). Skipped
  // while generating/stopping, where the banner accumulates live per-segment
  // errors the user needs to keep reading until the run ends.
  const canAutoClear =
    !!dubError && !!onDismissError && dubStep !== 'generating' && dubStep !== 'stopping';
  const availableTracks = useMemo(
    () => [
      { code: 'original', label: t('dub.original_track'), kind: 'original' },
      ...dubTracks.map((code) => ({ code, label: code.toUpperCase(), kind: 'dub' })),
    ],
    [dubTracks, t],
  );
  useEffect(() => {
    if (!canAutoClear) return undefined;
    const id = setTimeout(() => onDismissError(), ERROR_AUTOCLEAR_MS);
    return () => clearTimeout(id);
  }, [canAutoClear, dubError, onDismissError]);

  return (
    <div className="min-w-0 max-w-full px-[var(--space-3)] py-[4px] shrink-0 bg-[var(--chrome-bg)] border border-transparent">
      {!hideTracks && dubStep === 'done' && (
        <div className="mb-[var(--space-2)]">
          <Badge tone="success">
            <Check size={11} /> {t('dub.tracks_done', { tracks: dubTracks.join(', ') })}
          </Badge>
          {incrementalPlan && incrementalPlan.stale?.length > 0 && (
            <Badge tone="warn" className="ml-[6px]">
              {t('dub.segments_changed', { count: incrementalPlan.stale.length })}
            </Badge>
          )}
          {incrementalPlan &&
            incrementalPlan.stale?.length === 0 &&
            incrementalPlan.fresh?.length > 0 && (
              <Badge tone="neutral" className="ml-[6px]">
                {t('dub.all_up_to_date', { count: incrementalPlan.fresh.length })}
              </Badge>
            )}
        </div>
      )}
      {dubError && dubError !== dismissedError && (
        <div className="mb-[var(--space-2)] min-w-0 max-w-full" data-testid="dub-error-notice">
          <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2 rounded-lg bg-[var(--chrome-hover-bg)] p-3">
            <AlertCircle
              size={16}
              className="shrink-0 text-[var(--color-danger)] mt-0.5"
              aria-hidden="true"
            />
            <button
              type="button"
              aria-expanded={!errorCollapsed}
              aria-controls={errorDetailsId}
              onClick={() => setErrorCollapsed((value) => !value)}
              className="flex min-w-0 flex-1 items-center gap-2 min-h-9 text-left text-sm text-[var(--chrome-fg)] border-0 bg-transparent cursor-pointer"
            >
              {t('common.error')}
              {errorCollapsed ? (
                <ChevronDown size={16} aria-hidden="true" />
              ) : (
                <ChevronUp size={16} aria-hidden="true" />
              )}
            </button>
            <button
              type="button"
              onClick={() => {
                setDismissedError(dubError);
                onDismissError?.();
              }}
              aria-label={t('dub.dismiss_error')}
              className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md px-2 text-sm text-[var(--chrome-fg-muted)] hover:bg-[var(--chrome-hover-bg)] border-0 bg-transparent cursor-pointer"
            >
              <X size={16} aria-hidden="true" />
              {t('dub.dismiss_error')}
            </button>
            {!errorCollapsed && (
              <div id={errorDetailsId} className="basis-full min-w-0">
                <div
                  className="min-w-0 flex-1 max-h-28 overflow-y-auto whitespace-pre-wrap [overflow-wrap:anywhere] text-sm leading-relaxed text-[var(--chrome-fg)]"
                  data-testid="dub-error-message"
                >
                  {dubError}
                </div>
                <DubFailureNotice failure={dubFailure} />
              </div>
            )}
          </div>
        </div>
      )}
      {/* Output options + Timing moved to the top of the right (transcript) section. */}
      {!hideTracks && dubTracks.length > 0 && (
        <div className="mb-[2px] px-[var(--space-3)] py-[3px]">
          <TrackManager
            t={t}
            tracks={availableTracks}
            selection={exportTracks}
            setSelection={setExportTracks}
            primaryCode={dubLangCode}
          />
        </div>
      )}
      {(() => {
        // Pre-generation compression warning. Predicted by the
        // translate response (see services/speech_rate.rate_ratio
        // + dub_translate._maybe_cinematic), populated whenever
        // segments carry a slot_seconds and translated text.
        // Surfaces here so the user can act (re-translate in
        // Cinematic, edit text, allow longer slots) before
        // committing to a full Generate Dub run.
        const hot = dubSegments.filter((s) => (s.rate_ratio || 0) > 1.3);
        if (hot.length === 0 || !dubSegments.length) return null;
        const pctHot = Math.round((hot.length / dubSegments.length) * 100);
        if (pctHot < 10) return null;
        const worst = hot.reduce((a, b) => (a.rate_ratio > b.rate_ratio ? a : b));
        return (
          <div
            className="flex items-start gap-[8px] px-[10px] py-[6px] my-[4px] bg-[color-mix(in_srgb,#fabd2f_12%,transparent)] border border-transparent border-l-2 border-l-transparent rounded-[var(--chrome-radius-pill)] text-[0.72rem] text-[var(--chrome-fg)] leading-[1.35]"
            role="status"
          >
            <span className="text-[#fabd2f] text-[0.9rem] leading-none shrink-0">⚠</span>
            <span className="flex-1">
              <strong className="text-[#fabd2f] font-semibold">
                {hot.length} of {dubSegments.length}
              </strong>{' '}
              segments need {'>'}1.3× compression (worst:{' '}
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                {worst.rate_ratio.toFixed(2)}×
              </span>
              ). Output will be intelligible (pitch-preserving stretch) but stressed —
              {translateQuality === 'fast'
                ? ' switch to Cinematic and Re-translate'
                : ' shorten the worst segments'}{' '}
              for cleaner audio.
            </span>
          </div>
        );
      })()}
      {/* Generate / Export / Stop actions moved to the header bar (dub-head__primary). */}
    </div>
  );
}
