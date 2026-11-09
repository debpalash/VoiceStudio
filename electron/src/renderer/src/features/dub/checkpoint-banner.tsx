import {
  AlertTriangleIcon,
  ArrowRightIcon,
  CheckCircle2Icon,
  LanguagesIcon,
  MicIcon,
  SparklesIcon,
  XIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

export type CheckpointStage = 'asr' | 'translate' | 'done';

const stages = {
  asr: {
    title: 'checkpoint.asr_title',
    hint: 'checkpoint.asr_hint',
    cta: 'checkpoint.asr_cta',
    Icon: MicIcon,
    ActionIcon: LanguagesIcon,
  },
  translate: {
    title: 'checkpoint.translate_title',
    hint: 'checkpoint.translate_hint',
    cta: 'checkpoint.translate_cta',
    Icon: LanguagesIcon,
    ActionIcon: SparklesIcon,
  },
  done: {
    title: 'checkpoint.done_title',
    hint: 'checkpoint.done_hint',
    cta: null,
    Icon: CheckCircle2Icon,
    ActionIcon: null,
  },
} as const;

export function CheckpointBanner({
  stage,
  timingWarnings,
  disabled,
  onContinue,
  onDismiss,
}: {
  stage: CheckpointStage;
  timingWarnings: number;
  disabled?: boolean;
  onContinue?: () => void;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  const config = stages[stage];
  const Icon = config.Icon;
  const ActionIcon = config.ActionIcon;

  return (
    <div
      role="status"
      // One slim row: the stage, its next step and timing review; the hint is the tooltip.
      className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 bg-primary/5 px-2.5 py-1.5"
      title={t(config.hint)}
    >
      <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
        <Icon className="size-3.5" aria-hidden="true" />
      </span>
      <p className="min-w-0 flex-1 truncate text-sm font-medium">
        {t(config.title)}
        <span className="sr-only"> {t(config.hint)}</span>
      </p>
      {stage === 'done' && timingWarnings > 0 && (
        <p className="flex shrink-0 items-center gap-1.5 text-xs text-warning">
          <AlertTriangleIcon className="size-3.5" aria-hidden="true" />
          {t('checkpoint.timing_review', { count: timingWarnings })}
        </p>
      )}
      {config.cta && onContinue && (
        <Button size="sm" variant="secondary" disabled={disabled} onClick={onContinue}>
          {ActionIcon && <ActionIcon />}
          {t(config.cta)}
          <ArrowRightIcon />
        </Button>
      )}
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label={t('checkpoint.dismiss_title')}
        title={t('checkpoint.dismiss_title')}
        onClick={onDismiss}
      >
        <XIcon />
      </Button>
    </div>
  );
}
