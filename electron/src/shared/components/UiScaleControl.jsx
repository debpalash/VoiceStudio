import { Minus, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '../store';
import { Button } from '../ui';

const MIN_SCALE = 0.6;
const MAX_SCALE = 1.75;
const SCALE_STEP = 0.05;

function clampScale(value) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.round(value / SCALE_STEP) * SCALE_STEP));
}

/** Compact escape hatch for startup screens whose suggested scale is hard to read. */
export default function UiScaleControl() {
  const { t } = useTranslation();
  const uiScale = useAppStore((s) => s.uiScale);
  const setUiScale = useAppStore((s) => s.setUiScale);
  const setUiScalePreviewed = useAppStore((s) => s.setUiScalePreviewed);
  const label = t('settings.ui_scale');
  const percent = Math.round(uiScale * 100);
  const adjustScale = (nextScale) => {
    setUiScale(nextScale);
    setUiScalePreviewed(true);
  };

  return (
    <div className="ui-scale-control" aria-label={label} data-testid="ui-scale-control">
      <span className="ui-scale-control__label">{label}</span>
      <Button
        variant="ghost"
        size="sm"
        iconSize="sm"
        onClick={() => adjustScale(clampScale(uiScale - SCALE_STEP))}
        disabled={uiScale <= MIN_SCALE}
        aria-label={`${label} −`}
        title={`${label} −`}
        data-testid="ui-scale-decrease"
      >
        <Minus size={12} aria-hidden="true" />
      </Button>
      <span className="ui-scale-control__value" aria-live="polite">
        {percent}%
      </span>
      <Button
        variant="ghost"
        size="sm"
        iconSize="sm"
        onClick={() => adjustScale(clampScale(uiScale + SCALE_STEP))}
        disabled={uiScale >= MAX_SCALE}
        aria-label={`${label} +`}
        title={`${label} +`}
        data-testid="ui-scale-increase"
      >
        <Plus size={12} aria-hidden="true" />
      </Button>
    </div>
  );
}
