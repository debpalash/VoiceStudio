import { useMemo } from 'react';
import { GaugeIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useEngines } from '@/hooks/use-engines';
import { belowBestQuality, bestSamplingSteps, effectiveSamplingSteps } from '@/lib/audio/quality';
import {
  estimateRender,
  fasterPlan,
  isSlowRender,
  readRenderSamples,
  speedKey,
  type FasterPlan,
} from '@/lib/audio/render-speed';
import { useCloneSetting } from '@/lib/store/clone-settings';
import {
  applyFasterSettings,
  dontAskAgain,
  keepBestQuality,
  restoreBestQuality,
  useQualityOffer,
} from '@/lib/store/quality-offer';
import { cn } from '@/lib/utils';

/** Faster settings accepted from an offer and still in effect. */
interface AppliedChange {
  from: number;
  to: number;
}

export type QualityOfferView =
  | {
      kind: 'before' | 'after';
      engineId: string;
      seconds: number;
      atBest: boolean;
      plan: FasterPlan;
      applied: AppliedChange | null;
    }
  | ({ kind: 'applied' } & AppliedChange);

/** What, if anything, to say about speed beside the Generate button. Never changes settings. */
export function useQualityOfferView(text: string, isGenerating: boolean): QualityOfferView | null {
  const { activeTts } = useEngines();
  const offer = useQualityOffer();
  const steps = useCloneSetting('steps');
  const engineId = activeTts?.id ?? null;
  const key = engineId ? speedKey(engineId, activeTts?.effective_device) : null;
  // `version` changes whenever a take is measured.
  const samples = useMemo(
    () => (key ? readRenderSamples(key) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, offer.version],
  );
  if (!engineId || isGenerating) return null;
  const current = effectiveSamplingSteps(steps, engineId);
  const atBest = current >= (bestSamplingSteps(engineId) ?? current);
  const applied =
    offer.applied?.engineId === engineId && belowBestQuality(steps, engineId)
      ? { from: offer.applied.from, to: current }
      : null;
  const ask = (): QualityOfferView | null => {
    if (offer.muted.includes(engineId) || offer.dismissed.includes(engineId)) return null;
    const last = offer.lastTake?.key === key ? offer.lastTake : null;
    if (last && isSlowRender(last)) {
      const plan = fasterPlan(engineId, current, samples, last.chars);
      if (plan)
        return { kind: 'after', engineId, seconds: last.renderSeconds, atBest, plan, applied };
    }
    const chars = text.trim().length;
    const estimate = estimateRender(samples, chars, current);
    if (!estimate || !isSlowRender(estimate)) return null;
    const plan = fasterPlan(engineId, current, samples, chars);
    if (!plan) return null;
    return { kind: 'before', engineId, seconds: estimate.renderSeconds, atBest, plan, applied };
  };
  // Accepted faster settings can still prove slow; offer the next step then,
  // with Restore best quality alongside.
  return ask() ?? (applied ? { kind: 'applied', ...applied } : null);
}

/** "45 s" / "2 min", rounded the way a wait is felt, in the UI language. */
export function formatWait(seconds: number, locale: string): string {
  const minutes = seconds >= 90;
  const value = minutes
    ? Math.round(seconds / 60)
    : seconds >= 20
      ? Math.round(seconds / 5) * 5
      : Math.max(1, Math.round(seconds));
  try {
    return new Intl.NumberFormat(locale, {
      style: 'unit',
      unit: minutes ? 'minute' : 'second',
      unitDisplay: 'short',
    }).format(value);
  } catch {
    return `${value} ${minutes ? 'min' : 's'}`;
  }
}

/** Inline note by the Generate button: offers faster settings, never applies them alone. */
export function QualityOffer({
  text,
  isGenerating,
  className,
}: {
  text: string;
  isGenerating: boolean;
  className?: string;
}) {
  const { t, i18n } = useTranslation();
  const view = useQualityOfferView(text, isGenerating);
  if (!view) return null;
  const wait = (seconds: number) => formatWait(seconds, i18n.language);
  const change = (from: number, to: number) => t('cloneQuality.change', { from, to });

  if (view.kind === 'applied')
    return (
      <div
        role="status"
        className={cn(
          'flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground',
          className,
        )}
      >
        <GaugeIcon aria-hidden="true" className="size-3.5" />
        <span>{change(view.from, view.to)}</span>
        <Button variant="ghost" size="xs" onClick={restoreBestQuality}>
          {t('cloneQuality.restore')}
        </Button>
      </div>
    );

  const { plan, engineId } = view;
  const summary =
    view.kind === 'after'
      ? t('cloneQuality.slowTake', { time: wait(view.seconds) })
      : t(view.atBest ? 'cloneQuality.estimateBest' : 'cloneQuality.estimate', {
          time: wait(view.seconds),
        });
  return (
    <div
      role="status"
      className={cn(
        'flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground',
        className,
      )}
    >
      <GaugeIcon aria-hidden="true" className="size-3.5" />
      <span>
        {summary} · {t('cloneQuality.fasterOption', { time: wait(plan.renderSeconds) })}
      </span>
      <span className="tabular-nums opacity-80">{change(plan.from, plan.to)}</span>
      <span className="flex flex-wrap items-center gap-1">
        <Button
          variant="secondary"
          size="xs"
          onClick={() => applyFasterSettings(engineId, plan.from, plan.to)}
        >
          {t('cloneQuality.useFaster')}
        </Button>
        <Button variant="ghost" size="xs" onClick={() => keepBestQuality(engineId)}>
          {t(view.atBest ? 'cloneQuality.keepBest' : 'cloneQuality.keepCurrent')}
        </Button>
        <Button variant="ghost" size="xs" onClick={() => dontAskAgain(engineId)}>
          {t('cloneQuality.dontAsk')}
        </Button>
        {view.applied && (
          <Button variant="ghost" size="xs" onClick={restoreBestQuality}>
            {t('cloneQuality.restore')}
          </Button>
        )}
      </span>
    </div>
  );
}
