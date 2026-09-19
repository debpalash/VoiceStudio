import { GaugeIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import type { Draft } from './longform-session';

/** Lines whose own speed wins over the book-wide one. */
export const linesWithOwnSpeed = (lines: Draft['lines']) =>
  lines.filter((line) => typeof line.speed === 'number' && line.speed > 0).length;

/**
 * Book-wide reading speed, in the setup card next to voice and language. It
 * used to live at the bottom of the collapsed Cast card, where a narrator with
 * no cast never looks — so people set the speed line by line instead. Lines
 * that carry their own speed are counted, with one click to hand them back.
 */
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
  const overrides = linesWithOwnSpeed(draft.lines);
  return (
    <div className="space-y-2">
      <h2 className="flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground">
        <span className="flex items-center gap-2">
          <GaugeIcon className="size-4" aria-hidden="true" />
          {t('stories.global_speed')}
        </span>
        <span className="tabular-nums text-foreground/90">{draft.globalSpeed.toFixed(2)}×</span>
      </h2>
      <input
        aria-label={t('stories.global_speed')}
        title={t('stories.global_speed_hint')}
        type="range"
        min="0.5"
        max="2"
        step="0.05"
        className="w-full accent-primary"
        disabled={disabled}
        value={draft.globalSpeed}
        onChange={(e) => onChange({ globalSpeed: Number(e.target.value) })}
      />
      <p className="text-xs text-muted-foreground">{t('stories.global_speed_hint')}</p>
      {overrides > 0 && (
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{t('stories.global_speed_overrides', { count: overrides })}</span>
          <Button
            size="xs"
            variant="ghost"
            disabled={disabled}
            onClick={() =>
              onChange({ lines: draft.lines.map((line) => ({ ...line, speed: null })) })
            }
          >
            {t('stories.global_speed_apply')}
          </Button>
        </div>
      )}
    </div>
  );
}
