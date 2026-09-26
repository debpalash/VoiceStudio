import React from 'react';
import { CheckCircle, ArrowRight, X, Sparkles, Languages, Mic, AlertTriangle } from 'lucide-react';
import { Button } from '../ui';
import { useTranslation } from 'react-i18next';

/**
 * Phase 4.3 — between-stage checkpoint banner.
 *
 * The dub pipeline has three natural review points (post-ASR, post-translate,
 * post-generate). Each one is a chance for the user to spot a mistake before
 * it compounds into the next stage. This banner makes that review window
 * explicit instead of silently leaving the user on the segment editor with
 * no cue about what to do next.
 *
 * Render it above the segment table. Pass `onContinue` to advance the
 * pipeline directly from the banner's CTA (translate, generate, etc).
 */

const STAGE_ICONS = {
  asr: { icon: Mic, accent: '#b8bb26', ctaIcon: Languages },
  translate: { icon: Languages, accent: '#83a598', ctaIcon: Sparkles },
  done: { icon: CheckCircle, accent: '#8ec07c' },
};

const STAGE_KEYS = {
  asr: { title: 'checkpoint.asr_title', cta: 'checkpoint.asr_cta', hint: 'checkpoint.asr_hint' },
  translate: {
    title: 'checkpoint.translate_title',
    cta: 'checkpoint.translate_cta',
    hint: 'checkpoint.translate_hint',
  },
  done: { title: 'checkpoint.done_title', cta: null, hint: 'checkpoint.done_hint' },
};

export default function CheckpointBanner({
  stage,
  count,
  onContinue,
  onDismiss,
  continueLoading,
  timingWarnings = 0,
}) {
  const { t } = useTranslation();
  const icons = STAGE_ICONS[stage];
  const keys = STAGE_KEYS[stage];
  if (!icons || !keys) return null;

  const Icon = icons.icon;
  const CtaIcon = icons.ctaIcon;

  return (
    <div
      className="checkpoint-banner ckpt-banner"
      style={{ borderLeft: `2px solid ${icons.accent}` }}
      role="status"
    >
      <Icon size={14} color={icons.accent} className="ckpt-icon" />
      <div className="ckpt-body">
        <div className="ckpt-head">
          <span className="ckpt-title">{t(keys.title)}</span>
          {typeof count === 'number' && (
            <span className="ckpt-count">{t('checkpoint.segment', { count })}</span>
          )}
        </div>
        <span className="ckpt-hint">{t(keys.hint)}</span>
        {stage === 'done' && timingWarnings > 0 && (
          <span className="flex items-center gap-2 text-xs text-[var(--color-warn)]">
            <AlertTriangle size={14} aria-hidden="true" />
            {t('checkpoint.timing_review', { count: timingWarnings })}
          </span>
        )}
      </div>
      {keys.cta && onContinue && (
        <Button
          variant="subtle"
          size="sm"
          onClick={onContinue}
          loading={continueLoading}
          leading={CtaIcon ? <CtaIcon size={10} /> : null}
          trailing={<ArrowRight size={10} />}
        >
          {t(keys.cta)}
        </Button>
      )}
      {onDismiss && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onDismiss}
          title={t('checkpoint.dismiss_title')}
          iconSize="sm"
        >
          <X size={10} />
        </Button>
      )}
    </div>
  );
}
