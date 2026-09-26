import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import Pip from './Pip';
import { BUNDLED_PROGRESS, loadDonationProgress, progressPct, isGoalMet } from '../../api/donation';

/**
 * GoalBar — the "Support VoiceStudio development" progress meter.
 *
 * Two variants via the `mini` prop:
 *   - page (default): full bar with caption + Pip perched on the fill.
 *   - mini: a slim inline bar for the postcard toast (no Pip, terse caption).
 *
 * Data: starts from the bundled snapshot (instant, offline-safe), then
 * best-effort swaps in a fresher fetched copy. Caller may also pass `progress`
 * directly (e.g. the postcard reuses the page's already-loaded value) to skip
 * the fetch entirely.
 *
 * The fill width is driven by the `--goal-pct` CSS var (0..1) so the animation
 * is pure CSS. ONE shimmer pass on mount; reduced-motion disables it.
 */
function formatMoney(amount, currency) {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: amount % 1 === 0 ? 0 : 2,
    }).format(amount);
  } catch {
    return `$${Math.round(amount)}`;
  }
}

export default function GoalBar({ mini = false, progress: injected = null, className = '' }) {
  const { t } = useTranslation();
  // When the caller passes `progress`, that prop is the single source of truth
  // (no fetch, no local state). Otherwise we best-effort fetch a fresher copy,
  // starting from the bundled snapshot so the bar is instant + offline-safe.
  const [fetched, setFetched] = useState(BUNDLED_PROGRESS);

  useEffect(() => {
    if (injected) return undefined; // caller owns the data — skip fetch
    let alive = true;
    loadDonationProgress().then((p) => {
      if (alive) setFetched(p);
    });
    return () => {
      alive = false;
    };
  }, [injected]);

  const data = injected || fetched;

  const pct = useMemo(() => progressPct(data), [data]);
  const met = useMemo(() => isGoalMet(data), [data]);
  const pctLabel = Math.round(pct * 100);

  const raisedStr = formatMoney(data.raised, data.currency);
  const goalStr = formatMoney(data.goal, data.currency);

  return (
    <div
      className={[
        'goal [--goal-accent:var(--chrome-accent)] flex flex-col gap-[var(--space-3,6px)] w-full',
        mini ? 'goal--mini' : 'goal--page',
        met ? 'goal--met' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      style={{ '--goal-pct': pct }}
    >
      {!mini && (
        <div className="goal__head flex items-baseline justify-between gap-[var(--space-4,8px)]">
          <span className="goal__title font-serif text-[1rem] font-medium tracking-[-0.01em] text-[var(--chrome-fg)]">
            {t('donate.goal.title', { defaultValue: 'Support VoiceStudio development' })}
          </span>
          <span className="goal__pct font-mono text-[0.78rem] font-semibold text-[var(--goal-accent)] [font-variant-numeric:tabular-nums]">
            {pctLabel}%
          </span>
        </div>
      )}

      <div
        className="goal__track relative h-[12px] rounded-[999px] bg-[color-mix(in_srgb,var(--chrome-fg)_8%,transparent)] [border:1px_solid_var(--chrome-border)] overflow-visible isolate"
        role="progressbar"
        aria-valuenow={pctLabel}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={t('donate.goal.aria', {
          defaultValue: '{{raised}} of {{goal}} monthly goal',
          raised: raisedStr,
          goal: goalStr,
        })}
      >
        <span className="goal__fill" aria-hidden="true">
          <span className="goal__shimmer" aria-hidden="true" />
        </span>
        {!mini && (
          <span className="goal__pip" aria-hidden="true">
            <Pip size={24} />
          </span>
        )}
      </div>

      <div className="goal__caption flex items-baseline justify-between gap-[var(--space-4,8px)] font-sans text-[0.72rem] text-[var(--chrome-fg-muted)]">
        {met ? (
          <span className="goal__caption-met text-[var(--chrome-accent)] font-semibold">
            {t('donate.goal.met', {
              defaultValue: '🎉 Goal met — {{raised}} raised. Thank you!',
              raised: raisedStr,
            })}
          </span>
        ) : (
          <>
            <span className="goal__amounts">
              <strong>{raisedStr}</strong> {t('donate.goal.of', { defaultValue: 'of' })} {goalStr}{' '}
              {t('donate.goal.per_month', { defaultValue: '/ month' })}
            </span>
            {!mini && (
              <span className="goal__remaining font-mono text-[0.68rem] text-[var(--chrome-fg-dim)] whitespace-nowrap">
                {t('donate.goal.remaining', {
                  defaultValue: '{{amount}} to go',
                  amount: formatMoney(Math.max(0, data.goal - data.raised), data.currency),
                })}
              </span>
            )}
          </>
        )}
      </div>
    </div>
  );
}
