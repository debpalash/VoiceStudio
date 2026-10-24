import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

export function BackgroundVolumeControl({ value, disabled, onCommit }: {
  value: number; disabled: boolean; onCommit: (gain: number) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [percent, setPercent] = useState(Math.round(value * 100));
  useEffect(() => setPercent(Math.round(value * 100)), [value, disabled]);
  const commit = () => {
    if (!disabled && percent !== Math.round(value * 100)) onCommit(percent / 100);
  };
  return (
    <div className="space-y-2 text-xs">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={id}>{t('exportModal.background_volume')}</label>
        <output htmlFor={id}>{percent}%</output>
      </div>
      <input id={id} type="range" min={0} max={400} step={1}
        value={percent} disabled={disabled} aria-valuetext={`${percent}%`}
        className="w-full accent-primary disabled:opacity-50"
        onChange={(event) => setPercent(Number(event.target.value))}
        onPointerUp={commit} onKeyUp={commit} onBlur={commit} />
    </div>
  );
}
