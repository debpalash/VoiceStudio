import { TimerIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import type { Draft } from './longform-session';

type SpeedLine = { speed?: number | null };

/** Number of lines that carry their own positive speed. */
export function linesWithOwnSpeed(lines: SpeedLine[]): number {
  return lines.reduce((n, l) => (typeof l.speed === 'number' && l.speed > 0 ? n + 1 : n), 0);
}

const SPEED_RANGE = { min: 0.5, max: 2, step: 0.05 };

export function StorySpeed({
  draft,
  disabled,
  onChange,
}: {
  draft: Draft;
  disabled: boolean;
  onChange: (patch: Partial<Draft>) => void;
}) {
  const { t } = useTranslation();
  const overridden = linesWithOwnSpeed(draft.lines);
  const clearLineSpeeds = () =>
    onChange({ lines: draft.lines.map((l) => ({ ...l, speed: null })) });

  return (
    <fieldset className="m-0 grid gap-1.5 border-0 p-0" disabled={disabled}>
      <div className="flex items-baseline gap-2 text-xs font-medium text-muted-foreground">
        <TimerIcon className="size-4 self-center" aria-hidden="true" />
        <legend className="float-left p-0">{t('stories.global_speed')}</legend>
        <output className="ml-auto font-mono tabular-nums text-foreground">
          {`${draft.globalSpeed.toFixed(2)}×`}
        </output>
      </div>
      <input
        type="range"
        {...SPEED_RANGE}
        className="w-full accent-primary"
        aria-label={t('stories.global_speed')}
        title={t('stories.global_speed_hint')}
        value={draft.globalSpeed}
        onChange={(e) => onChange({ globalSpeed: Number(e.currentTarget.value) })}
      />
      <p className="m-0 text-xs text-muted-foreground">{t('stories.speedHint')}</p>
      {overridden > 0 ? (
        <p className="m-0 flex items-center gap-2 text-xs text-muted-foreground">
          <span className="min-w-0 flex-1">
            {t('stories.global_speed_overrides', { count: overridden })}
          </span>
          <Button size="xs" variant="ghost" onClick={clearLineSpeeds}>
            {t('stories.global_speed_apply')}
          </Button>
        </p>
      ) : null}
    </fieldset>
  );
}
